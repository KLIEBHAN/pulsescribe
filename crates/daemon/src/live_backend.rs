//! Live audio capture, cloud/local transcription, and optional LLM refinement backend.
//!
//! Implements [`DictationBackend`] using `pulsescribe-audio` (`CaptureStream`, `MonoResampler`),
//! `pulsescribe-providers` (`DeepgramWarmConnectionManager`, `CloudProvider`, `LlmRefineProvider`),
//! and `pulsescribe-core` (`VocabularyStore`, `PromptsStore`). All credentials and overrides are
//! read from the immutable [`DaemonConfig`] snapshot rather than mutating global process env.

use std::fmt;
use std::fs::{self, OpenOptions};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Condvar, Mutex};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use bytes::Bytes;
use pulsescribe_audio::{
    encode_wav_i16_bytes, f32_to_pcm16_le_into, rms_f32, AudioError, CaptureConfig, CaptureStream,
    MonoResampler, TARGET_CHANNELS, TARGET_SAMPLE_RATE,
};
use pulsescribe_core::env::parse_bool;
use pulsescribe_core::{DaemonErrorCode, DaemonStatusError, PromptsStore, VocabularyStore};
use pulsescribe_platform::{
    is_permission_related_message, AppDetector, MicrophonePermissionStatus, PermissionsChecker,
};
use pulsescribe_providers::{
    build_http_client, detect_context_with, parse_app_contexts_json, resolve_refine_prompt,
    CloudProvider, DeepgramRestProvider, DeepgramStreamClient, DeepgramStreamConfig,
    DeepgramStreamEvent, DeepgramStreamTimeouts, DeepgramWarmConnectionManager, GroqProvider,
    LlmRefineProvider, OpenAiCompatibleProvider, OpenAiProvider, OpenRouterRouting, ProviderError,
    RefineProviderKind, TranscriptionRequest, DEEPGRAM_API_BASE_URL, DEEPGRAM_WS_URL,
    DEFAULT_DEEPGRAM_MODEL, DEFAULT_REFINE_PROVIDER, GROQ_API_BASE_URL, OPENAI_API_BASE_URL,
};
use reqwest::Client;
use tokio::sync::{mpsc, oneshot, watch, Mutex as AsyncMutex};
use tokio::task::JoinHandle;

use crate::config::{DaemonConfig, DaemonMode};
use crate::runtime::{ActiveSession, DictationBackend, PipelineEvent};

/// Normalized RMS threshold triggering `Listening -> Recording` VAD transition.
///
/// Mirrors `_VAD_THRESHOLD_RMS` from `pulsescribe_windows.py`.
pub const SPEECH_VAD_THRESHOLD_RMS: f32 = 0.002;

/// Polling cadence of the blocking audio drain loop.
pub const CAPTURE_POLL_INTERVAL: Duration = Duration::from_millis(20);

/// Throttle interval for emitting [`PipelineEvent::AudioLevel`] (~30 Hz).
pub const AUDIO_LEVEL_EMIT_INTERVAL: Duration = Duration::from_millis(33);

/// Safety cap on buffered 16 kHz mono samples during batch capture (~10 minutes, ~38 MiB).
pub const MAX_BATCH_CAPTURE_SAMPLES: usize = (TARGET_SAMPLE_RATE as usize) * 60 * 10;

const STREAM_AUDIO_CHANNEL_CAPACITY: usize = 64;
const STREAM_EVENT_CHANNEL_CAPACITY: usize = 32;

static TEMP_WAV_SEQ: AtomicU64 = AtomicU64::new(0);

/// Live mono audio source owned exclusively by the capture thread.
pub trait AudioSource {
    fn sample_rate(&self) -> u32;
    fn drain_into(&mut self, out: &mut Vec<f32>) -> usize;
    fn latest_rms(&self) -> f32;
    fn take_error(&self) -> Option<String>;
    /// Stop buffering new samples so a final tail drain does not race with incoming audio.
    fn disarm(&mut self) {}
}

/// Factory creating an [`AudioSource`] on the capture thread.
///
/// Keeping `AudioSource` construction inside the capture thread ensures non-`Send` OS audio
/// handles (such as `cpal::Stream` on some targets) never cross thread boundaries.
pub trait AudioSourceFactory: Send + Sync {
    fn open(&self, device_name: Option<&str>) -> Result<Box<dyn AudioSource>, AudioError>;
}

struct CpalAudioSource(CaptureStream);

impl AudioSource for CpalAudioSource {
    fn sample_rate(&self) -> u32 {
        self.0.sample_rate()
    }

    fn drain_into(&mut self, out: &mut Vec<f32>) -> usize {
        self.0.reader_mut().drain_into(out)
    }

    fn latest_rms(&self) -> f32 {
        self.0.latest_rms()
    }

    fn take_error(&self) -> Option<String> {
        self.0.take_stream_error()
    }

    fn disarm(&mut self) {
        self.0.disarm();
    }
}

/// Production [`AudioSourceFactory`] backed by [`CaptureStream`] (`cpal`).
#[derive(Debug, Default, Clone, Copy)]
pub struct CpalAudioSourceFactory;

impl AudioSourceFactory for CpalAudioSourceFactory {
    fn open(&self, device_name: Option<&str>) -> Result<Box<dyn AudioSource>, AudioError> {
        let config = CaptureConfig {
            device_name: device_name.map(ToOwned::to_owned),
            initially_armed: true,
            ..CaptureConfig::default()
        };
        let stream = CaptureStream::open(config)?;
        Ok(Box::new(CpalAudioSource(stream)))
    }
}

/// Normalized audio payload passed to a [`LocalTranscriber`] implementation (`crates/local-asr`).
#[derive(Debug, Clone)]
pub struct LocalTranscriptionRequest {
    pub samples_16k: Vec<f32>,
    pub model: Option<String>,
    pub language: Option<String>,
    pub keywords: Vec<String>,
    /// Cooperative cancellation flag set when the dictation run is cancelled or the daemon shuts down.
    pub cancel_flag: Arc<AtomicBool>,
}

/// Extension point for local ASR engines (`whisper-rs`, `sherpa-onnx`) wired in Phase 4.
pub trait LocalTranscriber: Send + Sync {
    fn transcribe(&self, request: LocalTranscriptionRequest) -> Result<String, DaemonStatusError>;
}

/// Optional endpoint overrides for cloud transcription and LLM refinement (defaults to official URLs).
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct ProviderEndpoints {
    pub openai_base_url: Option<String>,
    pub groq_base_url: Option<String>,
    pub deepgram_rest_base_url: Option<String>,
    pub deepgram_ws_url: Option<String>,
    pub refine_base_url: Option<String>,
}

impl ProviderEndpoints {
    fn openai_url(&self) -> &str {
        self.openai_base_url
            .as_deref()
            .unwrap_or(OPENAI_API_BASE_URL)
    }

    fn groq_url(&self) -> &str {
        self.groq_base_url.as_deref().unwrap_or(GROQ_API_BASE_URL)
    }

    fn deepgram_rest_url(&self) -> &str {
        self.deepgram_rest_base_url
            .as_deref()
            .unwrap_or(DEEPGRAM_API_BASE_URL)
    }

    fn deepgram_ws_url(&self) -> &str {
        self.deepgram_ws_url.as_deref().unwrap_or(DEEPGRAM_WS_URL)
    }

    fn refine_url(&self, kind: RefineProviderKind) -> &str {
        self.refine_base_url
            .as_deref()
            .unwrap_or_else(|| kind.default_base_url())
    }
}

struct WarmDeepgramSlot {
    generation: u64,
    ws_url: String,
    api_key: String,
    manager: DeepgramWarmConnectionManager,
}

