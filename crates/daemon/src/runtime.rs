//! Async dictation daemon runtime actor wiring [`DaemonStateMachine`], platform adapters,
//! file-based IPC, history persistence, hot-reload polling, and a pluggable [`DictationBackend`].

use std::collections::HashMap;
use std::sync::{Arc, Mutex};
use std::time::Duration;

use pulsescribe_core::{
    AppState, DaemonStatusError, DeliveryOutcome, HistoryStore, PreferencesStore,
    TranscriptClipboardBackend, TranscriptDelivery,
};
use pulsescribe_platform::{AppDetector, HotkeyEvent, HotkeyRegistration, SoundName};
use tokio::sync::{broadcast, mpsc, oneshot, watch};
use tokio::task::JoinHandle;

use crate::config::{DaemonConfig, ReloadTracker};
use crate::events::{DaemonEvent, DaemonEventBus};
use crate::ipc::{IpcCommandKind, IpcPaths, IpcServer, IpcStatus, IPC_POLL_INTERVAL};
use crate::platform_bundle::DaemonPlatform;
use crate::state_machine::{DaemonAction, DaemonStateMachine};

/// Default interval for polling `~/.pulsescribe/.env` and `.reload` signal changes.
pub const DEFAULT_RELOAD_POLL_INTERVAL: Duration = Duration::from_secs(1);

/// Events pushed from an active [`DictationBackend`] capture/transcription session into the runtime.
#[derive(Debug, Clone, PartialEq)]
pub enum PipelineEvent {
    CaptureStarted,
    AudioLevel(f32),
    SpeechDetected,
    InterimText(String),
    RefiningStarted,
    TranscriptReady {
        text: String,
        duration_secs: Option<f64>,
    },
    Failed(DaemonStatusError),
}

/// Control channels returned by [`DictationBackend::start_session`] for stopping or aborting a run.
#[derive(Debug, Default)]
pub struct ActiveSession {
    pub stop_tx: Option<oneshot::Sender<()>>,
    pub cancel_tx: Option<oneshot::Sender<()>>,
}

impl ActiveSession {
    pub fn new(stop_tx: oneshot::Sender<()>, cancel_tx: oneshot::Sender<()>) -> Self {
        Self {
            stop_tx: Some(stop_tx),
            cancel_tx: Some(cancel_tx),
        }
    }

    pub fn stop(&mut self) {
        if let Some(tx) = self.stop_tx.take() {
            let _ = tx.send(());
        }
    }

    pub fn cancel(&mut self) {
        // Send cancel before dropping `stop_tx` so listeners selecting on both receivers observe
        // an explicit cancellation signal first.
        if let Some(tx) = self.cancel_tx.take() {
            let _ = tx.send(());
        }
        self.stop_tx = None;
    }
}

/// Pluggable audio capture + transcription + optional refinement engine driven by [`DaemonRuntime`].
///
/// Receives `app_detector` so implementations can query [`AppDetector::frontmost_app`] (process
/// identifier for LLM context routing) off the Tokio worker thread.
pub trait DictationBackend: Send + Sync {
    fn start_session(
        &self,
        run_id: u64,
        config: DaemonConfig,
        is_test: bool,
        app_detector: Arc<dyn AppDetector>,
        event_tx: mpsc::UnboundedSender<(u64, PipelineEvent)>,
    ) -> ActiveSession;
}

/// Optional timing and feature switches for [`DaemonRuntime::spawn`].
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct DaemonRuntimeOptions {
    pub enable_ipc: bool,
    pub ipc_poll_interval: Duration,
    pub enable_reload_watcher: bool,
    pub reload_poll_interval: Duration,
}

impl Default for DaemonRuntimeOptions {
    fn default() -> Self {
        Self {
            enable_ipc: true,
            ipc_poll_interval: IPC_POLL_INTERVAL,
            enable_reload_watcher: true,
            reload_poll_interval: DEFAULT_RELOAD_POLL_INTERVAL,
        }
    }
}

