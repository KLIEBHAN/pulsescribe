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
use pulsescribe_core::{
    apply_vocabulary_corrections, DaemonErrorCode, DaemonStatusError, PromptsStore, VocabularyStore,
};
use pulsescribe_local_asr::{
    resolve_model_spec, DownloadProgress, LocalAsrError, LocalAsrManager, LocalAsrRequest,
    LocalStreamingSession, ModelCache, ModelDownloader, ModelSpec,
};
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
use crate::runtime::{ActiveSession, DaemonHandle, DictationBackend, PipelineEvent};

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
const LOCAL_PREVIEW_CHANNEL_CAPACITY: usize = 16;

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

/// Observer notified when background local ASR model auto-download/preload updates overlay
/// loading state or model download progress.
pub trait LocalPreloadObserver: Send + Sync {
    /// Update `AppState::Loading` detail (`Some(text)`) or clear loading back to `Idle` (`None`).
    fn on_loading_status(&self, _detail: Option<String>) {}

    /// Report throttled byte-level progress during an automatic local model download.
    fn on_download_progress(&self, _progress: DownloadProgress) {}

    /// Report completion (`Ok(())`) or failure/cancellation (`Err(msg)`) of an automatic download.
    /// An empty error string indicates intentional cancellation (no error banner needed).
    fn on_download_finished(&self, _model_id: &str, _result: Result<(), &str>) {}
}

impl LocalPreloadObserver for DaemonHandle {
    fn on_loading_status(&self, detail: Option<String>) {
        match detail {
            Some(text) => self.set_loading(Some(text)),
            None => self.finish_loading(),
        }
    }
}

/// Extension point for local ASR engines (`whisper-rs`, `sherpa-onnx`) wired in Phase 4.
pub trait LocalTranscriber: Send + Sync {
    fn transcribe(&self, request: LocalTranscriptionRequest) -> Result<String, DaemonStatusError>;

    /// Return the curated offline [`ModelSpec`] that must be downloaded before `preload`/`transcribe`,
    /// or `Ok(None)` when already cached, custom file path, or test/mock implementation.
    fn missing_download_spec(
        &self,
        _model: Option<&str>,
    ) -> Result<Option<&'static ModelSpec>, DaemonStatusError> {
        Ok(None)
    }

    /// Optional shared [`ModelCache`] and [`ModelDownloader`] enabling automatic model downloads.
    fn auto_downloader(&self) -> Option<(ModelCache, ModelDownloader)> {
        None
    }

    /// Warm up (load and cache) the configured offline local model without transcribing.
    ///
    /// Returns `Ok(true)` when a model is loaded/ready, `Ok(false)` when preloading is skipped
    /// (for example, not downloaded yet, runtime feature not compiled, or cancelled), or `Err` on
    /// unexpected load failure.
    fn preload(
        &self,
        _model: Option<&str>,
        _language: Option<&str>,
        _cancel_flag: &AtomicBool,
    ) -> Result<bool, DaemonStatusError> {
        Ok(false)
    }

    /// Open a live local streaming preview session (e.g., Nemotron 3.5) for one dictation run.
    ///
    /// Returns `Ok(None)` when live local preview is unavailable or not downloaded; errors are
    /// logged and never abort offline dictation.
    fn open_streaming_preview(
        &self,
        _language: Option<&str>,
        _cancel_flag: &AtomicBool,
    ) -> Result<Option<Box<dyn LocalStreamingSession>>, LocalAsrError> {
        Ok(None)
    }

    /// Release any cached local model weights (offline + streaming preview) from RAM.
    fn unload(&self) {}

    /// Release only the cached local streaming preview model from RAM, keeping offline weights.
    fn unload_streaming_preview(&self) {}
}

/// Default [`LocalTranscriber`] backed by [`LocalAsrManager`] (`crates/local-asr`).
#[derive(Clone)]
pub struct LocalAsrManagerTranscriber {
    manager: LocalAsrManager,
    downloader: Option<ModelDownloader>,
}

impl LocalAsrManagerTranscriber {
    pub fn new(manager: LocalAsrManager) -> Self {
        let downloader = match ModelDownloader::new() {
            Ok(d) => Some(d),
            Err(err) => {
                tracing::warn!(
                    error = %err,
                    "failed to initialize local model auto-downloader; continuing without auto-download"
                );
                None
            }
        };
        Self {
            manager,
            downloader,
        }
    }

    pub fn with_downloader(manager: LocalAsrManager, downloader: ModelDownloader) -> Self {
        Self {
            manager,
            downloader: Some(downloader),
        }
    }

    pub fn manager(&self) -> &LocalAsrManager {
        &self.manager
    }
}

impl LocalTranscriber for LocalAsrManagerTranscriber {
    fn transcribe(&self, request: LocalTranscriptionRequest) -> Result<String, DaemonStatusError> {
        let local_req = LocalAsrRequest::new(request.samples_16k)
            .with_language(request.language)
            .with_keywords(request.keywords)
            .with_cancel_flag(request.cancel_flag);
        match self
            .manager
            .transcribe(request.model.as_deref(), &local_req)
        {
            Ok(text) => Ok(text),
            Err(LocalAsrError::Cancelled) => Ok(String::new()),
            Err(err) => Err(map_local_asr_error(&err)),
        }
    }

    fn missing_download_spec(
        &self,
        model: Option<&str>,
    ) -> Result<Option<&'static ModelSpec>, DaemonStatusError> {
        self.manager
            .missing_downloadable_spec(model)
            .map_err(|err| map_local_asr_error(&err))
    }

    fn auto_downloader(&self) -> Option<(ModelCache, ModelDownloader)> {
        self.downloader
            .as_ref()
            .map(|d| (self.manager.cache().clone(), d.clone()))
    }

    fn preload(
        &self,
        model: Option<&str>,
        language: Option<&str>,
        cancel_flag: &AtomicBool,
    ) -> Result<bool, DaemonStatusError> {
        match self.manager.preload(model, language, Some(cancel_flag)) {
            Ok(_) => Ok(true),
            Err(
                LocalAsrError::ModelNotDownloaded(_)
                | LocalAsrError::MissingRuntime(_)
                | LocalAsrError::Cancelled,
            ) => Ok(false),
            Err(err) => Err(map_local_asr_error(&err)),
        }
    }

    fn open_streaming_preview(
        &self,
        language: Option<&str>,
        cancel_flag: &AtomicBool,
    ) -> Result<Option<Box<dyn LocalStreamingSession>>, LocalAsrError> {
        self.manager
            .open_streaming_preview(language, Some(cancel_flag))
    }

    fn unload(&self) {
        self.manager.unload();
    }

    fn unload_streaming_preview(&self) {
        self.manager.unload_streaming_preview();
    }
}