#[derive(Default)]
struct WarmDeepgramState {
    generation: u64,
    slot: Option<WarmDeepgramSlot>,
}

#[derive(Default)]
struct RewarmState {
    shutting_down: bool,
    task: Option<JoinHandle<()>>,
}

#[derive(Default)]
struct SessionRegistry {
    shutting_down: bool,
    sessions: Vec<JoinHandle<()>>,
    detached_blocking: Vec<JoinHandle<()>>,
}

struct LiveBackendState {
    http_client: Client,
    permissions: Arc<dyn PermissionsChecker>,
    audio_factory: Arc<dyn AudioSourceFactory>,
    local_transcriber: Option<Arc<dyn LocalTranscriber>>,
    vocabulary_store: Arc<VocabularyStore>,
    prompts_store: Arc<PromptsStore>,
    endpoints: ProviderEndpoints,
    warm_deepgram: AsyncMutex<WarmDeepgramState>,
    rewarm_state: AsyncMutex<RewarmState>,
    registry: Mutex<SessionRegistry>,
    shutdown_tx: watch::Sender<bool>,
}

/// Production [`DictationBackend`] wiring live microphone capture, Deepgram streaming with
/// warm-connection reuse, cloud REST providers, pluggable local ASR, and optional LLM refinement.
#[derive(Clone)]
pub struct LiveDictationBackend {
    inner: Arc<LiveBackendState>,
}

impl fmt::Debug for LiveDictationBackend {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("LiveDictationBackend")
            .field(
                "config_dir",
                &self.inner.vocabulary_store.paths().config_dir(),
            )
            .field(
                "has_local_transcriber",
                &self.inner.local_transcriber.is_some(),
            )
            .field("endpoints", &self.inner.endpoints)
            .finish_non_exhaustive()
    }
}

impl LiveDictationBackend {
    /// Construct a live dictation backend using `cpal` microphone capture and official cloud URLs.
    pub fn new(
        config_dir: impl Into<PathBuf>,
        permissions: Arc<dyn PermissionsChecker>,
    ) -> Result<Self, ProviderError> {
        Self::with_components(
            config_dir,
            permissions,
            Arc::new(CpalAudioSourceFactory),
            None,
            ProviderEndpoints::default(),
        )
    }

    /// Construct a live dictation backend with custom audio source, local ASR engine, and/or
    /// endpoint overrides.
    pub fn with_components(
        config_dir: impl Into<PathBuf>,
        permissions: Arc<dyn PermissionsChecker>,
        audio_factory: Arc<dyn AudioSourceFactory>,
        local_transcriber: Option<Arc<dyn LocalTranscriber>>,
        endpoints: ProviderEndpoints,
    ) -> Result<Self, ProviderError> {
        let dir = config_dir.into();
        let http_client = build_http_client()?;
        let (shutdown_tx, _) = watch::channel(false);
        Ok(Self {
            inner: Arc::new(LiveBackendState {
                http_client,
                permissions,
                audio_factory,
                local_transcriber,
                vocabulary_store: Arc::new(VocabularyStore::new(&dir)),
                prompts_store: Arc::new(PromptsStore::new(&dir)),
                endpoints,
                warm_deepgram: AsyncMutex::new(WarmDeepgramState::default()),
                rewarm_state: AsyncMutex::new(RewarmState::default()),
                registry: Mutex::new(SessionRegistry::default()),
                shutdown_tx,
            }),
        })
    }

    /// Preconnect a warm Deepgram WebSocket if streaming + warm WebSocket are enabled and
    /// `DEEPGRAM_API_KEY` is configured; invalidates any existing warm slot otherwise.
    pub async fn warm_up_deepgram(&self, config: &DaemonConfig) -> Result<(), DaemonStatusError> {
        if *self.inner.shutdown_tx.borrow() {
            return Ok(());
        }
        if !config.uses_live_streaming() || !config.deepgram_warm_websocket {
            invalidate_warm_slot(&self.inner).await;
            return Ok(());
        }
        let Some(api_key) = config.provider_env.get("DEEPGRAM_API_KEY") else {
            invalidate_warm_slot(&self.inner).await;
            return Ok(());
        };
        let vocab_store = Arc::clone(&self.inner.vocabulary_store);
        let keywords = tokio::task::spawn_blocking(move || vocab_store.load_keywords())
            .await
            .unwrap_or_default();
        if *self.inner.shutdown_tx.borrow() {
            return Ok(());
        }
        let stream_cfg = build_deepgram_stream_config(config, keywords);
        let (manager, gen) = match get_or_create_deepgram_manager(
            &self.inner,
            self.inner.endpoints.deepgram_ws_url(),
            api_key,
        )
        .await
        {
            Ok(pair) => pair,
            Err(err) => {
                invalidate_warm_slot(&self.inner).await;
                return Err(map_provider_error(&err));
            }
        };
        warm_up_and_guard_generation(&self.inner, &manager, gen, &stream_cfg)
            .await
            .map_err(|err| map_provider_error(&err))
    }

    /// Synchronize the background Deepgram warm-connection slot with `config`:
    /// spawns a tracked warm-up when live streaming + warm WebSocket + API key are active, or
    /// tears down any previously warmed socket otherwise.
    pub async fn sync_deepgram_warm_state(&self, config: &DaemonConfig) {
        let should_warm = config.uses_live_streaming()
            && config.deepgram_warm_websocket
            && config.provider_env.get("DEEPGRAM_API_KEY").is_some();

        let mut rewarm = self.inner.rewarm_state.lock().await;
        if rewarm.shutting_down {
            return;
        }
        if let Some(prev) = rewarm.task.take() {
            prev.abort();
            let _ = prev.await;
        }

        if should_warm {
            let backend = self.clone();
            let cfg = config.clone();
            rewarm.task = Some(tokio::spawn(async move {
                if let Err(err) = backend.warm_up_deepgram(&cfg).await {
                    tracing::debug!(error = ?err, "Deepgram WebSocket warm-up skipped");
                }
            }));
        } else {
            drop(rewarm);
            invalidate_warm_slot(&self.inner).await;
        }
    }

    /// Abort any background re-warm task and close any prewarmed Deepgram WebSocket without
    /// shutting down the backend (e.g., after hot-reloading away from Deepgram streaming).
    pub async fn invalidate_warm_deepgram(&self) {
        {
            let mut rewarm = self.inner.rewarm_state.lock().await;
            if let Some(handle) = rewarm.task.take() {
                handle.abort();
                let _ = handle.await;
            }
        }
        invalidate_warm_slot(&self.inner).await;
    }

    /// Cancel all active sessions, await audio/network/local-ASR teardown, abort background
    /// re-warm, and gracefully close any prewarmed Deepgram socket.
    pub async fn shutdown(&self) {
        let _ = self.inner.shutdown_tx.send(true);
        let (session_handles, detached_handles) = {
            let mut reg = self
                .inner
                .registry
                .lock()
                .unwrap_or_else(|e| e.into_inner());
            reg.shutting_down = true;
            (
                std::mem::take(&mut reg.sessions),
                std::mem::take(&mut reg.detached_blocking),
            )
        };
        for handle in session_handles {
            let _ = handle.await;
        }
        // Drain any detached local-inference tasks pushed while sessions were unwinding.
        let extra_detached = {
            let mut reg = self
                .inner
                .registry
                .lock()
                .unwrap_or_else(|e| e.into_inner());
            std::mem::take(&mut reg.detached_blocking)
        };
        for handle in detached_handles.into_iter().chain(extra_detached) {
            let _ = handle.await;
        }
        {
            let mut rewarm = self.inner.rewarm_state.lock().await;
            rewarm.shutting_down = true;
            if let Some(handle) = rewarm.task.take() {
                handle.abort();
                let _ = handle.await;
            }
        }
        invalidate_warm_slot(&self.inner).await;
    }
}

