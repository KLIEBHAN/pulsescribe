//! End-to-end integration tests for `DaemonRuntime` covering hotkeys, clipboard/paste delivery,
//! `HistoryStore` persistence, File-IPC wizard round-trips, watchdog timeouts, and config reload.

use std::fs;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use pulsescribe_core::transcript_delivery::{LAST_TRANSCRIPT_COPIED, PASTED};
use pulsescribe_core::{
    AppState, DaemonErrorCode, DaemonStatusError, HistoryStore, PreferencesStore,
    TranscriptClipboardBackend,
};
use pulsescribe_daemon::{
    ActiveSession, DaemonConfig, DaemonEvent, DaemonPlatform, DaemonRuntime, DaemonRuntimeOptions,
    DaemonTimings, DictationBackend, IpcClient, IpcCommandKind, IpcPaths, IpcStatus, PipelineEvent,
    RELOAD_SIGNAL_FILENAME,
};
use pulsescribe_platform::{
    AppDetector, DefaultPermissionsChecker, HotkeyBackend, HotkeyBinding, HotkeyCallback,
    HotkeyEvent, HotkeyRegistration, HotkeySource, PlatformError, PlatformKind, SoundName,
    SoundPlayer,
};
use tokio::sync::{mpsc, oneshot};

static TEST_DIR_SEQ: AtomicU64 = AtomicU64::new(0);

struct TempDir(PathBuf);

impl TempDir {
    fn new(tag: &str) -> Self {
        let seq = TEST_DIR_SEQ.fetch_add(1, Ordering::Relaxed);
        let nanos = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let dir = std::env::temp_dir().join(format!(
            "pulsescribe-daemon-e2e-{tag}-{}-{nanos}-{seq}",
            std::process::id()
        ));
        fs::create_dir_all(&dir).unwrap();
        Self(dir)
    }

    fn path(&self) -> &Path {
        &self.0
    }
}

impl Drop for TempDir {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

#[derive(Default)]
struct RecordingClipboard {
    pasted: Vec<String>,
    copied: Vec<String>,
}

impl TranscriptClipboardBackend for RecordingClipboard {
    fn paste_transcript(&mut self, text: &str) -> bool {
        self.pasted.push(text.to_owned());
        true
    }

    fn copy_transcript(&mut self, text: &str) -> bool {
        self.copied.push(text.to_owned());
        true
    }
}

struct StubAppDetector {
    process_name: String,
    display_label: String,
}

impl StubAppDetector {
    fn new(process_name: &str, display_label: &str) -> Self {
        Self {
            process_name: process_name.to_owned(),
            display_label: display_label.to_owned(),
        }
    }
}

impl AppDetector for StubAppDetector {
    fn frontmost_app(&self) -> Option<String> {
        Some(self.process_name.clone())
    }

    fn frontmost_app_label(&self) -> Option<String> {
        Some(self.display_label.clone())
    }
}

#[derive(Default)]
struct StubSoundPlayer(Mutex<Vec<SoundName>>);

impl SoundPlayer for StubSoundPlayer {
    fn play(&self, sound: SoundName) {
        self.0.lock().unwrap().push(sound);
    }
}

#[derive(Default)]
struct StubHotkeyBackend {
    bindings: Mutex<Vec<HotkeyBinding>>,
    callback: Mutex<Option<HotkeyCallback>>,
}

impl StubHotkeyBackend {
    fn emit(&self, event: HotkeyEvent) {
        if let Some(cb) = self.callback.lock().unwrap().clone() {
            cb(event);
        }
    }