fn map_local_asr_error(err: &LocalAsrError) -> DaemonStatusError {
    match err {
        LocalAsrError::MissingRuntime(msg) => {
            DaemonStatusError::new(DaemonErrorCode::MissingDependency, Some(msg.clone()))
        }
        LocalAsrError::ModelNotDownloaded(model) => DaemonStatusError::new(
            DaemonErrorCode::MissingDependency,
            Some(format!(
                "Lokales Modell '{model}' ist noch nicht heruntergeladen"
            )),
        ),
        LocalAsrError::UnknownModel(model) => DaemonStatusError::new(
            DaemonErrorCode::InvalidProvider,
            Some(format!("Unbekanntes lokales Modell: '{model}'")),
        ),
        LocalAsrError::InvalidModel(msg) => {
            DaemonStatusError::new(DaemonErrorCode::InvalidProvider, Some(msg.clone()))
        }
        LocalAsrError::Download(msg) => {
            DaemonStatusError::new(DaemonErrorCode::Connection, Some(msg.clone()))
        }
        LocalAsrError::Inference(msg) => {
            DaemonStatusError::new(DaemonErrorCode::Unknown, Some(msg.clone()))
        }
        LocalAsrError::Io(io_err) => {
            DaemonStatusError::new(DaemonErrorCode::Unknown, Some(io_err.to_string()))
        }
        LocalAsrError::Cancelled => DaemonStatusError::new(DaemonErrorCode::Unknown, None),
    }
}

/// Optional endpoint overrides for cloud transcription and LLM refinement (defaults to official URLs).
///
/// [`fmt::Debug`] prints only which overrides are set so query-string tokens can never leak into logs.
#[derive(Clone, Default, PartialEq, Eq)]
pub struct ProviderEndpoints {
    pub openai_base_url: Option<String>,
    pub groq_base_url: Option<String>,
    pub deepgram_rest_base_url: Option<String>,
    pub deepgram_ws_url: Option<String>,
    pub refine_base_url: Option<String>,
}

impl fmt::Debug for ProviderEndpoints {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("ProviderEndpoints")
            .field("openai_override", &self.openai_base_url.is_some())
            .field("groq_override", &self.groq_base_url.is_some())
            .field(
                "deepgram_rest_override",
                &self.deepgram_rest_base_url.is_some(),
            )
            .field("deepgram_ws_override", &self.deepgram_ws_url.is_some())
            .field("refine_override", &self.refine_base_url.is_some())
            .finish()
    }
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

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct DeepgramMaintenanceKey {
    keepalive_interval: Duration,
    keepalive_send: Duration,
    close: Duration,
}

impl DeepgramMaintenanceKey {
    fn from_timeouts(timeouts: &DeepgramStreamTimeouts) -> Self {
        Self {
            keepalive_interval: timeouts.keepalive_interval,
            keepalive_send: timeouts.keepalive_send,
            close: timeouts.close,
        }
    }
}

struct WarmDeepgramSlot {
    generation: u64,
    ws_url: String,
    api_key: String,
    maintenance: DeepgramMaintenanceKey,
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

#[derive(Debug, Clone, PartialEq, Eq)]
struct LocalPreloadKey {
    model: Option<String>,
    language: Option<String>,
}

impl LocalPreloadKey {
    fn from_config(config: &DaemonConfig) -> Self {
        let model = config
            .model
            .as_deref()
            .map(str::trim)
            .filter(|s| !s.is_empty())
            .map(ToOwned::to_owned);
        let language = config
            .language
            .as_deref()
            .map(str::trim)
            .filter(|s| !s.is_empty())
            .map(ToOwned::to_owned);
        Self { model, language }
    }

    fn matches_catalog_id(&self, model_id: &str) -> bool {
        if resolve_model_spec(self.model.as_deref()).is_some_and(|spec| spec.id == model_id) {
            return true;
        }
        self.model
            .as_deref()
            .is_some_and(|raw| raw.eq_ignore_ascii_case(model_id))
    }
}

struct DetachedPreload {
    key: LocalPreloadKey,
    handle: JoinHandle<()>,
}

#[derive(Default)]
struct LocalPreloadState {
    shutting_down: bool,
    generation: u64,
    desired: Option<LocalPreloadKey>,
    ready: bool,
    cancel_flag: Option<Arc<AtomicBool>>,
    task: Option<JoinHandle<()>>,
    superseded: Vec<DetachedPreload>,
    downloading_model_id: Option<String>,
    dictation_download: Option<(u64, String, Arc<AtomicBool>)>,
    loading_generation: Option<u64>,
    observer: Option<Arc<dyn LocalPreloadObserver>>,
}

impl LocalPreloadState {
    fn stash_superseded(&mut self, key: Option<LocalPreloadKey>, handle: Option<JoinHandle<()>>) {
        self.superseded.retain(|entry| !entry.handle.is_finished());
        if let (Some(key), Some(handle)) = (key, handle) {
            if !handle.is_finished() {
                self.superseded.push(DetachedPreload { key, handle });
            }
        }
    }
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
    // Lock order: `loading_emit_lock` -> `local_preload` (never lock `loading_emit_lock` while
    // holding `local_preload`). Serializes `loading_generation` updates with observer emission so
    // an older `SetLoading` can never land after a newer `FinishLoading` (or vice versa).
    loading_emit_lock: Mutex<()>,
    local_preload: Mutex<LocalPreloadState>,
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
    /// Construct a live dictation backend using `cpal` microphone capture, the default
    /// [`LocalAsrManager`], and official cloud URLs.
    pub fn new(
        config_dir: impl Into<PathBuf>,
        permissions: Arc<dyn PermissionsChecker>,
    ) -> Result<Self, ProviderError> {
        let dir = config_dir.into();
        let manager = LocalAsrManager::from_config_dir(&dir);
        Self::with_local_asr_manager(dir, permissions, manager)
    }

    /// Construct a live dictation backend wired to an externally shared [`LocalAsrManager`]
    /// (e.g., shared between the desktop UI's model manager and live dictation).
    pub fn with_local_asr_manager(
        config_dir: impl Into<PathBuf>,
        permissions: Arc<dyn PermissionsChecker>,
        manager: LocalAsrManager,
    ) -> Result<Self, ProviderError> {
        Self::with_components(
            config_dir,
            permissions,
            Arc::new(CpalAudioSourceFactory),
            Some(Arc::new(LocalAsrManagerTranscriber::new(manager))),
            ProviderEndpoints::default(),
        )
    }