impl DictationBackend for LiveDictationBackend {
    fn start_session(
        &self,
        run_id: u64,
        config: DaemonConfig,
        is_test: bool,
        app_detector: Arc<dyn AppDetector>,
        event_tx: mpsc::UnboundedSender<(u64, PipelineEvent)>,
    ) -> ActiveSession {
        let mut reg = self
            .inner
            .registry
            .lock()
            .unwrap_or_else(|e| e.into_inner());
        if reg.shutting_down {
            return ActiveSession::default();
        }
        let (stop_tx, stop_rx) = oneshot::channel();
        let (cancel_tx, cancel_rx) = oneshot::channel();
        let state = Arc::clone(&self.inner);
        let shutdown_rx = self.inner.shutdown_tx.subscribe();

        let handle = tokio::spawn(async move {
            run_session(
                state,
                run_id,
                config,
                is_test,
                app_detector,
                event_tx,
                stop_rx,
                cancel_rx,
                shutdown_rx,
            )
            .await;
        });

        reg.sessions.retain(|h| !h.is_finished());
        reg.sessions.push(handle);

        ActiveSession::new(stop_tx, cancel_tx)
    }
}

#[derive(Default)]
struct CaptureSignal {
    stop: bool,
    cancel: bool,
}

struct CaptureControl {
    state: Mutex<CaptureSignal>,
    condvar: Condvar,
}

impl CaptureControl {
    fn new() -> Self {
        Self {
            state: Mutex::new(CaptureSignal::default()),
            condvar: Condvar::new(),
        }
    }

    fn signal_stop(&self) {
        let mut guard = self.state.lock().unwrap_or_else(|e| e.into_inner());
        guard.stop = true;
        self.condvar.notify_all();
    }

    fn signal_cancel(&self) {
        let mut guard = self.state.lock().unwrap_or_else(|e| e.into_inner());
        guard.cancel = true;
        self.condvar.notify_all();
    }

    fn is_cancelled(&self) -> bool {
        self.state.lock().unwrap_or_else(|e| e.into_inner()).cancel
    }

    fn is_stopped(&self) -> bool {
        self.state.lock().unwrap_or_else(|e| e.into_inner()).stop
    }

    fn poll_signal(&self) -> (bool, bool) {
        let guard = self.state.lock().unwrap_or_else(|e| e.into_inner());
        (guard.cancel, guard.stop)
    }

    fn wait_timeout(&self, timeout: Duration) -> (bool, bool) {
        let guard = self.state.lock().unwrap_or_else(|e| e.into_inner());
        if guard.cancel || guard.stop {
            return (guard.cancel, guard.stop);
        }
        let (guard, _) = self
            .condvar
            .wait_timeout(guard, timeout)
            .unwrap_or_else(|e| e.into_inner());
        (guard.cancel, guard.stop)
    }

    /// Wait up to `timeout` for cancellation only (does not short-circuit on `stop`, avoiding
    /// busy-spins while flushing tail PCM chunks after `stop` into a full channel).
    fn wait_cancel_timeout(&self, timeout: Duration) -> bool {
        let guard = self.state.lock().unwrap_or_else(|e| e.into_inner());
        if guard.cancel {
            return true;
        }
        let (guard, _) = self
            .condvar
            .wait_timeout(guard, timeout)
            .unwrap_or_else(|e| e.into_inner());
        guard.cancel
    }
}

struct AbortOnDrop(JoinHandle<()>);

impl Drop for AbortOnDrop {
    fn drop(&mut self) {
        self.0.abort();
    }
}

#[allow(clippy::too_many_arguments)]
async fn run_session(
    state: Arc<LiveBackendState>,
    run_id: u64,
    config: DaemonConfig,
    is_test: bool,
    app_detector: Arc<dyn AppDetector>,
    event_tx: mpsc::UnboundedSender<(u64, PipelineEvent)>,
    stop_rx: oneshot::Receiver<()>,
    cancel_rx: oneshot::Receiver<()>,
    mut shutdown_rx: watch::Receiver<bool>,
) {
    let control = Arc::new(CaptureControl::new());
    let (cancelled_tx, mut cancelled_rx) = watch::channel(*shutdown_rx.borrow());
    if *shutdown_rx.borrow() {
        return;
    }

    // Bridge runtime oneshot control channels and backend shutdown onto both the blocking capture
    // Condvar and async watch channel.
    let bridge_control = Arc::clone(&control);
    let _bridge_guard = AbortOnDrop(tokio::spawn(async move {
        let mut stop_rx = stop_rx;
        let mut cancel_rx = cancel_rx;
        let mut stop_done = false;
        loop {
            tokio::select! {
                biased;
                res = shutdown_rx.changed() => {
                    if res.is_err() || *shutdown_rx.borrow() {
                        bridge_control.signal_cancel();
                        let _ = cancelled_tx.send(true);
                        break;
                    }
                }
                res = &mut cancel_rx => {
                    if res.is_ok() {
                        bridge_control.signal_cancel();
                        let _ = cancelled_tx.send(true);
                    }
                    break;
                }
                res = &mut stop_rx, if !stop_done => {
                    stop_done = true;
                    if res.is_ok() {
                        bridge_control.signal_stop();
                    }
                }
            }
        }
    }));

    if let Err(err) = config.validate() {
        emit_if_active(&event_tx, &cancelled_rx, run_id, PipelineEvent::Failed(err));
        return;
    }

    // Fast-fail missing API keys / invalid provider config / missing local engine before opening mic.
    let prepared_backend = match prepare_transcription_backend(&state, &config).await {
        Ok(backend) => backend,
        Err(err) => {
            emit_if_active(&event_tx, &cancelled_rx, run_id, PipelineEvent::Failed(err));
            return;
        }
    };

    // Check microphone permission off the Tokio worker thread.
    let perms = Arc::clone(&state.permissions);
    let mic_status = tokio::task::spawn_blocking(move || perms.microphone_status()).await;
    if is_cancelled(&cancelled_rx) {
        return;
    }
    match mic_status {
        Ok(MicrophonePermissionStatus::Denied | MicrophonePermissionStatus::Restricted) => {
            emit_if_active(
                &event_tx,
                &cancelled_rx,
                run_id,
                PipelineEvent::Failed(DaemonStatusError::new(
                    DaemonErrorCode::MicrophonePermission,
                    Some("Microphone permission denied".to_owned()),
                )),
            );
            return;
        }
        Ok(_) => {}
        Err(err) => {
            emit_if_active(
                &event_tx,
                &cancelled_rx,
                run_id,
                PipelineEvent::Failed(DaemonStatusError::new(
                    DaemonErrorCode::Unknown,
                    Some(format!("microphone permission task failed: {err}")),
                )),
            );
            return;
        }
    }

    let vocab_store = Arc::clone(&state.vocabulary_store);
    let keywords = tokio::task::spawn_blocking(move || vocab_store.load_keywords())
        .await
        .unwrap_or_default();
    if is_cancelled(&cancelled_rx) {
        return;
    }

    let outcome = match prepared_backend {
        PreparedBackend::DeepgramStream(manager, warm_gen) => {
            run_streaming_session(
                &state,
                run_id,
                &config,
                manager,
                warm_gen,
                keywords,
                &control,
                &mut cancelled_rx,
                &event_tx,
            )
            .await
        }
        PreparedBackend::Batch(batch_backend) => {
            run_batch_session(
                &state,
                run_id,
                &config,
                batch_backend,
                keywords,
                &control,
                &mut cancelled_rx,
                &event_tx,
            )
            .await
        }
    };

    if is_cancelled(&cancelled_rx) {
        return;
    }

    let (raw_transcript, post_stop_started) = match outcome {
        Ok(result) => result,
        Err(err) => {
            emit_if_active(&event_tx, &cancelled_rx, run_id, PipelineEvent::Failed(err));
            return;
        }
    };

    let final_transcript = if config.refine && !is_test && !raw_transcript.trim().is_empty() {
        emit_if_active(
            &event_tx,
            &cancelled_rx,
            run_id,
            PipelineEvent::RefiningStarted,
        );
        match refine_transcript(
            &state,
            &config,
            &raw_transcript,
            app_detector,
            &mut cancelled_rx,
        )
        .await
        {
            Some(refined) => refined,
            None => return, // Cancelled mid-refinement.
        }
    } else {
        raw_transcript
    };

    if is_cancelled(&cancelled_rx) {
        return;
    }

    let duration_secs = Some(post_stop_started.elapsed().as_secs_f64());
    let _ = event_tx.send((
        run_id,
        PipelineEvent::TranscriptReady {
            text: final_transcript,
            duration_secs,
        },
    ));
}