struct DynClipboardRef<'a>(&'a mut (dyn TranscriptClipboardBackend + Send));

impl TranscriptClipboardBackend for DynClipboardRef<'_> {
    fn paste_transcript(&mut self, text: &str) -> bool {
        self.0.paste_transcript(text)
    }

    fn copy_transcript(&mut self, text: &str) -> bool {
        self.0.copy_transcript(text)
    }
}

struct PendingIpcResponse {
    cmd_id: String,
    status: IpcStatus,
    transcript: String,
    error: Option<String>,
}

struct PendingHistoryEntry {
    text: String,
    mode: Option<String>,
    language: Option<String>,
    refined: bool,
    target_app: Option<String>,
}

enum RuntimeCommand {
    Hotkey(HotkeyEvent),
    StartRecording,
    StopRecording,
    StartTest,
    Cancel,
    SetTestHotkeyMode(bool),
    ReloadConfig,
    CopyLastTranscript(oneshot::Sender<&'static str>),
    IpcCommand {
        cmd_id: String,
        kind: IpcCommandKind,
    },
    DeliveryFinished {
        run_id: u64,
        text: String,
        outcome: DeliveryOutcome,
        target_app: Option<String>,
        duration_secs: Option<f64>,
    },
    ConfigReloadLoaded(Box<DaemonConfig>),
    WatchdogTimeout(u64),
    IdleReset(u64),
    Shutdown(Option<oneshot::Sender<()>>),
}

/// Thread-safe, cheap-to-clone handle used by Tray, Native Overlay, Tauri WebView, and CLI to
/// control and observe a running [`DaemonRuntime`].
#[derive(Clone)]
pub struct DaemonHandle {
    cmd_tx: mpsc::UnboundedSender<RuntimeCommand>,
    event_bus: DaemonEventBus,
    state_rx: watch::Receiver<AppState>,
    config_rx: watch::Receiver<DaemonConfig>,
}

impl DaemonHandle {
    pub fn subscribe(&self) -> broadcast::Receiver<DaemonEvent> {
        self.event_bus.subscribe()
    }

    pub fn state(&self) -> AppState {
        *self.state_rx.borrow()
    }

    pub fn config(&self) -> DaemonConfig {
        self.config_rx.borrow().clone()
    }

    pub fn toggle_recording(&self) {
        let _ = self
            .cmd_tx
            .send(RuntimeCommand::Hotkey(HotkeyEvent::Toggle));
    }

    pub fn start_recording(&self) {
        let _ = self.cmd_tx.send(RuntimeCommand::StartRecording);
    }

    pub fn stop_recording(&self) {
        let _ = self.cmd_tx.send(RuntimeCommand::StopRecording);
    }

    pub fn start_test(&self) {
        let _ = self.cmd_tx.send(RuntimeCommand::StartTest);
    }

    pub fn cancel(&self) {
        let _ = self.cmd_tx.send(RuntimeCommand::Cancel);
    }

    pub fn set_test_hotkey_mode(&self, enabled: bool) {
        let _ = self.cmd_tx.send(RuntimeCommand::SetTestHotkeyMode(enabled));
    }

    pub fn reload_config(&self) {
        let _ = self.cmd_tx.send(RuntimeCommand::ReloadConfig);
    }

    pub async fn copy_last_transcript(&self) -> Option<&'static str> {
        let (tx, rx) = oneshot::channel();
        if self
            .cmd_tx
            .send(RuntimeCommand::CopyLastTranscript(tx))
            .is_err()
        {
            return None;
        }
        rx.await.ok()
    }

    /// Request graceful shutdown and wait until the runtime finishes draining pending history,
    /// delivery, IPC, reload, sound, and hotkey cleanup tasks.
    pub async fn shutdown(&self) {
        let (tx, rx) = oneshot::channel();
        if self.cmd_tx.send(RuntimeCommand::Shutdown(Some(tx))).is_ok() {
            let _ = rx.await;
        }
    }
}