    /// Construct a live dictation backend sharing both [`LocalAsrManager`] and [`ModelDownloader`]
    /// with the desktop shell so automatic and manual model downloads serialize per model.
    pub fn with_local_asr_and_downloader(
        config_dir: impl Into<PathBuf>,
        permissions: Arc<dyn PermissionsChecker>,
        manager: LocalAsrManager,
        downloader: ModelDownloader,
    ) -> Result<Self, ProviderError> {
        Self::with_components(
            config_dir,
            permissions,
            Arc::new(CpalAudioSourceFactory),
            Some(Arc::new(LocalAsrManagerTranscriber::with_downloader(
                manager, downloader,
            ))),
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
                loading_emit_lock: Mutex::new(()),
                local_preload: Mutex::new(LocalPreloadState::default()),
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
        let maintenance = DeepgramMaintenanceKey::from_timeouts(&stream_cfg.timeouts);
        let (manager, gen) = match get_or_create_deepgram_manager(
            &self.inner,
            self.inner.endpoints.deepgram_ws_url(),
            api_key,
            maintenance,
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

    /// Register an observer receiving overlay loading status and model download progress updates
    /// from background local ASR auto-download/preloading.
    pub fn set_local_preload_observer(&self, observer: Arc<dyn LocalPreloadObserver>) {
        let mut preload = self
            .inner
            .local_preload
            .lock()
            .unwrap_or_else(|e| e.into_inner());
        preload.observer = Some(observer);
    }

    /// Return the catalog model ID currently being downloaded automatically (by background preload
    /// or an active on-demand dictation download).
    pub fn active_local_download_model_id(&self) -> Option<String> {
        let preload = self
            .inner
            .local_preload
            .lock()
            .unwrap_or_else(|e| e.into_inner());
        preload.downloading_model_id.clone().or_else(|| {
            preload
                .dictation_download
                .as_ref()
                .map(|(_, id, _)| id.clone())
        })
    }

    /// Cancel an active automatic local model download/preload targeting `model_id` (e.g., when
    /// the user clicks Cancel in Settings → Local Models). Returns `true` if a matching task was active.
    pub fn cancel_local_preload_if_matching(&self, model_id: &str) -> bool {
        let _emit_guard = self
            .inner
            .loading_emit_lock
            .lock()
            .unwrap_or_else(|e| e.into_inner());
        let (cancelled, clear_observer) = {
            let mut preload = self
                .inner
                .local_preload
                .lock()
                .unwrap_or_else(|e| e.into_inner());
            let cancelled_dictation =
                if let Some((_, ref dl_id, ref dl_cancel)) = preload.dictation_download {
                    if dl_id == model_id {
                        dl_cancel.store(true, Ordering::Release);
                        true
                    } else {
                        false
                    }
                } else {
                    false
                };
            let matches_dl = preload.downloading_model_id.as_deref() == Some(model_id);
            let matches_desired = preload
                .desired
                .as_ref()
                .is_some_and(|key| key.matches_catalog_id(model_id));
            let active = preload.task.as_ref().is_some_and(|h| !h.is_finished());
            if !(active && (matches_dl || matches_desired)) {
                (cancelled_dictation, None)
            } else {
                preload.generation = preload.generation.wrapping_add(1);
                let prev_key = preload.desired.take();
                preload.ready = false;
                preload.downloading_model_id = None;
                if let Some(cancel) = preload.cancel_flag.take() {
                    cancel.store(true, Ordering::Release);
                }
                let prev_task = preload.task.take();
                preload.stash_superseded(prev_key, prev_task);
                let clear_obs = preload
                    .loading_generation
                    .take()
                    .and_then(|_| preload.observer.clone());
                (true, clear_obs)
            }
        };
        if let Some(observer) = clear_observer {
            observer.on_loading_status(None);
        }
        cancelled
    }

    /// Synchronize background local ASR model auto-download & preloading with `config`
    /// (non-blocking, coalesced).
    ///
    /// - In [`DaemonMode::Local`]: if the configured curated offline model is not yet cached and
    ///   its runtime is compiled in, automatically downloads it with `AppState::Loading` progress
    ///   updates before warming it in RAM; releases any cached Nemotron streaming preview when
    ///   `config.streaming` is disabled.
    /// - In cloud modes: cancels any in-flight download/preload and unloads cached local weights.
    pub fn sync_local_preload_state(&self, config: &DaemonConfig) {
        let Some(local) = self.inner.local_transcriber.clone() else {
            return;
        };
        if *self.inner.shutdown_tx.borrow() {
            return;
        }
        if config.mode != DaemonMode::Local {
            self.unload_local_model();
            return;
        }
        if !config.streaming {
            local.unload_streaming_preview();
        }

        let key = LocalPreloadKey::from_config(config);
        let mut preload = self
            .inner
            .local_preload
            .lock()
            .unwrap_or_else(|e| e.into_inner());
        if preload.shutting_down {
            return;
        }
        if preload.desired.as_ref() == Some(&key)
            && (preload.ready || preload.task.as_ref().is_some_and(|h| !h.is_finished()))
        {
            return;
        }
        if let Some(prev_cancel) = preload.cancel_flag.take() {
            prev_cancel.store(true, Ordering::Release);
        }
        let prev_key = preload.desired.take();
        let prev_task = preload.task.take();
        preload.stash_superseded(prev_key, prev_task);
        preload.downloading_model_id = None;

        preload.generation = preload.generation.wrapping_add(1);
        let my_gen = preload.generation;
        if preload.loading_generation.is_some() {
            // Transfer loading-overlay ownership to `my_gen` so the replacement task either
            // updates it smoothly for a new download or clears it immediately if already cached.
            preload.loading_generation = Some(my_gen);
        }
        let cancel_flag = Arc::new(AtomicBool::new(false));
        preload.desired = Some(key.clone());
        preload.ready = false;
        preload.cancel_flag = Some(Arc::clone(&cancel_flag));

        let state_for_task = Arc::clone(&self.inner);
        let model = key.model.clone();
        let language = key.language.clone();
        let cancel_for_task = Arc::clone(&cancel_flag);
        // Spawn and store `preload.task` under the same `local_preload` lock so concurrent
        // coalescing, `unload_local_model_if_matching`, and `shutdown()` always observe the task.
        let task = tokio::spawn(async move {
            let model_for_log = model.clone();

            // 1. Auto-download missing curated offline models before loading weights.
            let local_for_probe = Arc::clone(&local);
            let model_for_probe = model.clone();
            let probe_res = tokio::task::spawn_blocking(move || {
                local_for_probe.missing_download_spec(model_for_probe.as_deref())
            })
            .await;

            let missing_spec = match probe_res {
                Ok(Ok(spec)) => spec,
                Ok(Err(err)) => {
                    clear_loading_for_gen(&state_for_task, my_gen);
                    tracing::debug!(
                        model = model_for_log.as_deref().unwrap_or("default"),
                        error = ?err,
                        "local ASR preload skipped (runtime unavailable or invalid model)"
                    );
                    return;
                }
                Err(join_err) => {
                    clear_loading_for_gen(&state_for_task, my_gen);
                    tracing::warn!(error = %join_err, "local ASR cache probe task panicked");
                    return;
                }
            };

            let mut downloaded_now = false;
            if let Some(spec) = missing_spec {
                if let Some((cache, downloader)) = local.auto_downloader() {
                    if cancel_for_task.load(Ordering::Acquire) {
                        clear_loading_for_gen(&state_for_task, my_gen);
                        return;
                    }
                    publish_loading_for_gen(
                        &state_for_task,
                        my_gen,
                        &cancel_for_task,
                        format!("Downloading {}", spec.id),
                    );
                    tracing::info!(
                        model = spec.id,
                        "local ASR model not cached yet; downloading automatically"
                    );

                    let mut last_label = format!("Downloading {}", spec.id);
                    let owned_transfer = Arc::new(AtomicBool::new(false));
                    let owned_start = Arc::clone(&owned_transfer);
                    let state_start = Arc::clone(&state_for_task);
                    let state_cb = Arc::clone(&state_for_task);
                    let cancel_cb = Arc::clone(&cancel_for_task);
                    let dl_res = downloader
                        .ensure_downloaded_observing(
                            &cache,
                            spec,
                            &cancel_for_task,
                            move || {
                                owned_start.store(true, Ordering::Release);
                                set_downloading_model_for_gen(&state_start, my_gen, Some(spec.id));
                            },
                            |progress, from_peer| {
                                let label = format_model_download_status(spec.id, &progress);
                                if label != last_label {
                                    last_label = label.clone();
                                    publish_loading_for_gen(&state_cb, my_gen, &cancel_cb, label);
                                }
                                if !from_peer {
                                    notify_download_progress(&state_cb, my_gen, progress);
                                }
                            },
                        )
                        .await;

                    let was_owner = owned_transfer.load(Ordering::Acquire);
                    match dl_res {
                        Ok(_) => {
                            downloaded_now = true;
                            if was_owner {
                                finish_preload_download(&state_for_task, my_gen, spec.id, Ok(()));
                            }
                            publish_loading_for_gen(
                                &state_for_task,
                                my_gen,
                                &cancel_for_task,
                                format!("Loading {}", spec.id),
                            );
                        }
                        Err(LocalAsrError::Cancelled) => {
                            if was_owner {
                                finish_preload_download(&state_for_task, my_gen, spec.id, Err(""));
                            }
                            clear_loading_for_gen(&state_for_task, my_gen);
                            return;
                        }
                        Err(err) => {
                            let msg = err.to_string();
                            tracing::warn!(
                                model = spec.id,
                                error = %msg,
                                "automatic local ASR model download failed"
                            );
                            if was_owner {
                                finish_preload_download(
                                    &state_for_task,
                                    my_gen,
                                    spec.id,
                                    Err(&msg),
                                );
                            }
                            clear_loading_for_gen(&state_for_task, my_gen);
                            return;
                        }
                    }
                }
            }

            if !downloaded_now {
                clear_loading_for_gen(&state_for_task, my_gen);
            }
            if cancel_for_task.load(Ordering::Acquire) {
                clear_loading_for_gen(&state_for_task, my_gen);
                return;
            }

            // 2. Load the offline model into RAM.
            let cancel_for_blocking = Arc::clone(&cancel_for_task);
            let res = tokio::task::spawn_blocking(move || {
                local.preload(model.as_deref(), language.as_deref(), &cancel_for_blocking)
            })
            .await;

            clear_loading_for_gen(&state_for_task, my_gen);

            match res {
                Ok(Ok(true)) => {
                    let mut preload = state_for_task
                        .local_preload
                        .lock()
                        .unwrap_or_else(|e| e.into_inner());
                    if !preload.shutting_down
                        && preload.generation == my_gen
                        && !cancel_for_task.load(Ordering::Acquire)
                    {
                        preload.ready = true;
                        tracing::info!(
                            model = model_for_log.as_deref().unwrap_or("default"),
                            "local ASR model preloaded"
                        );
                    }
                }
                Ok(Ok(false)) => {
                    tracing::debug!(
                        model = model_for_log.as_deref().unwrap_or("default"),
                        "local ASR preload skipped (not downloaded, runtime unavailable, or cancelled)"
                    );
                }
                Ok(Err(err)) => {
                    tracing::warn!(
                        model = model_for_log.as_deref().unwrap_or("default"),
                        error = ?err,
                        "local ASR preload failed"
                    );
                }
                Err(join_err) => {
                    tracing::warn!(error = %join_err, "local ASR preload task panicked");
                }
            }
        });
        preload.task = Some(task);
    }

    /// Cancel any in-flight local download/preload and release cached local ASR models
    /// (offline + streaming) from RAM (e.g., after switching away from local mode or clicking Unload).
    pub fn unload_local_model(&self) {
        {
            let _emit_guard = self
                .inner
                .loading_emit_lock
                .lock()
                .unwrap_or_else(|e| e.into_inner());
            let clear_observer = {
                let mut preload = self
                    .inner
                    .local_preload
                    .lock()
                    .unwrap_or_else(|e| e.into_inner());
                preload.generation = preload.generation.wrapping_add(1);
                let prev_key = preload.desired.take();
                preload.ready = false;
                preload.downloading_model_id = None;
                if let Some(cancel) = preload.cancel_flag.take() {
                    cancel.store(true, Ordering::Release);
                }
                let prev_task = preload.task.take();
                preload.stash_superseded(prev_key, prev_task);
                preload
                    .loading_generation
                    .take()
                    .and_then(|_| preload.observer.clone())
            };
            if let Some(observer) = clear_observer {
                observer.on_loading_status(None);
            }
        }
        if let Some(local) = &self.inner.local_transcriber {
            local.unload();
        }
    }

    /// Release only the cached local streaming preview model (Nemotron) without evicting the
    /// active offline engine.
    pub fn unload_local_streaming_preview(&self) {
        if let Some(local) = &self.inner.local_transcriber {
            local.unload_streaming_preview();
        }
    }

    /// Cancel and await any active or superseded in-flight local download/preload targeting `model_id`,
    /// unloading cached weights when `model_id` was the active preload target (prevents Windows
    /// sharing violations when deleting model files without evicting an unrelated newer model).
    pub async fn unload_local_model_if_matching(&self, model_id: &str) -> bool {
        let (active_matched, superseded_matched, tasks_to_await) = {
            let _emit_guard = self
                .inner
                .loading_emit_lock
                .lock()
                .unwrap_or_else(|e| e.into_inner());
            let (active_matched, superseded_matched, tasks_to_await, clear_observer) = {
                let mut preload = self
                    .inner
                    .local_preload
                    .lock()
                    .unwrap_or_else(|e| e.into_inner());
                preload
                    .superseded
                    .retain(|entry| !entry.handle.is_finished());

                let mut tasks = Vec::new();
                let mut active_matched = false;
                let mut superseded_matched = false;
                let mut clear_observer = None;

                if let Some((_, ref dl_id, ref dl_cancel)) = preload.dictation_download {
                    if dl_id == model_id {
                        dl_cancel.store(true, Ordering::Release);
                    }
                }

                if preload
                    .desired
                    .as_ref()
                    .is_some_and(|key| key.matches_catalog_id(model_id))
                {
                    active_matched = true;
                    preload.generation = preload.generation.wrapping_add(1);
                    preload.desired = None;
                    preload.ready = false;
                    preload.downloading_model_id = None;
                    clear_observer = preload
                        .loading_generation
                        .take()
                        .and_then(|_| preload.observer.clone());
                    if let Some(cancel) = preload.cancel_flag.take() {
                        cancel.store(true, Ordering::Release);
                    }
                    if let Some(task) = preload.task.take() {
                        tasks.push(task);
                    }
                }

                let mut remaining = Vec::with_capacity(preload.superseded.len());
                for entry in std::mem::take(&mut preload.superseded) {
                    if entry.key.matches_catalog_id(model_id) {
                        superseded_matched = true;
                        tasks.push(entry.handle);
                    } else {
                        remaining.push(entry);
                    }
                }
                preload.superseded = remaining;

                (active_matched, superseded_matched, tasks, clear_observer)
            };
            if let Some(observer) = clear_observer {
                observer.on_loading_status(None);
            }
            (active_matched, superseded_matched, tasks_to_await)
        };
        for task in tasks_to_await {
            let _ = task.await;
        }
        if active_matched {
            if let Some(local) = &self.inner.local_transcriber {
                local.unload();
            }
        }
        active_matched || superseded_matched
    }

    /// Cancel all active sessions, await audio/network/local-ASR teardown, abort background
    /// re-warm and preloads, and gracefully close any prewarmed Deepgram socket.
    pub async fn shutdown(&self) {
        self.inner.shutdown_tx.send_replace(true);
        let preload_tasks = {
            let _emit_guard = self
                .inner
                .loading_emit_lock
                .lock()
                .unwrap_or_else(|e| e.into_inner());
            let (preload_tasks, clear_observer) = {
                let mut preload = self
                    .inner
                    .local_preload
                    .lock()
                    .unwrap_or_else(|e| e.into_inner());
                preload.shutting_down = true;
                preload.generation = preload.generation.wrapping_add(1);
                preload.desired = None;
                preload.ready = false;
                preload.downloading_model_id = None;
                if let Some((_, _, cancel)) = preload.dictation_download.take() {
                    cancel.store(true, Ordering::Release);
                }
                let clear_observer = preload
                    .loading_generation
                    .take()
                    .and_then(|_| preload.observer.clone());
                if let Some(cancel) = preload.cancel_flag.take() {
                    cancel.store(true, Ordering::Release);
                }
                let mut tasks: Vec<JoinHandle<()>> = std::mem::take(&mut preload.superseded)
                    .into_iter()
                    .map(|entry| entry.handle)
                    .collect();
                if let Some(task) = preload.task.take() {
                    tasks.push(task);
                }
                (tasks, clear_observer)
            };
            if let Some(observer) = clear_observer {
                observer.on_loading_status(None);
            }
            preload_tasks
        };
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
        for task in preload_tasks {
            let _ = task.await;
        }
        for handle in session_handles {
            let _ = handle.await;
        }
        // Drain any detached local-inference/preview/preload tasks pushed while sessions unwound.
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
        if let Some(local) = &self.inner.local_transcriber {
            local.unload();
        }
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
                keywords.clone(),
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
                keywords.clone(),
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

    let raw_transcript = apply_vocabulary_corrections(&raw_transcript, &keywords);

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
        let maintenance =
            DeepgramMaintenanceKey::from_timeouts(&deepgram_timeouts_from_config(config));
        let (manager, warm_gen) = get_or_create_deepgram_manager(
            state,
            state.endpoints.deepgram_ws_url(),
            api_key,
            maintenance,
        )
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
            .map_err(|err| map_openai_compatible_config_error(&err))?;
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
    maintenance: DeepgramMaintenanceKey,
) -> Result<(DeepgramWarmConnectionManager, u64), ProviderError> {
    let (manager, gen, previous) = {
        let mut warm = state.warm_deepgram.lock().await;
        if let Some(existing) = warm.slot.as_ref() {
            if existing.ws_url == ws_url
                && existing.api_key == api_key
                && existing.maintenance == maintenance
            {
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
            maintenance,
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

    let was_cancelled = control.is_cancelled() || is_cancelled(cancelled_rx);
    let ended_before_stop = !control.is_stopped() && !was_cancelled;
    if !capture_done {
        // Stream ended before user stopped capture (e.g., server closed or auth failure).
        control.signal_cancel();
        let _ = capture_handle.await;
    }

    if was_cancelled {
        return Ok((String::new(), Instant::now()));
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
            tracing::debug!(
                reason = %safe_provider_error_summary(&err),
                "background Deepgram WebSocket re-warm failed"
            );
        }
    }));
}

fn format_model_download_status(model_id: &str, progress: &DownloadProgress) -> String {
    let part = if progress.total_files > 1 {
        format!("{}/{}, ", progress.file_index, progress.total_files)
    } else {
        String::new()
    };
    if let Some(total) = progress.total_bytes.filter(|&t| t > 0) {
        let pct = progress
            .bytes_downloaded
            .saturating_mul(100)
            .checked_div(total)
            .unwrap_or(0)
            .min(100);
        format!("Downloading {model_id} ({part}{pct}%)")
    } else {
        let mb = progress.bytes_downloaded / (1024 * 1024);
        format!("Downloading {model_id} ({part}{mb} MB)")
    }
}

fn set_downloading_model_for_gen(state: &LiveBackendState, my_gen: u64, model_id: Option<&str>) {
    let _emit_guard = state
        .loading_emit_lock
        .lock()
        .unwrap_or_else(|e| e.into_inner());
    let mut preload = state
        .local_preload
        .lock()
        .unwrap_or_else(|e| e.into_inner());
    if preload.generation == my_gen {
        preload.downloading_model_id = model_id.map(ToOwned::to_owned);
    }
}

fn publish_loading_for_gen(
    state: &LiveBackendState,
    my_gen: u64,
    cancel_flag: &AtomicBool,
    detail: String,
) {
    let _emit_guard = state
        .loading_emit_lock
        .lock()
        .unwrap_or_else(|e| e.into_inner());
    let observer = {
        let mut preload = state
            .local_preload
            .lock()
            .unwrap_or_else(|e| e.into_inner());
        if preload.shutting_down
            || preload.generation != my_gen
            || cancel_flag.load(Ordering::Acquire)
        {
            None
        } else {
            preload.loading_generation = Some(my_gen);
            preload.observer.clone()
        }
    };
    if let Some(obs) = observer {
        obs.on_loading_status(Some(detail));
    }
}

fn clear_loading_for_gen(state: &LiveBackendState, my_gen: u64) {
    let _emit_guard = state
        .loading_emit_lock
        .lock()
        .unwrap_or_else(|e| e.into_inner());
    let observer = {
        let mut preload = state
            .local_preload
            .lock()
            .unwrap_or_else(|e| e.into_inner());
        if preload.loading_generation == Some(my_gen) {
            preload.loading_generation = None;
            preload.observer.clone()
        } else {
            None
        }
    };
    if let Some(obs) = observer {
        obs.on_loading_status(None);
    }
}

fn notify_download_progress(state: &LiveBackendState, my_gen: u64, progress: DownloadProgress) {
    let _emit_guard = state
        .loading_emit_lock
        .lock()
        .unwrap_or_else(|e| e.into_inner());
    let observer = {
        let preload = state
            .local_preload
            .lock()
            .unwrap_or_else(|e| e.into_inner());
        if preload.shutting_down || preload.generation != my_gen {
            None
        } else {
            preload.observer.clone()
        }
    };
    if let Some(obs) = observer {
        obs.on_download_progress(progress);
    }
}

fn finish_preload_download(
    state: &LiveBackendState,
    my_gen: u64,
    model_id: &str,
    result: Result<(), &str>,
) {
    let _emit_guard = state
        .loading_emit_lock
        .lock()
        .unwrap_or_else(|e| e.into_inner());
    let observer = {
        let mut preload = state
            .local_preload
            .lock()
            .unwrap_or_else(|e| e.into_inner());
        if preload.generation == my_gen {
            preload.downloading_model_id = None;
        }
        let same_model_restarted = preload.generation != my_gen
            && preload.downloading_model_id.as_deref() == Some(model_id);
        let dictation_same_model = preload
            .dictation_download
            .as_ref()
            .is_some_and(|(_, id, _)| id == model_id);
        if preload.shutting_down || same_model_restarted || dictation_same_model {
            // A newer preload generation or active dictation transfer is downloading the same
            // model; do not let a stale cancelled task clear its UI download state.
            None
        } else {
            preload.observer.clone()
        }
    };
    if let Some(obs) = observer {
        obs.on_download_finished(model_id, result);
    }
}

fn start_dictation_download(
    state: &LiveBackendState,
    run_id: u64,
    model_id: &str,
    cancel_flag: Arc<AtomicBool>,
) {
    let _emit_guard = state
        .loading_emit_lock
        .lock()
        .unwrap_or_else(|e| e.into_inner());
    let mut preload = state
        .local_preload
        .lock()
        .unwrap_or_else(|e| e.into_inner());
    if !preload.shutting_down {
        preload.dictation_download = Some((run_id, model_id.to_owned(), cancel_flag));
    }
}

fn notify_dictation_download_progress(
    state: &LiveBackendState,
    run_id: u64,
    progress: DownloadProgress,
) {
    let _emit_guard = state
        .loading_emit_lock
        .lock()
        .unwrap_or_else(|e| e.into_inner());
    let observer = {
        let preload = state
            .local_preload
            .lock()
            .unwrap_or_else(|e| e.into_inner());
        let matches = preload
            .dictation_download
            .as_ref()
            .is_some_and(|(id, _, _)| *id == run_id);
        if preload.shutting_down || !matches {
            None
        } else {
            preload.observer.clone()
        }
    };
    if let Some(obs) = observer {
        obs.on_download_progress(progress);
    }
}

fn finish_dictation_download(
    state: &LiveBackendState,
    run_id: u64,
    model_id: &str,
    result: Result<(), &str>,
) {
    let _emit_guard = state
        .loading_emit_lock
        .lock()
        .unwrap_or_else(|e| e.into_inner());
    let observer = {
        let mut preload = state
            .local_preload
            .lock()
            .unwrap_or_else(|e| e.into_inner());
        if preload
            .dictation_download
            .as_ref()
            .is_some_and(|(id, _, _)| *id == run_id)
        {
            preload.dictation_download = None;
        }
        let preload_same_model = preload.downloading_model_id.as_deref() == Some(model_id);
        let newer_dictation_same_model = preload
            .dictation_download
            .as_ref()
            .is_some_and(|(_, id, _)| id == model_id);
        if preload.shutting_down || preload_same_model || newer_dictation_same_model {
            None
        } else {
            preload.observer.clone()
        }
    };
    if let Some(obs) = observer {
        obs.on_download_finished(model_id, result);
    }
}

fn track_detached_task(state: &LiveBackendState, handle: Option<JoinHandle<()>>) {
    let Some(handle) = handle else {
        return;
    };
    if handle.is_finished() {
        return;
    }
    let mut reg = state.registry.lock().unwrap_or_else(|e| e.into_inner());
    reg.detached_blocking.retain(|h| !h.is_finished());
    reg.detached_blocking.push(handle);
}

fn stop_preview_worker(
    state: &LiveBackendState,
    preview: Option<(Arc<AtomicBool>, JoinHandle<()>)>,
) {
    let Some((cancel, handle)) = preview else {
        return;
    };
    cancel.store(true, Ordering::Release);
    track_detached_task(state, Some(handle));
}

fn run_local_preview_worker(
    local: &dyn LocalTranscriber,
    language: Option<&str>,
    rx: std::sync::mpsc::Receiver<Vec<f32>>,
    run_id: u64,
    cancel_flag: &AtomicBool,
    event_tx: &mpsc::UnboundedSender<(u64, PipelineEvent)>,
) {
    if cancel_flag.load(Ordering::Acquire) {
        return;
    }
    let mut session = match local.open_streaming_preview(language, cancel_flag) {
        Ok(Some(session)) => session,
        Ok(None) | Err(LocalAsrError::Cancelled) => return,
        Err(err) => {
            tracing::debug!(
                error = %err,
                "local streaming preview unavailable; continuing offline batch capture"
            );
            return;
        }
    };

    let mut chunk_buf = Vec::with_capacity(TARGET_SAMPLE_RATE as usize);
    let mut last_emitted = String::new();

    while let Ok(first) = rx.recv() {
        if cancel_flag.load(Ordering::Acquire) {
            break;
        }
        chunk_buf.clear();
        chunk_buf.extend_from_slice(&first);
        // Coalesce any already-buffered chunks so preview catches up in one decode pass.
        while let Ok(more) = rx.try_recv() {
            chunk_buf.extend_from_slice(&more);
            if chunk_buf.len() >= TARGET_SAMPLE_RATE as usize * 2 {
                break;
            }
        }
        if chunk_buf.is_empty() {
            continue;
        }
        if let Err(err) = session.feed(&chunk_buf) {
            tracing::debug!(error = %err, "local streaming preview feed failed");
            break;
        }
        if cancel_flag.load(Ordering::Acquire) {
            break;
        }
        match session.text() {
            Ok(text) => {
                let trimmed = text.trim();
                if !trimmed.is_empty()
                    && trimmed != last_emitted
                    && !cancel_flag.load(Ordering::Acquire)
                {
                    last_emitted.clear();
                    last_emitted.push_str(trimmed);
                    let _ =
                        event_tx.send((run_id, PipelineEvent::InterimText(last_emitted.clone())));
                }
            }
            Err(err) => {
                tracing::debug!(error = %err, "local streaming preview text read failed");
                break;
            }
        }
    }
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

    let (preview_tx, mut preview_session) = match &backend {
        BatchBackend::Local(local) if config.streaming => {
            let (tx, rx) =
                std::sync::mpsc::sync_channel::<Vec<f32>>(LOCAL_PREVIEW_CHANNEL_CAPACITY);
            let cancel = Arc::new(AtomicBool::new(false));
            let local_clone = Arc::clone(local);
            let language = config.language.clone();
            let preview_events = event_tx.clone();
            let cancel_worker = Arc::clone(&cancel);
            let handle = tokio::task::spawn_blocking(move || {
                run_local_preview_worker(
                    local_clone.as_ref(),
                    language.as_deref(),
                    rx,
                    run_id,
                    &cancel_worker,
                    &preview_events,
                );
            });
            (Some(tx), Some((cancel, handle)))
        }
        _ => (None, None),
    };

    let mut capture_handle = tokio::task::spawn_blocking(move || {
        capture_batch_loop(
            factory.as_ref(),
            device_name.as_deref(),
            &capture_control,
            run_id,
            &capture_events,
            preview_tx,
        )
    });

    let captured = tokio::select! {
        biased;
        _ = wait_cancelled(cancelled_rx) => {
            if let Some((ref cancel, _)) = preview_session {
                cancel.store(true, Ordering::Release);
            }
            control.signal_cancel();
            let _ = capture_handle.await;
            stop_preview_worker(state, preview_session.take());
            return Ok((String::new(), Instant::now()));
        }
        res = &mut capture_handle => res,
    };

    stop_preview_worker(state, preview_session.take());

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
            // If the configured curated local model is not yet cached (for example, dictation
            // triggered before background download completed or after an offline start), download
            // it on demand sharing `ModelDownloader`'s per-model lock.
            if let Some((cache, downloader)) = local.auto_downloader() {
                let local_probe = Arc::clone(&local);
                let model_probe = config.model.clone();
                let missing = tokio::task::spawn_blocking(move || {
                    local_probe.missing_download_spec(model_probe.as_deref())
                })
                .await
                .map_err(|err| {
                    DaemonStatusError::new(
                        DaemonErrorCode::Unknown,
                        Some(format!("local model cache probe panicked: {err}")),
                    )
                })??;

                if let Some(spec) = missing {
                    tracing::info!(
                        model = spec.id,
                        "local ASR model missing during dictation; downloading automatically"
                    );
                    let initial_label = format!("Downloading {}", spec.id);
                    let _ = event_tx.send((
                        run_id,
                        PipelineEvent::ModelDownloadProgress {
                            detail: Some(initial_label.clone()),
                            update_detail: true,
                        },
                    ));
                    let dl_cancel = Arc::clone(&cancel_flag);
                    let cancel_for_reg = Arc::clone(&cancel_flag);
                    let progress_tx = event_tx.clone();
                    let state_start = Arc::clone(state);
                    let state_cb = Arc::clone(state);
                    let owned_transfer = Arc::new(AtomicBool::new(false));
                    let owned_start = Arc::clone(&owned_transfer);
                    let mut last_label = initial_label;
                    let mut last_watchdog_bump = Instant::now();
                    let dl_fut = downloader.ensure_downloaded_observing(
                        &cache,
                        spec,
                        &dl_cancel,
                        move || {
                            owned_start.store(true, Ordering::Release);
                            start_dictation_download(&state_start, run_id, spec.id, cancel_for_reg);
                        },
                        move |progress, from_peer| {
                            let label = format_model_download_status(spec.id, &progress);
                            let label_changed = label != last_label;
                            // Refresh the stall watchdog at most once per second (or whenever the
                            // visible download label changes) while bytes flow—including peer
                            // transfer progress forwarded while awaiting the per-model slot.
                            if label_changed
                                || last_watchdog_bump.elapsed() >= Duration::from_secs(1)
                            {
                                last_watchdog_bump = Instant::now();
                                let detail = if label_changed {
                                    last_label = label.clone();
                                    Some(label)
                                } else {
                                    None
                                };
                                let _ = progress_tx.send((
                                    run_id,
                                    PipelineEvent::ModelDownloadProgress {
                                        detail,
                                        update_detail: label_changed,
                                    },
                                ));
                            }
                            if !from_peer {
                                notify_dictation_download_progress(&state_cb, run_id, progress);
                            }
                        },
                    );
                    tokio::pin!(dl_fut);
                    let dl_res = tokio::select! {
                        biased;
                        _ = wait_cancelled(cancelled_rx) => {
                            cancel_flag.store(true, Ordering::Release);
                            // Await cooperative exit (<=50 ms via `race_with_cancel`) so
                            // `download_single_file` closes and unlinks `.part` files cleanly.
                            let _ = dl_fut.await;
                            if owned_transfer.load(Ordering::Acquire) {
                                finish_dictation_download(state, run_id, spec.id, Err(""));
                            }
                            return Ok((String::new(), post_stop_started));
                        }
                        res = &mut dl_fut => res,
                    };
                    if owned_transfer.load(Ordering::Acquire) {
                        match &dl_res {
                            Ok(_) => finish_dictation_download(state, run_id, spec.id, Ok(())),
                            Err(LocalAsrError::Cancelled) => {
                                finish_dictation_download(state, run_id, spec.id, Err(""));
                            }
                            Err(err) => {
                                let msg = err.to_string();
                                finish_dictation_download(state, run_id, spec.id, Err(&msg));
                            }
                        }
                    }
                    match dl_res {
                        Ok(_) => {
                            // Restore the standard `"Transcribing…"` overlay text and grant offline
                            // inference a fresh watchdog window.
                            let _ = event_tx.send((
                                run_id,
                                PipelineEvent::ModelDownloadProgress {
                                    detail: None,
                                    update_detail: true,
                                },
                            ));
                        }
                        Err(LocalAsrError::Cancelled) => {
                            return Ok((String::new(), post_stop_started));
                        }
                        Err(err) => return Err(map_local_asr_error(&err)),
                    }
                }
            }
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
    mut preview_tx: Option<std::sync::mpsc::SyncSender<Vec<f32>>>,
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
            if let Some(tx) = preview_tx.as_ref() {
                match tx.try_send(chunk_16k.to_vec()) {
                    Ok(()) | Err(std::sync::mpsc::TrySendError::Full(_)) => {}
                    Err(std::sync::mpsc::TrySendError::Disconnected(_)) => {
                        // Preview worker exited (e.g., Nemotron not installed); stop cloning chunks.
                        preview_tx = None;
                    }
                }
            }
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
            tracing::warn!(
                reason = %safe_provider_error_summary(&err),
                "LLM refinement skipped; keeping original transcript"
            );
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
            tracing::warn!(
                provider = provider.kind().as_str(),
                reason = %safe_provider_error_summary(&err),
                "LLM refinement failed; keeping original transcript"
            );
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
        // Clamp in floating-point seconds BEFORE `Duration::from_secs_f64` so huge inputs like
        // `1e30` cannot panic inside `Duration::from_secs_f64`.
        .map(|secs| secs.clamp(min.as_secs_f64(), max.as_secs_f64()))
        .map(Duration::from_secs_f64)
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

fn safe_provider_error_summary(err: &ProviderError) -> String {
    match err {
        ProviderError::MissingConfig(msg) => msg.clone(),
        ProviderError::InvalidInput(_) => "Invalid provider configuration".to_owned(),
        ProviderError::ApiStatus {
            provider, status, ..
        } => format!("{provider} API returned HTTP {status}"),
        ProviderError::Http(http_err) if http_err.is_timeout() => {
            "HTTP request timed out".to_owned()
        }
        ProviderError::Http(_) => "HTTP request failed".to_owned(),
        ProviderError::WebSocket {
            provider, detail, ..
        } => {
            let lower = detail.to_ascii_lowercase();
            if lower.contains("401")
                || lower.contains("403")
                || lower.contains("unauthorized")
                || lower.contains("forbidden")
            {
                format!("{provider} WebSocket authentication failed")
            } else if lower.contains("timed out") || lower.contains("timeout") {
                format!("{provider} WebSocket connection timed out")
            } else {
                format!("{provider} WebSocket connection failed")
            }
        }
        ProviderError::InvalidResponse { provider, .. } => {
            format!("{provider} returned an invalid response")
        }
        ProviderError::Io(io_err) => format!("I/O error ({:?})", io_err.kind()),
    }
}

fn map_openai_compatible_config_error(err: &ProviderError) -> DaemonStatusError {
    match err {
        ProviderError::InvalidInput(_) => DaemonStatusError::new(
            DaemonErrorCode::InvalidProvider,
            Some(
                "Invalid OPENAI_COMPATIBLE_BASE_URL or OpenAI-compatible provider configuration"
                    .to_owned(),
            ),
        ),
        other => map_provider_error(other),
    }
}

fn map_provider_error(err: &ProviderError) -> DaemonStatusError {
    let summary = safe_provider_error_summary(err);
    match err {
        ProviderError::MissingConfig(_) => {
            DaemonStatusError::new(DaemonErrorCode::MissingApiKey, Some(summary))
        }
        ProviderError::InvalidInput(_) => {
            DaemonStatusError::new(DaemonErrorCode::InvalidProvider, Some(summary))
        }
        ProviderError::ApiStatus { status, .. } if matches!(*status, 401 | 403) => {
            DaemonStatusError::new(DaemonErrorCode::MissingApiKey, Some(summary))
        }
        ProviderError::Http(http_err) if http_err.is_timeout() => {
            DaemonStatusError::new(DaemonErrorCode::Timeout, Some(summary))
        }
        ProviderError::WebSocket { detail, .. } => {
            let lower = detail.to_ascii_lowercase();
            if lower.contains("401")
                || lower.contains("403")
                || lower.contains("unauthorized")
                || lower.contains("forbidden")
            {
                DaemonStatusError::new(DaemonErrorCode::MissingApiKey, Some(summary))
            } else if lower.contains("timed out") || lower.contains("timeout") {
                DaemonStatusError::new(DaemonErrorCode::Timeout, Some(summary))
            } else {
                DaemonStatusError::new(DaemonErrorCode::Connection, Some(summary))
            }
        }
        ProviderError::Http(_)
        | ProviderError::ApiStatus { .. }
        | ProviderError::InvalidResponse { .. } => {
            DaemonStatusError::new(DaemonErrorCode::Connection, Some(summary))
        }
        ProviderError::Io(_) => DaemonStatusError::new(DaemonErrorCode::Unknown, Some(summary)),
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parse_bounded_duration_clamps_extreme_values_without_panicking() {
        let default = Duration::from_secs(2);
        let min = Duration::from_millis(50);
        let max = Duration::from_secs(10);

        assert_eq!(parse_bounded_duration(Some("1e30"), default, min, max), max);
        assert_eq!(
            parse_bounded_duration(Some("0.5"), default, min, max),
            Duration::from_millis(500)
        );
        assert_eq!(
            parse_bounded_duration(Some("0.001"), default, min, max),
            min
        );
        assert_eq!(
            parse_bounded_duration(Some("-5"), default, min, max),
            default
        );
        assert_eq!(
            parse_bounded_duration(Some("NaN"), default, min, max),
            default
        );
    }

    #[test]
    fn debug_and_error_mappings_redact_tokens_and_response_bodies() {
        let endpoints = ProviderEndpoints {
            openai_base_url: Some("https://proxy.example/v1?token=super-secret-token".to_owned()),
            ..ProviderEndpoints::default()
        };
        let debug = format!("{endpoints:?}");
        assert!(debug.contains("openai_override: true"));
        assert!(!debug.contains("super-secret-token"));

        let api_err = ProviderError::ApiStatus {
            provider: "openai",
            status: 401,
            body: "invalid api key sk-secret-leak".to_owned(),
        };
        let mapped = map_provider_error(&api_err);
        assert_eq!(mapped.code, DaemonErrorCode::MissingApiKey);
        let detail = mapped.detail.unwrap_or_default();
        assert!(detail.contains("openai") && detail.contains("401"));
        assert!(!detail.contains("sk-secret-leak"));

        let url_err =
            ProviderError::InvalidInput("invalid url https://host?token=url-secret".to_owned());
        let mapped_url = map_openai_compatible_config_error(&url_err);
        assert_eq!(mapped_url.code, DaemonErrorCode::InvalidProvider);
        let url_detail = mapped_url.detail.unwrap_or_default();
        assert!(url_detail.contains("OPENAI_COMPATIBLE_BASE_URL"));
        assert!(!url_detail.contains("url-secret"));
    }

    #[test]
    fn local_asr_error_mapping_covers_missing_model_runtime_and_unknown() {
        assert_eq!(
            map_local_asr_error(&LocalAsrError::MissingRuntime("no lib".to_owned())).code,
            DaemonErrorCode::MissingDependency
        );
        assert_eq!(
            map_local_asr_error(&LocalAsrError::ModelNotDownloaded(
                "large-v3-turbo".to_owned()
            ))
            .code,
            DaemonErrorCode::MissingDependency
        );
        assert_eq!(
            map_local_asr_error(&LocalAsrError::UnknownModel("foo".to_owned())).code,
            DaemonErrorCode::InvalidProvider
        );
        assert_eq!(
            map_local_asr_error(&LocalAsrError::Download("offline".to_owned())).code,
            DaemonErrorCode::Connection
        );
    }
}