enum PreparedBackend {
    DeepgramStream(DeepgramWarmConnectionManager, u64),
    Batch(BatchBackend),
}

enum BatchBackend {
    Cloud(CloudProvider),
    Local(Arc<dyn LocalTranscriber>),
}

async fn prepare_transcription_backend(
    state: &LiveBackendState,
    config: &DaemonConfig,
) -> Result<PreparedBackend, DaemonStatusError> {
    if config.uses_live_streaming() {
        let api_key = config.provider_env.get("DEEPGRAM_API_KEY").ok_or_else(|| {
            DaemonStatusError::new(
                DaemonErrorCode::MissingApiKey,
                Some("DEEPGRAM_API_KEY nicht gesetzt".to_owned()),
            )
        })?;
        let (manager, warm_gen) =
            get_or_create_deepgram_manager(state, state.endpoints.deepgram_ws_url(), api_key)
                .await
                .map_err(|err| map_provider_error(&err))?;
        return Ok(PreparedBackend::DeepgramStream(manager, warm_gen));
    }

    match config.mode {
        DaemonMode::Local => {
            let local = state.local_transcriber.clone().ok_or_else(|| {
                DaemonStatusError::new(
                    DaemonErrorCode::MissingDependency,
                    Some("Local ASR engine is not configured in this build".to_owned()),
                )
            })?;
            Ok(PreparedBackend::Batch(BatchBackend::Local(local)))
        }
        DaemonMode::OpenAi => {
            let api_key = config.provider_env.get("OPENAI_API_KEY").ok_or_else(|| {
                DaemonStatusError::new(
                    DaemonErrorCode::MissingApiKey,
                    Some("OPENAI_API_KEY nicht gesetzt".to_owned()),
                )
            })?;
            let provider = OpenAiProvider::with_base_url(
                state.http_client.clone(),
                state.endpoints.openai_url(),
                api_key,
            )
            .map_err(|err| map_provider_error(&err))?;
            Ok(PreparedBackend::Batch(BatchBackend::Cloud(
                CloudProvider::OpenAi(provider),
            )))
        }
        DaemonMode::Groq => {
            let api_key = config.provider_env.get("GROQ_API_KEY").ok_or_else(|| {
                DaemonStatusError::new(
                    DaemonErrorCode::MissingApiKey,
                    Some("GROQ_API_KEY nicht gesetzt".to_owned()),
                )
            })?;
            let provider = GroqProvider::with_base_url(
                state.http_client.clone(),
                state.endpoints.groq_url(),
                api_key,
            )
            .map_err(|err| map_provider_error(&err))?;
            Ok(PreparedBackend::Batch(BatchBackend::Cloud(
                CloudProvider::Groq(provider),
            )))
        }
        DaemonMode::Deepgram => {
            let api_key = config.provider_env.get("DEEPGRAM_API_KEY").ok_or_else(|| {
                DaemonStatusError::new(
                    DaemonErrorCode::MissingApiKey,
                    Some("DEEPGRAM_API_KEY nicht gesetzt".to_owned()),
                )
            })?;
            let provider = DeepgramRestProvider::with_base_url(
                state.http_client.clone(),
                state.endpoints.deepgram_rest_url(),
                api_key,
            )
            .map_err(|err| map_provider_error(&err))?;
            Ok(PreparedBackend::Batch(BatchBackend::Cloud(
                CloudProvider::Deepgram(provider),
            )))
        }
        DaemonMode::OpenAiCompatible => {
            let base_url = config
                .provider_env
                .get("OPENAI_COMPATIBLE_BASE_URL")
                .ok_or_else(|| {
                    DaemonStatusError::new(
                        DaemonErrorCode::MissingApiKey,
                        Some("OPENAI_COMPATIBLE_BASE_URL nicht gesetzt".to_owned()),
                    )
                })?;
            let api_key = config
                .provider_env
                .get("OPENAI_COMPATIBLE_API_KEY")
                .map(ToOwned::to_owned);
            let default_model = config
                .provider_env
                .get("OPENAI_COMPATIBLE_MODEL")
                .map(ToOwned::to_owned);
            let provider = OpenAiCompatibleProvider::new(
                state.http_client.clone(),
                base_url,
                api_key,
                default_model,
            )
            .map_err(|err| map_provider_error(&err))?;
            Ok(PreparedBackend::Batch(BatchBackend::Cloud(
                CloudProvider::OpenAiCompatible(provider),
            )))
        }
    }
}

async fn invalidate_warm_slot(state: &LiveBackendState) {
    let previous = {
        let mut warm = state.warm_deepgram.lock().await;
        warm.generation = warm.generation.wrapping_add(1);
        warm.slot.take()
    };
    if let Some(old_slot) = previous {
        old_slot.manager.invalidate().await;
    }
}

async fn warm_up_and_guard_generation(
    state: &LiveBackendState,
    manager: &DeepgramWarmConnectionManager,
    expected_gen: u64,
    stream_cfg: &DeepgramStreamConfig,
) -> Result<(), ProviderError> {
    manager.warm_up(stream_cfg).await?;
    let still_current = {
        let warm = state.warm_deepgram.lock().await;
        !*state.shutdown_tx.borrow()
            && warm
                .slot
                .as_ref()
                .is_some_and(|slot| slot.generation == expected_gen)
    };
    if !still_current {
        manager.invalidate().await;
    }
    Ok(())
}

async fn get_or_create_deepgram_manager(
    state: &LiveBackendState,
    ws_url: &str,
    api_key: &str,
) -> Result<(DeepgramWarmConnectionManager, u64), ProviderError> {
    let (manager, gen, previous) = {
        let mut warm = state.warm_deepgram.lock().await;
        if let Some(existing) = warm.slot.as_ref() {
            if existing.ws_url == ws_url && existing.api_key == api_key {
                return Ok((existing.manager.clone(), existing.generation));
            }
        }
        let client = DeepgramStreamClient::with_ws_url(ws_url, api_key)?;
        let manager = DeepgramWarmConnectionManager::new(client);
        warm.generation = warm.generation.wrapping_add(1);
        let gen = warm.generation;
        let old = warm.slot.replace(WarmDeepgramSlot {
            generation: gen,
            ws_url: ws_url.to_owned(),
            api_key: api_key.to_owned(),
            manager: manager.clone(),
        });
        (manager, gen, old)
    };

    if let Some(old_slot) = previous {
        old_slot.manager.invalidate().await;
    }
    Ok((manager, gen))
}