/// Running dictation daemon actor and its background task handle.
pub struct DaemonRuntime {
    handle: DaemonHandle,
    join_handle: JoinHandle<()>,
}

impl DaemonRuntime {
    /// Start the unified daemon actor on the current Tokio runtime.
    pub async fn spawn(
        store: Arc<PreferencesStore>,
        initial_config: DaemonConfig,
        platform: DaemonPlatform,
        backend: Arc<dyn DictationBackend>,
        options: DaemonRuntimeOptions,
    ) -> Self {
        let event_bus = DaemonEventBus::default();
        let (cmd_tx, cmd_rx) = mpsc::unbounded_channel();
        let (pipeline_tx, pipeline_rx) = mpsc::unbounded_channel();
        let (state_tx, state_rx) = watch::channel(AppState::Idle);
        let (config_tx, config_rx) = watch::channel(initial_config.clone());

        let handle = DaemonHandle {
            cmd_tx: cmd_tx.clone(),
            event_bus: event_bus.clone(),
            state_rx,
            config_rx,
        };

        let ipc_paths = IpcPaths::from_app_paths(store.paths());
        let (ipc_shutdown_tx, ipc_shutdown_rx) = watch::channel(false);
        let ipc_task = if options.enable_ipc {
            let ipc_cmd_tx = cmd_tx.clone();
            let server = IpcServer::new(ipc_paths.clone());
            Some(
                server
                    .spawn_polling_task(
                        options.ipc_poll_interval,
                        ipc_shutdown_rx,
                        move |cmd_id, kind| {
                            let _ = ipc_cmd_tx.send(RuntimeCommand::IpcCommand { cmd_id, kind });
                        },
                    )
                    .await,
            )
        } else {
            None
        };

        let (reload_shutdown_tx, mut reload_shutdown_rx) = watch::channel(false);
        let reload_task = if options.enable_reload_watcher {
            let tracker_paths = store.paths().clone();
            let reload_cmd_tx = cmd_tx.clone();
            let interval = options.reload_poll_interval.max(Duration::from_millis(50));
            Some(tokio::spawn(async move {
                let mut tracker =
                    tokio::task::spawn_blocking(move || ReloadTracker::new(&tracker_paths))
                        .await
                        .ok();
                let mut ticker = tokio::time::interval(interval);
                ticker.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
                // Consume initial immediate tick so startup doesn't trigger a redundant poll.
                ticker.tick().await;
                loop {
                    tokio::select! {
                        biased;
                        changed = reload_shutdown_rx.changed() => {
                            if changed.is_err() || *reload_shutdown_rx.borrow() {
                                break;
                            }
                        }
                        _ = ticker.tick() => {
                            if *reload_shutdown_rx.borrow() {
                                break;
                            }
                            let Some(mut current) = tracker.take() else {
                                break;
                            };
                            let res = tokio::task::spawn_blocking(move || {
                                let changed = current.poll_changed();
                                (current, changed)
                            })
                            .await;
                            match res {
                                Ok((updated, changed)) => {
                                    tracker = Some(updated);
                                    if changed
                                        && reload_cmd_tx.send(RuntimeCommand::ReloadConfig).is_err()
                                    {
                                        break;
                                    }
                                }
                                Err(_) => break,
                            }
                        }
                    }
                }
            }))
        } else {
            None
        };

        // Serialized FIFO IPC response writer (preserves write order and drains before shutdown).
        let (ipc_resp_tx, mut ipc_resp_rx) = mpsc::unbounded_channel::<PendingIpcResponse>();
        let writer_paths = ipc_paths.clone();
        let ipc_writer_task = tokio::spawn(async move {
            while let Some(resp) = ipc_resp_rx.recv().await {
                let paths = writer_paths.clone();
                let _ = tokio::task::spawn_blocking(move || {
                    let server = IpcServer::new(paths);
                    if let Err(err) = server.send_response(
                        &resp.cmd_id,
                        resp.status,
                        &resp.transcript,
                        resp.error.as_deref(),
                    ) {
                        tracing::warn!(
                            id = %resp.cmd_id,
                            status = resp.status.as_str(),
                            error = %err,
                            "failed to write IPC response"
                        );
                    }
                })
                .await;
            }
        });

        // Serialized FIFO history writer (preserves chronological JSONL order and drains on shutdown).
        let history = Arc::new(HistoryStore::new(store.paths().config_dir()));
        let history_app_detector = Arc::clone(&platform.app_detector);
        let (history_tx, mut history_rx) = mpsc::unbounded_channel::<PendingHistoryEntry>();
        let history_writer_task = tokio::spawn(async move {
            while let Some(item) = history_rx.recv().await {
                let history = Arc::clone(&history);
                let app_detector = Arc::clone(&history_app_detector);
                let _ = tokio::task::spawn_blocking(move || {
                    let app_ctx = item.target_app.or_else(|| app_detector.frontmost_app());
                    if let Err(err) = history.save_transcript(
                        &item.text,
                        item.mode.as_deref(),
                        item.language.as_deref(),
                        item.refined,
                        app_ctx.as_deref(),
                    ) {
                        tracing::warn!(error = %err, "failed to save transcript history");
                    }
                })
                .await;
            }
        });

        // Serialized sound playback worker so cold asset loads / Win32 alias lookups never stall
        // Tokio worker threads.
        let sound_player = Arc::clone(&platform.sound);
        let (sound_tx, mut sound_rx) = mpsc::unbounded_channel::<SoundName>();
        let sound_task = tokio::spawn(async move {
            while let Some(sound) = sound_rx.recv().await {
                let player = Arc::clone(&sound_player);
                let _ = tokio::task::spawn_blocking(move || {
                    player.play(sound);
                })
                .await;
            }
        });

        let delivery = Arc::new(Mutex::new(TranscriptDelivery::for_platform(
            platform.platform_kind.is_macos(),
        )));
        let state_machine = DaemonStateMachine::new(initial_config.timings);

        let mut actor = DaemonActor {
            store,
            delivery,
            config: initial_config,
            platform,
            backend,
            state_machine,
            event_bus,
            cmd_tx,
            pipeline_tx,
            state_tx,
            config_tx,
            ipc_resp_tx: Some(ipc_resp_tx),
            ipc_writer_task: Some(ipc_writer_task),
            ipc_shutdown_tx,
            ipc_task,
            history_tx: Some(history_tx),
            history_writer_task: Some(history_writer_task),
            sound_tx: Some(sound_tx),
            sound_task: Some(sound_task),
            reload_shutdown_tx,
            reload_task,
            hotkey_registration: None,
            sessions: HashMap::new(),
            delivery_tasks: Vec::new(),
            watchdog_task: None,
            idle_reset_task: None,
            reload_in_flight: false,
            reload_pending_after_flight: false,
            shutting_down: false,
        };

        actor.register_hotkeys().await;
        let join_handle = tokio::spawn(actor.run(cmd_rx, pipeline_rx));

        Self {
            handle,
            join_handle,
        }
    }

