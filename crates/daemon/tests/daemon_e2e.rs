//! End-to-end integration tests for `DaemonRuntime` covering hotkeys, clipboard/paste delivery,
//! `HistoryStore` persistence, File-IPC wizard round-trips, watchdog timeouts, and config reload.

use std::fs;
use std::path::PathBuf;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use pulsescribe_core::transcript_delivery::{LAST_TRANSCRIPT_COPIED, PASTED};
use pulsescribe_core::{AppState, HistoryStore, PreferencesStore, TranscriptClipboardBackend};
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

fn unique_test_dir(tag: &str) -> PathBuf {
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
    dir
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

struct StubAppDetector(String);

impl AppDetector for StubAppDetector {
    fn frontmost_app(&self) -> Option<String> {
        Some(self.0.clone())
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
}

struct FakeDictationBackend {
    behavior: FakeBackendBehavior,
}

impl DictationBackend for FakeDictationBackend {
    fn start_session(
        &self,
        run_id: u64,
        _config: DaemonConfig,
        _is_test: bool,
        _target_app: Option<String>,
        event_tx: mpsc::UnboundedSender<(u64, PipelineEvent)>,
    ) -> ActiveSession {
        let (stop_tx, stop_rx) = oneshot::channel();
        let (cancel_tx, mut cancel_rx) = oneshot::channel();
        let response = match &self.behavior {
            FakeBackendBehavior::ReturnText(text) => Some(text.clone()),
            FakeBackendBehavior::HangAfterStop => None,
        };

        tokio::spawn(async move {
            let _ = event_tx.send((run_id, PipelineEvent::CaptureStarted));
            let _ = event_tx.send((run_id, PipelineEvent::AudioLevel(0.42)));
            let _ = event_tx.send((run_id, PipelineEvent::SpeechDetected));

            tokio::select! {
                _ = &mut cancel_rx => {}
                _ = stop_rx => {
                    if let Some(text) = response {
                        let _ = event_tx.send((
                            run_id,
                            PipelineEvent::TranscriptReady {
                                text,
                                duration_secs: Some(0.5),
                            },
                        ));
                    } else {
                        // Simulate hung backend until watchdog cancels.
                        let _ = cancel_rx.await;
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
    let dir = unique_test_dir("toggle");
    let store = Arc::new(PreferencesStore::new(&dir));
    let clipboard = Arc::new(Mutex::new(RecordingClipboard::default()));
    let sounds = Arc::new(StubSoundPlayer::default());
    let hotkeys = Arc::new(StubHotkeyBackend::default());

    let platform = DaemonPlatform {
        app_detector: Arc::new(StubAppDetector("Mail".into())),
        sound: Arc::clone(&sounds) as Arc<dyn SoundPlayer>,
        hotkeys: Arc::clone(&hotkeys) as Arc<dyn HotkeyBackend>,
        permissions: Arc::new(DefaultPermissionsChecker),
        clipboard: Arc::clone(&clipboard) as Arc<Mutex<dyn TranscriptClipboardBackend + Send>>,
        platform_kind: PlatformKind::MacOs,
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
        Arc::new(FakeDictationBackend {
            behavior: FakeBackendBehavior::ReturnText("Hallo PulseScribe".into()),
        }),
        DaemonRuntimeOptions {
            enable_ipc: false,
            enable_reload_watcher: false,
            ..DaemonRuntimeOptions::default()
        },
    )
    .await;

    let handle = runtime.handle();
    let mut events = handle.subscribe();

    // Start via toggle hotkey, wait until Recording, then stop via toggle.
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
                    assert_eq!(target_app.as_deref(), Some("Mail"));
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

    // Verify JSONL history persistence in ~/.pulsescribe/history.jsonl.
    let history = HistoryStore::new(store.paths().config_dir());
    let entries = history.get_recent_transcripts(5);
    assert_eq!(entries.len(), 1);
    assert_eq!(entries[0].text, "Hallo PulseScribe");
    assert_eq!(entries[0].app.as_deref(), Some("Mail"));

    runtime.shutdown().await;
    let _ = fs::remove_dir_all(&dir);
}

#[tokio::test]
async fn file_ipc_wizard_test_roundtrip_and_config_hot_reload() {
    let dir = unique_test_dir("ipc-reload");
    let store = Arc::new(PreferencesStore::new(&dir));
    fs::write(
        store.paths().env_file(),
        "PULSESCRIBE_TOGGLE_HOTKEY=f19\nPULSESCRIBE_HOLD_HOTKEY=fn\n",
    )
    .unwrap();

    let clipboard = Arc::new(Mutex::new(RecordingClipboard::default()));
    let hotkeys = Arc::new(StubHotkeyBackend::default());
    let platform = DaemonPlatform {
        app_detector: Arc::new(StubAppDetector("Wizard".into())),
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
        Arc::new(FakeDictationBackend {
            behavior: FakeBackendBehavior::ReturnText("IPC Test Ok".into()),
        }),
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

    // 1. Wizard sends StartTest -> daemon writes `recording` response.
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

    // 2. Wizard sends StopTest -> daemon writes `done` with transcript without pasting or history.
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
    assert!(HistoryStore::new(store.paths().config_dir())
        .get_recent_transcripts(5)
        .is_empty());

    // 3. Edit .env + touch .reload -> daemon reloads config and re-registers updated hotkeys.
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
    let _ = fs::remove_dir_all(&dir);
}

#[tokio::test]
async fn watchdog_timeout_recovers_hung_transcription_to_idle() {
    let dir = unique_test_dir("watchdog");
    let store = Arc::new(PreferencesStore::new(&dir));
    let clipboard = Arc::new(Mutex::new(RecordingClipboard::default()));
    let hotkeys = Arc::new(StubHotkeyBackend::default());

    let platform = DaemonPlatform {
        app_detector: Arc::new(StubAppDetector("Notes".into())),
        sound: Arc::new(StubSoundPlayer::default()),
        hotkeys: Arc::clone(&hotkeys) as Arc<dyn HotkeyBackend>,
        permissions: Arc::new(DefaultPermissionsChecker),
        clipboard,
        platform_kind: PlatformKind::MacOs,
    };

    let config = DaemonConfig {
        hotkeys: HotkeyBinding::new(None, Some("fn".into())),
        timings: fast_timings(),
        ..DaemonConfig::default()
    };

    let runtime = DaemonRuntime::spawn(
        store,
        config,
        platform,
        Arc::new(FakeDictationBackend {
            behavior: FakeBackendBehavior::HangAfterStop,
        }),
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
                    assert_eq!(err.code, pulsescribe_core::DaemonErrorCode::Timeout);
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
    runtime.shutdown().await;
    let _ = fs::remove_dir_all(&dir);
}