#[allow(clippy::too_many_arguments)]
async fn run_streaming_session(
    state: &Arc<LiveBackendState>,
    run_id: u64,
    config: &DaemonConfig,
    manager: DeepgramWarmConnectionManager,
    warm_gen: u64,
    keywords: Vec<String>,
    control: &Arc<CaptureControl>,
    cancelled_rx: &mut watch::Receiver<bool>,
    event_tx: &mpsc::UnboundedSender<(u64, PipelineEvent)>,
) -> Result<(String, Instant), DaemonStatusError> {
    let stream_cfg = build_deepgram_stream_config(config, keywords);
    let (audio_tx, audio_rx) = mpsc::channel::<Bytes>(STREAM_AUDIO_CHANNEL_CAPACITY);
    let (ready_tx, ready_rx) = oneshot::channel::<Result<(), AudioError>>();

    let factory = Arc::clone(&state.audio_factory);
    let device_name = config.input_device.clone();
    let capture_control = Arc::clone(control);
    let capture_events = event_tx.clone();

    let mut capture_handle = tokio::task::spawn_blocking(move || {
        capture_streaming_loop(
            factory.as_ref(),
            device_name.as_deref(),
            &capture_control,
            run_id,
            &capture_events,
            ready_tx,
            audio_tx,
        )
    });

    // Wait for audio device open before touching Deepgram so mic failures never burn a socket.
    let open_result = tokio::select! {
        biased;
        _ = wait_cancelled(cancelled_rx) => {
            control.signal_cancel();
            let _ = capture_handle.await;
            return Ok((String::new(), Instant::now()));
        }
        res = ready_rx => res,
    };

    match open_result {
        Ok(Ok(())) => {}
        Ok(Err(audio_err)) => {
            let _ = capture_handle.await;
            return Err(map_audio_error(&audio_err));
        }
        Err(_) => {
            let _ = capture_handle.await;
            return Err(DaemonStatusError::new(
                DaemonErrorCode::MicrophoneUnavailable,
                Some("Audio capture worker exited before startup".to_owned()),
            ));
        }
    }

    let (dg_event_tx, mut dg_event_rx) =
        mpsc::channel::<DeepgramStreamEvent>(STREAM_EVENT_CHANNEL_CAPACITY);
    let forward_events = event_tx.clone();
    let forward_cancel = cancelled_rx.clone();
    let _interim_guard = AbortOnDrop(tokio::spawn(async move {
        while let Some(ev) = dg_event_rx.recv().await {
            if *forward_cancel.borrow() {
                break;
            }
            if let DeepgramStreamEvent::Interim(text) = ev {
                let _ = forward_events.send((run_id, PipelineEvent::InterimText(text)));
            }
        }
    }));

    let stream_manager = manager.clone();
    let stream_cfg_for_run = stream_cfg.clone();
    let mut stream_task = tokio::spawn(async move {
        stream_manager
            .transcribe_stream(&stream_cfg_for_run, audio_rx, Some(dg_event_tx))
            .await
    });

    let mut capture_done = false;
    let mut capture_result: Option<Result<Instant, AudioError>> = None;

    let stream_result = loop {
        tokio::select! {
            biased;
            _ = wait_cancelled(cancelled_rx) => {
                control.signal_cancel();
                stream_task.abort();
                let _ = stream_task.await;
                if !capture_done {
                    let _ = capture_handle.await;
                }
                return Ok((String::new(), Instant::now()));
            }
            res = &mut capture_handle, if !capture_done => {
                capture_done = true;
                match res {
                    Ok(Ok(stopped_at)) => capture_result = Some(Ok(stopped_at)),
                    Ok(Err(audio_err)) => {
                        capture_result = Some(Err(audio_err));
                    }
                    Err(join_err) => {
                        stream_task.abort();
                        let _ = stream_task.await;
                        return Err(DaemonStatusError::new(
                            DaemonErrorCode::MicrophoneUnavailable,
                            Some(format!("audio capture task panicked: {join_err}")),
                        ));
                    }
                }
            }
            res = &mut stream_task => {
                break res;
            }
        }
    };

    let ended_before_stop = !capture_done && !control.is_stopped();
    if !capture_done {
        // Stream ended before user stopped capture (e.g., server closed or auth failure).
        control.signal_cancel();
        let _ = capture_handle.await;
    }

    let post_stop_started = match capture_result {
        Some(Ok(ts)) => ts,
        Some(Err(audio_err)) => return Err(map_audio_error(&audio_err)),
        None => Instant::now(),
    };

    let result = match stream_result {
        Ok(Ok(res)) => res,
        Ok(Err(provider_err)) => return Err(map_provider_error(&provider_err)),
        Err(join_err) => {
            return Err(DaemonStatusError::new(
                DaemonErrorCode::Connection,
                Some(format!("Deepgram stream task failed: {join_err}")),
            ));
        }
    };

    // A clean server close before user stop would otherwise emit `TranscriptReady` while the
    // state machine is still `Listening`/`Recording` (which drops it and wedges the run).
    if ended_before_stop {
        return Err(DaemonStatusError::new(
            DaemonErrorCode::Connection,
            Some("Deepgram stream closed unexpectedly before recording stopped".to_owned()),
        ));
    }

    // Re-warm the next Deepgram WebSocket asynchronously so the next dictation starts instantly.
    if config.deepgram_warm_websocket && !is_cancelled(cancelled_rx) {
        schedule_deepgram_rewarm(Arc::clone(state), manager, warm_gen, stream_cfg).await;
    }

    Ok((result.transcript, post_stop_started))
}

async fn schedule_deepgram_rewarm(
    state: Arc<LiveBackendState>,
    manager: DeepgramWarmConnectionManager,
    warm_gen: u64,
    stream_cfg: DeepgramStreamConfig,
) {
    let mut rewarm = state.rewarm_state.lock().await;
    if rewarm.shutting_down {
        return;
    }
    if let Some(prev) = rewarm.task.take() {
        prev.abort();
        let _ = prev.await;
    }
    let state_for_task = Arc::clone(&state);
    rewarm.task = Some(tokio::spawn(async move {
        if let Err(err) =
            warm_up_and_guard_generation(&state_for_task, &manager, warm_gen, &stream_cfg).await
        {
            tracing::debug!(error = %err, "background Deepgram WebSocket re-warm failed");
        }
    }));
}