    pub fn handle(&self) -> DaemonHandle {
        self.handle.clone()
    }

    /// Shut down the daemon actor cleanly and await completion of all background tasks.
    pub async fn shutdown(self) {
        self.handle.shutdown().await;
        let _ = self.join_handle.await;
    }
}

struct DaemonActor {
    store: Arc<PreferencesStore>,
    delivery: Arc<Mutex<TranscriptDelivery>>,
    config: DaemonConfig,
    platform: DaemonPlatform,
    backend: Arc<dyn DictationBackend>,
    state_machine: DaemonStateMachine,
    event_bus: DaemonEventBus,
    cmd_tx: mpsc::UnboundedSender<RuntimeCommand>,
    pipeline_tx: mpsc::UnboundedSender<(u64, PipelineEvent)>,
    state_tx: watch::Sender<AppState>,
    config_tx: watch::Sender<DaemonConfig>,
    ipc_resp_tx: Option<mpsc::UnboundedSender<PendingIpcResponse>>,
    ipc_writer_task: Option<JoinHandle<()>>,
    ipc_shutdown_tx: watch::Sender<bool>,
    ipc_task: Option<JoinHandle<()>>,
    history_tx: Option<mpsc::UnboundedSender<PendingHistoryEntry>>,
    history_writer_task: Option<JoinHandle<()>>,
    sound_tx: Option<mpsc::UnboundedSender<SoundName>>,
    sound_task: Option<JoinHandle<()>>,
    reload_shutdown_tx: watch::Sender<bool>,
    reload_task: Option<JoinHandle<()>>,
    hotkey_registration: Option<Box<dyn HotkeyRegistration>>,
    sessions: HashMap<u64, ActiveSession>,
    delivery_tasks: Vec<JoinHandle<()>>,
    watchdog_task: Option<JoinHandle<()>>,
    idle_reset_task: Option<JoinHandle<()>>,
    reload_in_flight: bool,
    reload_pending_after_flight: bool,
    shutting_down: bool,
}