    fn registered_bindings(&self) -> Vec<HotkeyBinding> {
        self.bindings.lock().unwrap().clone()
    }
}

struct StubRegistration;

impl HotkeyRegistration for StubRegistration {
    fn unregister(&mut self) -> Result<(), PlatformError> {
        Ok(())
    }
}

impl HotkeyBackend for StubHotkeyBackend {
    fn register(
        &self,
        binding: &HotkeyBinding,
        on_event: HotkeyCallback,
    ) -> Result<Box<dyn HotkeyRegistration>, PlatformError> {
        self.bindings.lock().unwrap().push(binding.clone());
        *self.callback.lock().unwrap() = Some(on_event);
        Ok(Box::new(StubRegistration))
    }
}

enum FakeBackendBehavior {
    ReturnText(String),
    HangAfterStop,
    FailMidCapture,
}

struct FakeDictationBackend {
    behavior: FakeBackendBehavior,
    observed_context_app: Arc<Mutex<Option<String>>>,
    received_cancel: Arc<AtomicBool>,
}

impl FakeDictationBackend {
    fn new(behavior: FakeBackendBehavior) -> Self {
        Self {
            behavior,
            observed_context_app: Arc::new(Mutex::new(None)),
            received_cancel: Arc::new(AtomicBool::new(false)),
        }
    }
}

impl DictationBackend for FakeDictationBackend {
    fn start_session(
        &self,
        run_id: u64,
        _config: DaemonConfig,
        _is_test: bool,
        app_detector: Arc<dyn AppDetector>,
        event_tx: mpsc::UnboundedSender<(u64, PipelineEvent)>,
    ) -> ActiveSession {
        let (stop_tx, stop_rx) = oneshot::channel();
        let (cancel_tx, mut cancel_rx) = oneshot::channel();
        let observed_context_app = Arc::clone(&self.observed_context_app);
        let received_cancel = Arc::clone(&self.received_cancel);
        let mode = match &self.behavior {
            FakeBackendBehavior::ReturnText(text) => Some(text.clone()),
            FakeBackendBehavior::HangAfterStop => None,
            FakeBackendBehavior::FailMidCapture => Some(String::new()),
        };
        let fail_mid = matches!(self.behavior, FakeBackendBehavior::FailMidCapture);

        tokio::spawn(async move {
            *observed_context_app.lock().unwrap() = app_detector.frontmost_app();
            let _ = event_tx.send((run_id, PipelineEvent::CaptureStarted));
            let _ = event_tx.send((run_id, PipelineEvent::AudioLevel(0.42)));
            let _ = event_tx.send((run_id, PipelineEvent::SpeechDetected));

            if fail_mid {
                let _ = event_tx.send((
                    run_id,
                    PipelineEvent::Failed(DaemonStatusError::new(
                        DaemonErrorCode::Connection,
                        Some("stream dropped".into()),
                    )),
                ));
                if cancel_rx.await.is_ok() {
                    received_cancel.store(true, Ordering::Release);
                }
                return;
            }

            tokio::select! {
                biased;
                res = &mut cancel_rx => {
                    if res.is_ok() {
                        received_cancel.store(true, Ordering::Release);
                    }
                }
                Ok(()) = stop_rx => {
                    if let Some(text) = mode {
                        let _ = event_tx.send((
                            run_id,
                            PipelineEvent::TranscriptReady {
                                text,
                                duration_secs: Some(0.5),
                            },
                        ));
                    } else if cancel_rx.await.is_ok() {
                        received_cancel.store(true, Ordering::Release);
                    }
                }
            }
        });

        ActiveSession::new(stop_tx, cancel_tx)
    }
}

fn fast_timings() -> DaemonTimings {
    DaemonTimings {
        done_display_hold: Duration::from_millis(60),
        no_speech_reset_delay: Duration::from_millis(60),
        error_reset_delay: Duration::from_millis(80),
        transcribing_timeout: Duration::from_millis(120),
    }
}

#[tokio::test]
async fn hotkey_toggle_delivers_transcript_saves_history_copies_last_and_resets_idle() {
    let dir = TempDir::new("toggle");
    let store = Arc::new(PreferencesStore::new(dir.path()));
    let clipboard = Arc::new(Mutex::new(RecordingClipboard::default()));
    let sounds = Arc::new(StubSoundPlayer::default());
    let hotkeys = Arc::new(StubHotkeyBackend::default());
    let backend = Arc::new(FakeDictationBackend::new(FakeBackendBehavior::ReturnText(
        "Hallo PulseScribe".into(),
    )));

    let platform = DaemonPlatform {
        app_detector: Arc::new(StubAppDetector::new("WINWORD", "Microsoft Word")),
        sound: Arc::clone(&sounds) as Arc<dyn SoundPlayer>,
        hotkeys: Arc::clone(&hotkeys) as Arc<dyn HotkeyBackend>,
        permissions: Arc::new(DefaultPermissionsChecker),
        clipboard: Arc::clone(&clipboard) as Arc<Mutex<dyn TranscriptClipboardBackend + Send>>,
        platform_kind: PlatformKind::Windows,
    };

    let config = DaemonConfig {
        hotkeys: HotkeyBinding::new(Some("f19".into()), Some("fn".into())),
        timings: fast_timings(),
        show_rtf: true,
        ..DaemonConfig::default()
    };

    let runtime = DaemonRuntime::spawn(
        Arc::clone(&store),
        config,
        platform,
        Arc::clone(&backend) as Arc<dyn DictationBackend>,
        DaemonRuntimeOptions {
            enable_ipc: false,
            enable_reload_watcher: false,
            ..DaemonRuntimeOptions::default()
        },
    )
    .await;

    let handle = runtime.handle();
    let mut events = handle.subscribe();

    hotkeys.emit(HotkeyEvent::Toggle);
    tokio::time::timeout(Duration::from_secs(2), async {
        loop {
            if let Ok(DaemonEvent::StateChanged {
                state: AppState::Recording,
                ..
            }) = events.recv().await
            {
                break;
            }
        }
    })
    .await
    .expect("should reach Recording state");

    // Backend receives process identifier (`WINWORD`) for LLM context routing.
    assert_eq!(
        backend.observed_context_app.lock().unwrap().as_deref(),
        Some("WINWORD")
    );

    hotkeys.emit(HotkeyEvent::Toggle);

    let mut saw_delivered = false;
    let mut saw_idle_reset = false;
    tokio::time::timeout(Duration::from_secs(2), async {
        while !(saw_delivered && saw_idle_reset) {
            match events.recv().await {
                Ok(DaemonEvent::TranscriptDelivered {
                    text,
                    outcome,
                    target_app,
                    ..
                }) => {
                    assert_eq!(text, "Hallo PulseScribe");
                    assert!(outcome.ok);
                    assert_eq!(outcome.feedback, PASTED);
                    // Delivery UI label uses human-readable `frontmost_app_label`.
                    assert_eq!(target_app.as_deref(), Some("Microsoft Word"));
                    saw_delivered = true;
                }
                Ok(DaemonEvent::StateChanged {
                    state: AppState::Idle,
                    ..
                }) if saw_delivered => {
                    saw_idle_reset = true;
                }
                _ => {}
            }
        }
    })
    .await
    .expect("should deliver transcript and auto-reset to Idle");

    assert_eq!(handle.state(), AppState::Idle);
    assert_eq!(clipboard.lock().unwrap().pasted, vec!["Hallo PulseScribe"]);
    assert_eq!(
        handle.copy_last_transcript().await,
        Some(LAST_TRANSCRIPT_COPIED)
    );
    assert_eq!(clipboard.lock().unwrap().copied, vec!["Hallo PulseScribe"]);

    // Shutdown drains FIFO history writer before we read history.jsonl (zero timing race).
    runtime.shutdown().await;

    let history = HistoryStore::new(store.paths().config_dir());
    let entries = history.get_recent_transcripts(5);
    assert_eq!(entries.len(), 1);
    assert_eq!(entries[0].text, "Hallo PulseScribe");
    assert_eq!(entries[0].app.as_deref(), Some("WINWORD"));
}

#[tokio::test]
async fn file_ipc_wizard_test_roundtrip_and_config_hot_reload() {
    let dir = TempDir::new("ipc-reload");
    let store = Arc::new(PreferencesStore::new(dir.path()));
    fs::write(
        store.paths().env_file(),
        "PULSESCRIBE_TOGGLE_HOTKEY=f19\nPULSESCRIBE_HOLD_HOTKEY=fn\n",
    )
    .unwrap();

    let clipboard = Arc::new(Mutex::new(RecordingClipboard::default()));
    let hotkeys = Arc::new(StubHotkeyBackend::default());
    let platform = DaemonPlatform {
        app_detector: Arc::new(StubAppDetector::new("Wizard", "PulseScribe Wizard")),
        sound: Arc::new(StubSoundPlayer::default()),
        hotkeys: Arc::clone(&hotkeys) as Arc<dyn HotkeyBackend>,
        permissions: Arc::new(DefaultPermissionsChecker),
        clipboard: Arc::clone(&clipboard) as Arc<Mutex<dyn TranscriptClipboardBackend + Send>>,
        platform_kind: PlatformKind::Windows,
    };

    let config = DaemonConfig {
        hotkeys: HotkeyBinding::new(Some("f19".into()), Some("fn".into())),
        timings: fast_timings(),
        ..DaemonConfig::default()
    };

    let runtime = DaemonRuntime::spawn(
        Arc::clone(&store),
        config,
        platform,
        Arc::new(FakeDictationBackend::new(FakeBackendBehavior::ReturnText(
            "IPC Test Ok".into(),
        ))),
        DaemonRuntimeOptions {
            enable_ipc: true,
            ipc_poll_interval: Duration::from_millis(20),
            enable_reload_watcher: true,
            reload_poll_interval: Duration::from_millis(50),
        },
    )
    .await;

    let handle = runtime.handle();
    let mut events = handle.subscribe();
    let ipc_paths = IpcPaths::from_app_paths(store.paths());
    let mut ipc_client = IpcClient::new(ipc_paths.clone());

    let cmd_id = ipc_client.send_command(&IpcCommandKind::StartTest).unwrap();
    let rec_resp = tokio::time::timeout(Duration::from_secs(2), async {
        loop {
            if let Some(resp) = ipc_client.poll_response(&cmd_id) {
                if resp.status == IpcStatus::Recording {
                    return resp;
                }
            }
            tokio::time::sleep(Duration::from_millis(15)).await;
        }
    })
    .await
    .expect("IPC recording status should arrive");
    assert_eq!(rec_resp.status, IpcStatus::Recording);

    let _ = ipc_client.send_command(&IpcCommandKind::StopTest).unwrap();
    let done_resp = tokio::time::timeout(Duration::from_secs(2), async {
        loop {
            if let Some(resp) = ipc_client.poll_response(&cmd_id) {
                if resp.status == IpcStatus::Done {
                    return resp;
                }
            }
            tokio::time::sleep(Duration::from_millis(15)).await;
        }
    })
    .await
    .expect("IPC done status should arrive");
    assert_eq!(done_resp.transcript, "IPC Test Ok");
    assert!(clipboard.lock().unwrap().pasted.is_empty());

    fs::write(
        store.paths().env_file(),
        "PULSESCRIBE_TOGGLE_HOTKEY=ctrl+alt+r\nPULSESCRIBE_HOLD_HOTKEY=ctrl+win\n",
    )
    .unwrap();
    fs::write(store.paths().config_dir().join(RELOAD_SIGNAL_FILENAME), "1").unwrap();

    tokio::time::timeout(Duration::from_secs(2), async {
        loop {
            if let Ok(DaemonEvent::ConfigReloaded) = events.recv().await {
                break;
            }
        }
    })
    .await
    .expect("ConfigReloaded event should fire");

    assert_eq!(
        handle.config().hotkeys,
        HotkeyBinding::new(Some("ctrl+alt+r".into()), Some("ctrl+win".into()))
    );
    assert!(hotkeys.registered_bindings().len() >= 2);

    runtime.shutdown().await;
    assert!(!ipc_paths.command_file.exists());
    assert!(!ipc_paths.response_file.exists());
    assert!(HistoryStore::new(store.paths().config_dir())
        .get_recent_transcripts(5)
        .is_empty());
}

#[tokio::test]
async fn watchdog_timeout_and_mid_capture_failure_cancel_active_sessions_cleanly() {
    let dir = TempDir::new("watchdog-fail");
    let store = Arc::new(PreferencesStore::new(dir.path()));
    let clipboard = Arc::new(Mutex::new(RecordingClipboard::default()));
    let hotkeys = Arc::new(StubHotkeyBackend::default());
    let hang_backend = Arc::new(FakeDictationBackend::new(
        FakeBackendBehavior::HangAfterStop,
    ));

    let platform = DaemonPlatform {
        app_detector: Arc::new(StubAppDetector::new("Notes", "Notes")),
        sound: Arc::new(StubSoundPlayer::default()),
        hotkeys: Arc::clone(&hotkeys) as Arc<dyn HotkeyBackend>,
        permissions: Arc::new(DefaultPermissionsChecker),
        clipboard: Arc::clone(&clipboard) as Arc<Mutex<dyn TranscriptClipboardBackend + Send>>,
        platform_kind: PlatformKind::MacOs,
    };

    let config = DaemonConfig {
        hotkeys: HotkeyBinding::new(None, Some("fn".into())),
        timings: fast_timings(),
        ..DaemonConfig::default()
    };

    let runtime = DaemonRuntime::spawn(
        Arc::clone(&store),
        config.clone(),
        platform,
        Arc::clone(&hang_backend) as Arc<dyn DictationBackend>,
        DaemonRuntimeOptions {
            enable_ipc: false,
            enable_reload_watcher: false,
            ..DaemonRuntimeOptions::default()
        },
    )
    .await;

    let handle = runtime.handle();
    let mut events = handle.subscribe();

    hotkeys.emit(HotkeyEvent::HoldDown {
        source: HotkeySource::EventTap,
    });
    tokio::time::timeout(Duration::from_secs(2), async {
        loop {
            if let Ok(DaemonEvent::StateChanged {
                state: AppState::Recording,
                ..
            }) = events.recv().await
            {
                break;
            }
        }
    })
    .await
    .unwrap();

    hotkeys.emit(HotkeyEvent::HoldUp {
        source: HotkeySource::EventTap,
    });

    let mut saw_timeout_error = false;
    let mut recovered_idle = false;
    tokio::time::timeout(Duration::from_secs(2), async {
        while !(saw_timeout_error && recovered_idle) {
            match events.recv().await {
                Ok(DaemonEvent::StateChanged {
                    state: AppState::Error,
                    error: Some(err),
                    ..
                }) => {
                    assert_eq!(err.code, DaemonErrorCode::Timeout);
                    saw_timeout_error = true;
                }
                Ok(DaemonEvent::StateChanged {
                    state: AppState::Idle,
                    ..
                }) if saw_timeout_error => {
                    recovered_idle = true;
                }
                _ => {}
            }
        }
    })
    .await
    .expect("watchdog timeout should transition Error -> Idle");

    assert_eq!(handle.state(), AppState::Idle);
    assert!(hang_backend.received_cancel.load(Ordering::Acquire));
    runtime.shutdown().await;

    // Also verify mid-capture `PipelineEvent::Failed` dispatches an explicit `cancel_tx` signal.
    let fail_backend = Arc::new(FakeDictationBackend::new(
        FakeBackendBehavior::FailMidCapture,
    ));
    let fail_runtime = DaemonRuntime::spawn(
        store,
        config,
        DaemonPlatform::noop(clipboard),
        Arc::clone(&fail_backend) as Arc<dyn DictationBackend>,
        DaemonRuntimeOptions {
            enable_ipc: false,
            enable_reload_watcher: false,
            ..DaemonRuntimeOptions::default()
        },
    )
    .await;
    let fail_handle = fail_runtime.handle();
    let mut fail_events = fail_handle.subscribe();
    fail_handle.start_recording();
    tokio::time::timeout(Duration::from_secs(2), async {
        loop {
            if let Ok(DaemonEvent::StateChanged {
                state: AppState::Error,
                error: Some(err),
                ..
            }) = fail_events.recv().await
            {
                assert_eq!(err.code, DaemonErrorCode::Connection);
                break;
            }
        }
    })
    .await
    .expect("mid-capture failure should enter Error state");
    tokio::time::timeout(Duration::from_secs(1), async {
        while !fail_backend.received_cancel.load(Ordering::Acquire) {
            tokio::time::sleep(Duration::from_millis(5)).await;
        }
    })
    .await
    .expect("mid-capture failure should explicitly cancel the active session");
    fail_runtime.shutdown().await;
}