#[allow(clippy::too_many_arguments)]
async fn run_batch_session(
    state: &Arc<LiveBackendState>,
    run_id: u64,
    config: &DaemonConfig,
    backend: BatchBackend,
    keywords: Vec<String>,
    control: &Arc<CaptureControl>,
    cancelled_rx: &mut watch::Receiver<bool>,
    event_tx: &mpsc::UnboundedSender<(u64, PipelineEvent)>,
) -> Result<(String, Instant), DaemonStatusError> {
    let factory = Arc::clone(&state.audio_factory);
    let device_name = config.input_device.clone();
    let capture_control = Arc::clone(control);
    let capture_events = event_tx.clone();

    let mut capture_handle = tokio::task::spawn_blocking(move || {
        capture_batch_loop(
            factory.as_ref(),
            device_name.as_deref(),
            &capture_control,
            run_id,
            &capture_events,
        )
    });

    let captured = tokio::select! {
        biased;
        _ = wait_cancelled(cancelled_rx) => {
            control.signal_cancel();
            let _ = capture_handle.await;
            return Ok((String::new(), Instant::now()));
        }
        res = &mut capture_handle => res,
    };

    let (samples_16k, had_speech, post_stop_started) = match captured {
        Ok(Ok(batch)) => batch,
        Ok(Err(audio_err)) => return Err(map_audio_error(&audio_err)),
        Err(join_err) => {
            return Err(DaemonStatusError::new(
                DaemonErrorCode::MicrophoneUnavailable,
                Some(format!("audio capture task panicked: {join_err}")),
            ));
        }
    };

    if is_cancelled(cancelled_rx) {
        return Ok((String::new(), post_stop_started));
    }

    if samples_16k.is_empty() || !had_speech {
        return Ok((String::new(), post_stop_started));
    }

    match backend {
        BatchBackend::Local(local) => {
            let cancel_flag = Arc::new(AtomicBool::new(false));
            let request = LocalTranscriptionRequest {
                samples_16k,
                model: config.model.clone(),
                language: config.language.clone(),
                keywords,
                cancel_flag: Arc::clone(&cancel_flag),
            };
            let mut local_handle = tokio::task::spawn_blocking(move || local.transcribe(request));
            let res = tokio::select! {
                biased;
                _ = wait_cancelled(cancelled_rx) => {
                    cancel_flag.store(true, Ordering::Release);
                    // Retain the blocking task handle so `shutdown()` awaits local inference teardown.
                    let tracker = tokio::spawn(async move {
                        let _ = local_handle.await;
                    });
                    let mut reg = state.registry.lock().unwrap_or_else(|e| e.into_inner());
                    reg.detached_blocking.retain(|h| !h.is_finished());
                    reg.detached_blocking.push(tracker);
                    return Ok((String::new(), post_stop_started));
                }
                res = &mut local_handle => res,
            };
            match res {
                Ok(Ok(text)) => Ok((text, post_stop_started)),
                Ok(Err(err)) => Err(err),
                Err(join_err) => Err(DaemonStatusError::new(
                    DaemonErrorCode::Unknown,
                    Some(format!("local transcription task panicked: {join_err}")),
                )),
            }
        }
        BatchBackend::Cloud(provider) => {
            let wav_guard =
                tokio::task::spawn_blocking(move || write_private_temp_wav(&samples_16k))
                    .await
                    .map_err(|err| {
                        DaemonStatusError::new(
                            DaemonErrorCode::Unknown,
                            Some(format!("temp WAV task panicked: {err}")),
                        )
                    })?
                    .map_err(|err| map_audio_error(&err))?;

            if is_cancelled(cancelled_rx) {
                wav_guard.remove_async().await;
                return Ok((String::new(), post_stop_started));
            }

            let mut request = TranscriptionRequest::new(wav_guard.path()).with_keywords(keywords);
            if let Some(model) = config.model.as_deref() {
                request = request.with_model(model);
            }
            if let Some(language) = config.language.as_deref() {
                request = request.with_language(language);
            }

            let transcribe_res = {
                let fut = provider.transcribe(&request);
                tokio::pin!(fut);
                tokio::select! {
                    biased;
                    _ = wait_cancelled(cancelled_rx) => None,
                    res = &mut fut => Some(res),
                }
            };
            // Drop `fut` above first so any open streaming file handle closes before unlinking.
            wav_guard.remove_async().await;

            match transcribe_res {
                None => Ok((String::new(), post_stop_started)),
                Some(res) => res
                    .map(|text| (text, post_stop_started))
                    .map_err(|err| map_provider_error(&err)),
            }
        }
    }
}

fn capture_streaming_loop(
    factory: &dyn AudioSourceFactory,
    device_name: Option<&str>,
    control: &CaptureControl,
    run_id: u64,
    event_tx: &mpsc::UnboundedSender<(u64, PipelineEvent)>,
    ready_tx: oneshot::Sender<Result<(), AudioError>>,
    audio_tx: mpsc::Sender<Bytes>,
) -> Result<Instant, AudioError> {
    let mut pcm_buf = Vec::with_capacity(4096);
    let (stopped_at, _had_speech) = capture_audio_loop(
        factory,
        device_name,
        control,
        run_id,
        event_tx,
        Some(ready_tx),
        |samples_16k| {
            pcm_buf.clear();
            f32_to_pcm16_le_into(samples_16k, &mut pcm_buf);
            send_pcm_chunk(Bytes::copy_from_slice(&pcm_buf), &audio_tx, control)
        },
    )?;
    Ok(stopped_at)
}

fn capture_batch_loop(
    factory: &dyn AudioSourceFactory,
    device_name: Option<&str>,
    control: &CaptureControl,
    run_id: u64,
    event_tx: &mpsc::UnboundedSender<(u64, PipelineEvent)>,
) -> Result<(Vec<f32>, bool, Instant), AudioError> {
    let mut samples_16k = Vec::with_capacity(TARGET_SAMPLE_RATE as usize * 4);
    let mut cap_warned = false;
    let (stopped_at, had_speech) = capture_audio_loop(
        factory,
        device_name,
        control,
        run_id,
        event_tx,
        None,
        |chunk_16k| {
            let remaining = MAX_BATCH_CAPTURE_SAMPLES.saturating_sub(samples_16k.len());
            if remaining == 0 {
                if !cap_warned {
                    cap_warned = true;
                    tracing::warn!(
                        max_samples = MAX_BATCH_CAPTURE_SAMPLES,
                        "batch recording reached maximum buffer duration; truncating further audio"
                    );
                }
            } else {
                let take = chunk_16k.len().min(remaining);
                samples_16k.extend_from_slice(&chunk_16k[..take]);
            }
            true
        },
    )?;
    if control.is_cancelled() {
        return Ok((Vec::new(), false, stopped_at));
    }
    Ok((samples_16k, had_speech, stopped_at))
}

fn capture_audio_loop<F>(
    factory: &dyn AudioSourceFactory,
    device_name: Option<&str>,
    control: &CaptureControl,
    run_id: u64,
    event_tx: &mpsc::UnboundedSender<(u64, PipelineEvent)>,
    ready_tx: Option<oneshot::Sender<Result<(), AudioError>>>,
    mut on_samples_16k: F,
) -> Result<(Instant, bool), AudioError>
where
    F: FnMut(&[f32]) -> bool,
{
    let mut source = match factory.open(device_name) {
        Ok(src) => src,
        Err(err) => {
            if let Some(tx) = ready_tx {
                let msg = err.to_string();
                let _ = tx.send(Err(err));
                return Err(AudioError::Capture(msg));
            }
            return Err(err);
        }
    };

    let sample_rate = source.sample_rate();
    let mut resampler = match MonoResampler::new(sample_rate, TARGET_SAMPLE_RATE) {
        Ok(r) => r,
        Err(err) => {
            if let Some(tx) = ready_tx {
                let msg = err.to_string();
                let _ = tx.send(Err(err));
                return Err(AudioError::Resample(msg));
            }
            return Err(err);
        }
    };

    if let Some(tx) = ready_tx {
        let _ = tx.send(Ok(()));
    }
    let _ = event_tx.send((run_id, PipelineEvent::CaptureStarted));

    let mut raw_buf = Vec::with_capacity(4096);
    let mut resampled_buf = Vec::with_capacity(2048);
    let mut speech_emitted = false;
    let mut last_level_at = Instant::now()
        .checked_sub(AUDIO_LEVEL_EMIT_INTERVAL)
        .unwrap_or_else(Instant::now);

    loop {
        if let Some(err) = source.take_error() {
            return Err(AudioError::Capture(err));
        }

        let (cancelled, stopped) = control.poll_signal();
        if cancelled {
            return Ok((Instant::now(), speech_emitted));
        }

        raw_buf.clear();
        let drained = source.drain_into(&mut raw_buf);
        if drained > 0 {
            let rms = effective_rms(source.latest_rms(), &raw_buf);
            maybe_emit_level_and_vad(
                run_id,
                rms,
                &mut speech_emitted,
                &mut last_level_at,
                event_tx,
            );

            resampled_buf.clear();
            resampler.push(&raw_buf, &mut resampled_buf)?;
            if !resampled_buf.is_empty() && !on_samples_16k(&resampled_buf) {
                return Ok((Instant::now(), speech_emitted));
            }
        }

        if stopped {
            let stopped_at = Instant::now();
            source.disarm();
            raw_buf.clear();
            if source.drain_into(&mut raw_buf) > 0 {
                let rms = effective_rms(source.latest_rms(), &raw_buf);
                maybe_emit_level_and_vad(
                    run_id,
                    rms,
                    &mut speech_emitted,
                    &mut last_level_at,
                    event_tx,
                );
                resampled_buf.clear();
                resampler.push(&raw_buf, &mut resampled_buf)?;
                if !resampled_buf.is_empty() {
                    let _ = on_samples_16k(&resampled_buf);
                }
            }
            resampled_buf.clear();
            resampler.flush(&mut resampled_buf)?;
            if !resampled_buf.is_empty() {
                let _ = on_samples_16k(&resampled_buf);
            }
            return Ok((stopped_at, speech_emitted));
        }

        if drained == 0 {
            let (cancelled, _) = control.wait_timeout(CAPTURE_POLL_INTERVAL);
            if cancelled {
                return Ok((Instant::now(), speech_emitted));
            }
        }
    }
}