impl DaemonActor {
    async fn run(
        mut self,
        mut cmd_rx: mpsc::UnboundedReceiver<RuntimeCommand>,
        mut pipeline_rx: mpsc::UnboundedReceiver<(u64, PipelineEvent)>,
    ) {
        let mut shutdown_ack: Option<oneshot::Sender<()>> = None;

        loop {
            tokio::select! {
                biased;
                maybe_cmd = cmd_rx.recv() => {
                    let Some(cmd) = maybe_cmd else {
                        break;
                    };
                    match cmd {
                        RuntimeCommand::Shutdown(ack) => {
                            shutdown_ack = ack;
                            break;
                        }
                        other => self.handle_command(other).await,
                    }
                }
                maybe_evt = pipeline_rx.recv() => {
                    let Some((run_id, event)) = maybe_evt else {
                        break;
                    };
                    self.handle_pipeline_event(run_id, event).await;
                }
            }
        }

        self.cleanup_on_shutdown().await;
        if let Some(ack) = shutdown_ack {
            let _ = ack.send(());
        }
    }

    async fn handle_command(&mut self, cmd: RuntimeCommand) {
        let actions = match cmd {
            RuntimeCommand::Hotkey(event) => self.state_machine.on_hotkey(event, None),
            RuntimeCommand::StartRecording => self.state_machine.on_start_recording(None),
            RuntimeCommand::StopRecording => self.state_machine.on_stop_recording(),
            RuntimeCommand::StartTest => self.state_machine.on_start_test(None),
            RuntimeCommand::Cancel => self.state_machine.on_cancel(),
            RuntimeCommand::SetTestHotkeyMode(enabled) => {
                self.state_machine.set_test_hotkey_mode(enabled);
                Vec::new()
            }
            RuntimeCommand::ReloadConfig => self.state_machine.on_config_reload_requested(),
            RuntimeCommand::CopyLastTranscript(reply_tx) => {
                let delivery = Arc::clone(&self.delivery);
                let clipboard = Arc::clone(&self.platform.clipboard);
                let handle = tokio::task::spawn_blocking(move || {
                    let msg = {
                        let d = delivery.lock().unwrap_or_else(|e| e.into_inner());
                        let mut c = clipboard.lock().unwrap_or_else(|e| e.into_inner());
                        let mut backend_ref = DynClipboardRef(&mut *c);
                        d.copy_last(&mut backend_ref)
                    };
                    let _ = reply_tx.send(msg);
                });
                self.track_delivery_task(handle);
                Vec::new()
            }
            RuntimeCommand::IpcCommand { cmd_id, kind } => {
                self.state_machine.on_ipc_command(cmd_id, kind)
            }
            RuntimeCommand::DeliveryFinished {
                run_id,
                text,
                outcome,
                target_app,
                duration_secs,
            } => {
                self.prune_finished_delivery_tasks();
                self.state_machine.on_delivery_finished(
                    run_id,
                    text,
                    outcome,
                    target_app,
                    duration_secs,
                    self.config.show_rtf,
                )
            }
            RuntimeCommand::ConfigReloadLoaded(new_cfg) => {
                self.reload_in_flight = false;
                if self.shutting_down {
                    self.reload_pending_after_flight = false;
                    Vec::new()
                } else if self.state_machine.active_run().is_none()
                    && self.state_machine.is_idle_like()
                {
                    self.apply_reloaded_config(*new_cfg).await;
                    if std::mem::take(&mut self.reload_pending_after_flight) {
                        self.spawn_config_reload();
                    }
                    Vec::new()
                } else {
                    // A dictation started while `.env` was loading; defer reload until Idle.
                    self.reload_pending_after_flight = false;
                    self.state_machine.on_config_reload_requested()
                }
            }
            RuntimeCommand::WatchdogTimeout(run_id) => {
                self.state_machine.on_watchdog_timeout(run_id)
            }
            RuntimeCommand::IdleReset(generation) => self.state_machine.on_reset_timer(generation),
            RuntimeCommand::Shutdown(_) => Vec::new(),
        };

        self.execute_actions(actions).await;
    }