fn send_pcm_chunk(
    mut chunk: Bytes,
    audio_tx: &mpsc::Sender<Bytes>,
    control: &CaptureControl,
) -> bool {
    loop {
        if control.is_cancelled() {
            return false;
        }
        match audio_tx.try_send(chunk) {
            Ok(()) => return true,
            Err(mpsc::error::TrySendError::Closed(_)) => return false,
            Err(mpsc::error::TrySendError::Full(returned)) => {
                chunk = returned;
                if control.wait_cancel_timeout(Duration::from_millis(5)) {
                    return false;
                }
            }
        }
    }
}

fn effective_rms(reported_rms: f32, chunk: &[f32]) -> f32 {
    if reported_rms.is_finite() && reported_rms > 0.0 {
        reported_rms
    } else {
        rms_f32(chunk)
    }
}

fn maybe_emit_level_and_vad(
    run_id: u64,
    rms: f32,
    speech_emitted: &mut bool,
    last_level_at: &mut Instant,
    event_tx: &mpsc::UnboundedSender<(u64, PipelineEvent)>,
) {
    if !*speech_emitted && rms > SPEECH_VAD_THRESHOLD_RMS {
        *speech_emitted = true;
        let _ = event_tx.send((run_id, PipelineEvent::SpeechDetected));
    }

    let now = Instant::now();
    if now.duration_since(*last_level_at) >= AUDIO_LEVEL_EMIT_INTERVAL {
        *last_level_at = now;
        let _ = event_tx.send((run_id, PipelineEvent::AudioLevel(rms)));
    }
}

async fn refine_transcript(
    state: &LiveBackendState,
    config: &DaemonConfig,
    transcript: &str,
    app_detector: Arc<dyn AppDetector>,
    cancelled_rx: &mut watch::Receiver<bool>,
) -> Option<String> {
    let provider = match build_refine_provider(state, config) {
        Ok(p) => p,
        Err(err) => {
            tracing::warn!(error = %err, "LLM refinement skipped; keeping original transcript");
            return Some(transcript.to_owned());
        }
    };

    let prompts_store = Arc::clone(&state.prompts_store);
    let context_load = tokio::task::spawn_blocking(move || {
        let frontmost = app_detector.frontmost_app();
        let prompts = prompts_store.load();
        (frontmost, prompts)
    });

    let (frontmost_app, prompts) = tokio::select! {
        biased;
        _ = wait_cancelled(cancelled_rx) => return None,
        res = context_load => match res {
            Ok(pair) => pair,
            Err(err) => {
                tracing::warn!(error = %err, "failed to load refinement context; keeping original");
                return Some(transcript.to_owned());
            }
        }
    };

    let env_app_contexts = config
        .provider_env
        .get("PULSESCRIBE_APP_CONTEXTS")
        .map(parse_app_contexts_json)
        .unwrap_or_default();
    let voice_commands = config
        .provider_env
        .get("PULSESCRIBE_VOICE_COMMANDS")
        .and_then(|v| parse_bool(Some(v)))
        .unwrap_or(true);

    let detected = detect_context_with(
        None,
        config.context.as_deref(),
        frontmost_app.as_deref(),
        &env_app_contexts,
        &prompts,
    );
    let instruction = resolve_refine_prompt(None, &detected, &prompts, voice_commands);

    let refine_res = tokio::select! {
        biased;
        _ = wait_cancelled(cancelled_rx) => return None,
        res = provider.refine_with_prompt(
            transcript,
            &instruction,
            config.refine_model.as_deref(),
        ) => res,
    };

    match refine_res {
        Ok(refined) if !refined.trim().is_empty() => Some(refined),
        Ok(_) => {
            tracing::warn!("LLM refinement returned empty text; keeping original transcript");
            Some(transcript.to_owned())
        }
        Err(err) => {
            tracing::warn!(error = %err, "LLM refinement failed; keeping original transcript");
            Some(transcript.to_owned())
        }
    }
}

fn build_refine_provider(
    state: &LiveBackendState,
    config: &DaemonConfig,
) -> Result<LlmRefineProvider, ProviderError> {
    let raw_provider = config
        .refine_provider
        .as_deref()
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .unwrap_or(DEFAULT_REFINE_PROVIDER.as_str());
    let kind: RefineProviderKind = raw_provider.parse()?;
    let api_key = config
        .provider_env
        .get(kind.api_key_env_var())
        .ok_or_else(|| {
            ProviderError::MissingConfig(format!("{} nicht gesetzt", kind.api_key_env_var()))
        })?;

    let mut provider = LlmRefineProvider::with_base_url(
        state.http_client.clone(),
        kind,
        state.endpoints.refine_url(kind),
        api_key,
    )?;

    if let Some(model) = config.refine_model.as_deref() {
        provider = provider.with_default_model(model)?;
    }

    if kind == RefineProviderKind::OpenRouter {
        if let Some(raw_order) = config.provider_env.get("OPENROUTER_PROVIDER_ORDER") {
            let allow_fallbacks = config
                .provider_env
                .get("OPENROUTER_ALLOW_FALLBACKS")
                .and_then(|v| parse_bool(Some(v)))
                .unwrap_or(true);
            provider = provider
                .with_openrouter_routing(OpenRouterRouting::parse(raw_order, allow_fallbacks));
        }
    }

    Ok(provider)
}

fn build_deepgram_stream_config(
    config: &DaemonConfig,
    keywords: Vec<String>,
) -> DeepgramStreamConfig {
    let mut stream_cfg = DeepgramStreamConfig::default()
        .with_model(config.model.as_deref().unwrap_or(DEFAULT_DEEPGRAM_MODEL))
        .with_sample_rate(TARGET_SAMPLE_RATE)
        .with_channels(TARGET_CHANNELS)
        .with_keywords(keywords)
        .with_timeouts(deepgram_timeouts_from_config(config));

    if let Some(lang) = config.language.as_deref() {
        stream_cfg = stream_cfg.with_language(lang);
    }
    stream_cfg
}

fn deepgram_timeouts_from_config(config: &DaemonConfig) -> DeepgramStreamTimeouts {
    let defaults = DeepgramStreamTimeouts::default();
    DeepgramStreamTimeouts {
        close: parse_bounded_duration(
            config
                .provider_env
                .get("PULSESCRIBE_DEEPGRAM_CLOSE_TIMEOUT"),
            defaults.close,
            Duration::ZERO,
            Duration::from_secs(10),
        ),
        tail_padding: parse_bounded_duration(
            config
                .provider_env
                .get("PULSESCRIBE_DEEPGRAM_TAIL_PADDING_SECONDS"),
            defaults.tail_padding,
            Duration::ZERO,
            Duration::from_secs(5),
        ),
        empty_finalize_grace: parse_bounded_duration(
            config
                .provider_env
                .get("PULSESCRIBE_DEEPGRAM_EMPTY_FINALIZE_GRACE_SECONDS"),
            defaults.empty_finalize_grace,
            Duration::ZERO,
            Duration::from_secs(5),
        ),
        keepalive_interval: parse_bounded_duration(
            config
                .provider_env
                .get("PULSESCRIBE_DEEPGRAM_KEEPALIVE_INTERVAL_SECONDS"),
            defaults.keepalive_interval,
            Duration::from_secs(1),
            Duration::from_secs(8),
        ),
        finalize_send: parse_bounded_duration(
            config
                .provider_env
                .get("PULSESCRIBE_DEEPGRAM_FINALIZE_SEND_TIMEOUT"),
            defaults.finalize_send,
            Duration::from_millis(50),
            Duration::from_secs(10),
        ),
        close_stream_send: parse_bounded_duration(
            config
                .provider_env
                .get("PULSESCRIBE_DEEPGRAM_CLOSE_STREAM_SEND_TIMEOUT"),
            defaults.close_stream_send,
            Duration::from_millis(50),
            Duration::from_secs(10),
        ),
        keepalive_send: parse_bounded_duration(
            config
                .provider_env
                .get("PULSESCRIBE_DEEPGRAM_KEEPALIVE_SEND_TIMEOUT"),
            defaults.keepalive_send,
            Duration::from_millis(50),
            Duration::from_secs(10),
        ),
        ..defaults
    }
}

fn parse_bounded_duration(
    raw: Option<&str>,
    default: Duration,
    min: Duration,
    max: Duration,
) -> Duration {
    raw.and_then(|s| s.trim().parse::<f64>().ok())
        .filter(|v| v.is_finite() && *v >= 0.0)
        .map(Duration::from_secs_f64)
        .map(|d| d.clamp(min, max))
        .unwrap_or(default)
}

struct TempWavGuard {
    path: Option<PathBuf>,
}

impl TempWavGuard {
    fn new(path: PathBuf) -> Self {
        Self { path: Some(path) }
    }

    fn path(&self) -> &Path {
        self.path.as_deref().expect("TempWavGuard path consumed")
    }

    async fn remove_async(mut self) {
        if let Some(path) = self.path.take() {
            let _ = tokio::task::spawn_blocking(move || remove_file_with_retry(&path)).await;
        }
    }
}

impl Drop for TempWavGuard {
    fn drop(&mut self) {
        if let Some(path) = self.path.take() {
            if let Ok(handle) = tokio::runtime::Handle::try_current() {
                handle.spawn_blocking(move || remove_file_with_retry(&path));
            } else {
                remove_file_with_retry(&path);
            }
        }
    }
}

fn remove_file_with_retry(path: &Path) {
    for attempt in 0..6 {
        match fs::remove_file(path) {
            Ok(()) => return,
            Err(err) if err.kind() == std::io::ErrorKind::NotFound => return,
            Err(err) if is_retryable_remove_error(&err) && attempt < 5 => {
                std::thread::sleep(Duration::from_millis(25 * (attempt + 1)));
            }
            Err(_) => return,
        }
    }
}

fn is_retryable_remove_error(err: &std::io::Error) -> bool {
    err.kind() == std::io::ErrorKind::PermissionDenied
        || matches!(err.raw_os_error(), Some(32) | Some(33))
}

fn write_private_temp_wav(samples_16k: &[f32]) -> Result<TempWavGuard, AudioError> {
    let wav_bytes = encode_wav_i16_bytes(samples_16k, TARGET_SAMPLE_RATE, TARGET_CHANNELS)?;
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos();
    let pid = std::process::id();

    for _ in 0..8 {
        let seq = TEMP_WAV_SEQ.fetch_add(1, Ordering::Relaxed);
        let path = std::env::temp_dir().join(format!("pulsescribe-rec-{pid}-{nanos}-{seq}.wav"));
        let mut opts = OpenOptions::new();
        opts.create_new(true).write(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            opts.mode(0o600);
        }
        match opts.open(&path) {
            Ok(mut file) => {
                let guard = TempWavGuard::new(path);
                file.write_all(&wav_bytes)?;
                file.flush()?;
                return Ok(guard);
            }
            Err(err) if err.kind() == std::io::ErrorKind::AlreadyExists => continue,
            Err(err) => return Err(AudioError::Io(err)),
        }
    }

    Err(AudioError::Io(std::io::Error::new(
        std::io::ErrorKind::AlreadyExists,
        "failed to allocate unique temporary WAV path",
    )))
}

fn map_provider_error(err: &ProviderError) -> DaemonStatusError {
    match err {
        ProviderError::MissingConfig(msg) => {
            DaemonStatusError::new(DaemonErrorCode::MissingApiKey, Some(msg.clone()))
        }
        ProviderError::InvalidInput(msg) => {
            DaemonStatusError::new(DaemonErrorCode::InvalidProvider, Some(msg.clone()))
        }
        ProviderError::ApiStatus { status, .. } if matches!(*status, 401 | 403) => {
            DaemonStatusError::new(DaemonErrorCode::MissingApiKey, Some(err.to_string()))
        }
        ProviderError::Http(http_err) if http_err.is_timeout() => {
            DaemonStatusError::new(DaemonErrorCode::Timeout, Some(http_err.to_string()))
        }
        ProviderError::WebSocket { detail, .. } => {
            let lower = detail.to_ascii_lowercase();
            if lower.contains("401")
                || lower.contains("403")
                || lower.contains("unauthorized")
                || lower.contains("forbidden")
            {
                DaemonStatusError::new(DaemonErrorCode::MissingApiKey, Some(err.to_string()))
            } else if lower.contains("timed out") || lower.contains("timeout") {
                DaemonStatusError::new(DaemonErrorCode::Timeout, Some(err.to_string()))
            } else {
                DaemonStatusError::new(DaemonErrorCode::Connection, Some(err.to_string()))
            }
        }
        ProviderError::Http(_)
        | ProviderError::ApiStatus { .. }
        | ProviderError::InvalidResponse { .. } => {
            DaemonStatusError::new(DaemonErrorCode::Connection, Some(err.to_string()))
        }
        ProviderError::Io(io_err) => {
            DaemonStatusError::new(DaemonErrorCode::Unknown, Some(io_err.to_string()))
        }
    }
}

fn map_audio_error(err: &AudioError) -> DaemonStatusError {
    let msg = err.to_string();
    match err {
        AudioError::DeviceNotFound(_) => {
            DaemonStatusError::new(DaemonErrorCode::MicrophoneUnavailable, Some(msg))
        }
        AudioError::Capture(detail) if is_permission_related_message(detail) => {
            DaemonStatusError::new(DaemonErrorCode::MicrophonePermission, Some(msg))
        }
        AudioError::Capture(_) => {
            DaemonStatusError::new(DaemonErrorCode::MicrophoneUnavailable, Some(msg))
        }
        _ => DaemonStatusError::new(DaemonErrorCode::Unknown, Some(msg)),
    }
}

fn is_cancelled(cancelled_rx: &watch::Receiver<bool>) -> bool {
    *cancelled_rx.borrow()
}

async fn wait_cancelled(cancelled_rx: &mut watch::Receiver<bool>) {
    if *cancelled_rx.borrow() {
        return;
    }
    while cancelled_rx.changed().await.is_ok() {
        if *cancelled_rx.borrow() {
            return;
        }
    }
    // Bridge exited without sending `true`; stay pending forever so sibling futures win.
    std::future::pending::<()>().await;
}

fn emit_if_active(
    event_tx: &mpsc::UnboundedSender<(u64, PipelineEvent)>,
    cancelled_rx: &watch::Receiver<bool>,
    run_id: u64,
    event: PipelineEvent,
) {
    if !*cancelled_rx.borrow() {
        let _ = event_tx.send((run_id, event));
    }
}