    async fn handle_pipeline_event(&mut self, run_id: u64, event: PipelineEvent) {
        let mut stale_failed_run = None;
        let actions = match event {
            PipelineEvent::CaptureStarted => self.state_machine.on_capture_started(run_id),
            PipelineEvent::AudioLevel(level) => {
                if self
                    .state_machine
                    .active_run()
                    .is_some_and(|r| r.run_id == run_id)
                    && self.state_machine.is_capturing()
                {
                    self.event_bus.publish(DaemonEvent::audio_level(level));
                }
                Vec::new()
            }
            PipelineEvent::SpeechDetected => self.state_machine.on_speech_detected(run_id),
            PipelineEvent::InterimText(text) => self.state_machine.on_interim_text(run_id, text),
            PipelineEvent::RefiningStarted => self.state_machine.on_refining_started(run_id),
            PipelineEvent::TranscriptReady {
                text,
                duration_secs,
            } => {
                self.sessions.remove(&run_id);
                self.state_machine
                    .on_transcript_ready(run_id, text, duration_secs)
            }
            PipelineEvent::Failed(err) => {
                // Keep the session registered until `on_run_failed`'s `CancelCapture` action runs
                // so the backend receives an explicit cancellation signal.
                stale_failed_run = Some(run_id);
                self.state_machine.on_run_failed(run_id, err)
            }
        };

        self.execute_actions(actions).await;
        if let Some(failed_id) = stale_failed_run {
            if let Some(mut session) = self.sessions.remove(&failed_id) {
                session.cancel();
            }
        }
    }

    async fn execute_actions(&mut self, actions: Vec<DaemonAction>) {
        let _ = self.state_tx.send_if_modified(|current| {
            let next = self.state_machine.state();
            if *current != next {
                *current = next;
                true
            } else {
                false
            }
        });

        for action in actions {
            match action {
                DaemonAction::PublishEvent(event) => {
                    self.event_bus.publish(event);
                }
                DaemonAction::PlaySound(sound) => {
                    if self.config.sound_enabled {
                        if let Some(tx) = &self.sound_tx {
                            let _ = tx.send(sound);
                        }
                    }
                }
                DaemonAction::StartCapture { run_id, is_test } => {
                    let session = self.backend.start_session(
                        run_id,
                        self.config.clone(),
                        is_test,
                        Arc::clone(&self.platform.app_detector),
                        self.pipeline_tx.clone(),
                    );
                    self.sessions.insert(run_id, session);
                }
                DaemonAction::StopCapture { run_id } => {
                    if let Some(session) = self.sessions.get_mut(&run_id) {
                        session.stop();
                    }
                }
                DaemonAction::CancelCapture { run_id } => {
                    if let Some(mut session) = self.sessions.remove(&run_id) {
                        session.cancel();
                    }
                }
                DaemonAction::DeliverTranscript {
                    run_id,
                    text,
                    target_app,
                    duration_secs,
                } => {
                    let delivery = Arc::clone(&self.delivery);
                    let clipboard = Arc::clone(&self.platform.clipboard);
                    let app_detector = Arc::clone(&self.platform.app_detector);
                    let auto_paste = self.config.auto_paste;
                    let cmd_tx = self.cmd_tx.clone();
                    let handle = tokio::task::spawn_blocking(move || {
                        // Resolve human-readable target app label off the Tokio worker thread
                        // right at delivery time (matching Python `_done_status`).
                        let display_app = target_app.or_else(|| app_detector.frontmost_app_label());
                        let outcome = {
                            let mut d = delivery.lock().unwrap_or_else(|e| e.into_inner());
                            let mut c = clipboard.lock().unwrap_or_else(|e| e.into_inner());
                            let mut backend_ref = DynClipboardRef(&mut *c);
                            d.deliver(&text, auto_paste, &mut backend_ref)
                        };
                        let _ = cmd_tx.send(RuntimeCommand::DeliveryFinished {
                            run_id,
                            text,
                            outcome,
                            target_app: display_app,
                            duration_secs,
                        });
                    });
                    self.track_delivery_task(handle);
                }
                DaemonAction::SaveHistory { text, target_app } => {
                    if let Some(tx) = &self.history_tx {
                        let _ = tx.send(PendingHistoryEntry {
                            text,
                            mode: Some(self.config.mode.as_str().to_owned()),
                            language: self.config.language.clone(),
                            refined: self.config.refine,
                            target_app,
                        });
                    }
                }
                DaemonAction::SendIpcResponse {
                    cmd_id,
                    status,
                    transcript,
                    error,
                } => {
                    if let Some(tx) = &self.ipc_resp_tx {
                        let _ = tx.send(PendingIpcResponse {
                            cmd_id,
                            status,
                            transcript,
                            error,
                        });
                    }
                }
                DaemonAction::ScheduleWatchdog { run_id, timeout } => {
                    if let Some(prev) = self.watchdog_task.take() {
                        prev.abort();
                    }
                    let cmd_tx = self.cmd_tx.clone();
                    self.watchdog_task = Some(tokio::spawn(async move {
                        tokio::time::sleep(timeout).await;
                        let _ = cmd_tx.send(RuntimeCommand::WatchdogTimeout(run_id));
                    }));
                }
                DaemonAction::CancelWatchdog => {
                    if let Some(prev) = self.watchdog_task.take() {
                        prev.abort();
                    }
                }
                DaemonAction::ScheduleIdleReset { generation, delay } => {
                    if let Some(prev) = self.idle_reset_task.take() {
                        prev.abort();
                    }
                    let cmd_tx = self.cmd_tx.clone();
                    self.idle_reset_task = Some(tokio::spawn(async move {
                        tokio::time::sleep(delay).await;
                        let _ = cmd_tx.send(RuntimeCommand::IdleReset(generation));
                    }));
                }
                DaemonAction::ApplyPendingConfigReload => {
                    if self.shutting_down {
                        continue;
                    }
                    if self.reload_in_flight {
                        self.reload_pending_after_flight = true;
                    } else {
                        self.spawn_config_reload();
                    }
                }
            }
        }
    }

    fn spawn_config_reload(&mut self) {
        self.reload_in_flight = true;
        let store = Arc::clone(&self.store);
        let cmd_tx = self.cmd_tx.clone();
        tokio::task::spawn_blocking(move || {
            let reloaded = DaemonConfig::from_store_reload(&store);
            let _ = cmd_tx.send(RuntimeCommand::ConfigReloadLoaded(Box::new(reloaded)));
        });
    }

    async fn apply_reloaded_config(&mut self, new_cfg: DaemonConfig) {
        let hotkeys_changed = new_cfg.hotkeys != self.config.hotkeys;
        self.state_machine.update_timings(new_cfg.timings);
        self.config = new_cfg.clone();
        let _ = self.config_tx.send(new_cfg);

        if hotkeys_changed {
            self.register_hotkeys().await;
        }
        self.event_bus.publish(DaemonEvent::ConfigReloaded);
    }

    async fn register_hotkeys(&mut self) {
        if let Some(mut existing) = self.hotkey_registration.take() {
            let _ = tokio::task::spawn_blocking(move || {
                if let Err(err) = existing.unregister() {
                    tracing::warn!(error = %err, "failed to unregister previous hotkey");
                }
            })
            .await;
        }
        if self.config.hotkeys.is_empty() {
            return;
        }
        let binding = self.config.hotkeys.clone();
        let hotkeys = Arc::clone(&self.platform.hotkeys);
        let cmd_tx = self.cmd_tx.clone();
        let callback = Arc::new(move |event: HotkeyEvent| {
            let _ = cmd_tx.send(RuntimeCommand::Hotkey(event));
        });
        match tokio::task::spawn_blocking(move || hotkeys.register(&binding, callback)).await {
            Ok(Ok(reg)) => {
                self.hotkey_registration = Some(reg);
            }
            Ok(Err(err)) => {
                tracing::error!(error = %err, "failed to register global hotkeys");
            }
            Err(err) => {
                tracing::error!(error = %err, "hotkey registration task panicked");
            }
        }
    }

    fn track_delivery_task(&mut self, handle: JoinHandle<()>) {
        self.prune_finished_delivery_tasks();
        self.delivery_tasks.push(handle);
    }

    fn prune_finished_delivery_tasks(&mut self) {
        self.delivery_tasks.retain(|handle| !handle.is_finished());
    }

    async fn cleanup_on_shutdown(&mut self) {
        self.shutting_down = true;
        self.reload_pending_after_flight = false;

        let actions = self.state_machine.on_cancel();
        self.execute_actions(actions).await;

        for (_, mut session) in self.sessions.drain() {
            session.cancel();
        }
        if let Some(task) = self.watchdog_task.take() {
            task.abort();
        }
        if let Some(task) = self.idle_reset_task.take() {
            task.abort();
        }

        // Stop and await reload watcher cleanly so no blocking poll touches config dir after exit.
        let _ = self.reload_shutdown_tx.send(true);
        if let Some(task) = self.reload_task.take() {
            let _ = task.await;
        }

        // Drain outstanding clipboard delivery, history persistence, and sound tasks.
        for task in self.delivery_tasks.drain(..) {
            let _ = task.await;
        }
        self.history_tx.take();
        if let Some(task) = self.history_writer_task.take() {
            let _ = task.await;
        }
        self.sound_tx.take();
        if let Some(task) = self.sound_task.take() {
            let _ = task.await;
        }

        // Drain pending FIFO IPC response writes BEFORE stopping the IPC polling task so an
        // in-flight write can never recreate `ipc_response.json` after shutdown cleanup removes it.
        self.ipc_resp_tx.take();
        if let Some(task) = self.ipc_writer_task.take() {
            let _ = task.await;
        }
        let _ = self.ipc_shutdown_tx.send(true);
        if let Some(task) = self.ipc_task.take() {
            let _ = task.await;
        }
        if let Some(mut reg) = self.hotkey_registration.take() {
            let _ = tokio::task::spawn_blocking(move || {
                let _ = reg.unregister();
            })
            .await;
        }
    }
}
