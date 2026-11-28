//! Single-slot local ASR engine cache and backend dispatcher (`whisper-rs` + `sherpa-onnx`).
//!
//! Mirrors `providers/local_memory.py` by keeping at most one loaded model in RAM at a time and
//! coordinating model replacement with in-flight transcriptions so previous weights are released
//! before replacement weights are allocated.

use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex, RwLock, RwLockReadGuard};

use pulsescribe_core::paths::AppPaths;
use serde::{Deserialize, Serialize};

use crate::cache::{InstalledModel, ModelCache};
use crate::catalog::{resolve_canary_language, resolve_model_spec, LocalEngineKind, ModelSpec};
use crate::{LocalAsrEngine, LocalAsrError, LocalAsrRequest, LocalStreamingSession};

/// Compile-time feature flags enabled in this binary.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub struct LocalRuntimeCapabilities {
    pub whisper_compiled: bool,
    pub sherpa_compiled: bool,
}

impl LocalRuntimeCapabilities {
    pub const fn current() -> Self {
        Self {
            whisper_compiled: cfg!(feature = "local-whisper"),
            sherpa_compiled: cfg!(feature = "local-sherpa"),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct LoadedEngineKey {
    model_id: String,
    language_tag: Option<String>,
}

struct LoadedEngineSlot {
    key: LoadedEngineKey,
    engine: Arc<dyn LocalAsrEngine>,
}

#[derive(Default)]
struct ActiveState {
    generation: u64,
    slot: Option<LoadedEngineSlot>,
}

#[cfg(feature = "local-sherpa")]
#[derive(Default)]
struct StreamingPreviewState {
    generation: u64,
    model: Option<Arc<crate::sherpa_backend::SherpaOnlineModel>>,
}

enum ResolvedLoadPlan {
    CustomWhisper {
        key: LoadedEngineKey,
        path: PathBuf,
    },
    Catalog {
        key: LoadedEngineKey,
        spec: &'static ModelSpec,
        installed: InstalledModel,
        primary_whisper_file: Option<PathBuf>,
        canary_lang: Option<String>,
    },
}

impl ResolvedLoadPlan {
    fn key(&self) -> &LoadedEngineKey {
        match self {
            Self::CustomWhisper { key, .. } | Self::Catalog { key, .. } => key,
        }
    }
}

/// Thread-safe local ASR manager resolving catalog models or custom GGML paths, caching the active
/// engine, and dispatching transcription requests.
#[derive(Clone)]
pub struct LocalAsrManager {
    config_dir: PathBuf,
    cache: ModelCache,
    // Lock acquisition order: `load_gate` -> `in_use` -> `active`.
    // `active` is never held while dropping an evicted engine or running a native model loader.
    active: Arc<Mutex<ActiveState>>,
    in_use: Arc<RwLock<()>>,
    load_gate: Arc<Mutex<()>>,
    // Independent lock hierarchy for the optional Nemotron streaming preview slot:
    // `streaming_load_gate` -> `streaming_preview`. Never coupled with the offline slot so
    // live preview never evicts or blocks Whisper/Parakeet/Canary inference.
    #[cfg(feature = "local-sherpa")]
    streaming_preview: Arc<Mutex<StreamingPreviewState>>,
    #[cfg(feature = "local-sherpa")]
    streaming_load_gate: Arc<Mutex<()>>,
}

impl LocalAsrManager {
    /// Construct a manager bound to `config_dir` (`~/.pulsescribe`) with Hugging Face cache reuse.
    pub fn from_config_dir(config_dir: impl Into<PathBuf>) -> Self {
        let config_dir = config_dir.into();
        let paths = AppPaths::new(&config_dir);
        let cache = ModelCache::from_app_paths(&paths);
        Self {
            config_dir,
            cache,
            active: Arc::new(Mutex::new(ActiveState::default())),
            in_use: Arc::new(RwLock::new(())),
            load_gate: Arc::new(Mutex::new(())),
            #[cfg(feature = "local-sherpa")]
            streaming_preview: Arc::new(Mutex::new(StreamingPreviewState::default())),
            #[cfg(feature = "local-sherpa")]
            streaming_load_gate: Arc::new(Mutex::new(())),
        }
    }

    /// Construct a manager with an explicit [`ModelCache`] (useful in tests).
    pub fn with_cache(config_dir: impl Into<PathBuf>, cache: ModelCache) -> Self {
        Self {
            config_dir: config_dir.into(),
            cache,
            active: Arc::new(Mutex::new(ActiveState::default())),
            in_use: Arc::new(RwLock::new(())),
            load_gate: Arc::new(Mutex::new(())),
            #[cfg(feature = "local-sherpa")]
            streaming_preview: Arc::new(Mutex::new(StreamingPreviewState::default())),
            #[cfg(feature = "local-sherpa")]
            streaming_load_gate: Arc::new(Mutex::new(())),
        }
    }

    pub fn cache(&self) -> &ModelCache {
        &self.cache
    }

    pub fn capabilities() -> LocalRuntimeCapabilities {
        LocalRuntimeCapabilities::current()
    }

    /// Unload any cached local ASR engine (both offline and streaming preview) to release RAM.
    pub fn unload(&self) {
        let evicted = {
            let mut guard = self.active.lock().unwrap_or_else(|e| e.into_inner());
            guard.generation = guard.generation.wrapping_add(1);
            guard.slot.take()
        };
        drop(evicted);
        self.unload_streaming_preview();
    }

    /// Unload only the cached local streaming preview model (Nemotron) without evicting the
    /// active offline Whisper/Parakeet/Canary engine.
    pub fn unload_streaming_preview(&self) {
        #[cfg(feature = "local-sherpa")]
        {
            let evicted = {
                let mut guard = self
                    .streaming_preview
                    .lock()
                    .unwrap_or_else(|e| e.into_inner());
                guard.generation = guard.generation.wrapping_add(1);
                guard.model.take()
            };
            drop(evicted);
        }
    }

    /// Return the currently loaded offline model identifier, if any.
    pub fn loaded_model_id(&self) -> Option<String> {
        let guard = self.active.lock().unwrap_or_else(|e| e.into_inner());
        guard.slot.as_ref().map(|slot| slot.key.model_id.clone())
    }

    /// Warm up (load and cache) the configured offline local ASR engine without running inference.
    ///
    /// Never downloads missing model files: returns [`LocalAsrError::ModelNotDownloaded`] if not
    /// cached locally or [`LocalAsrError::Cancelled`] when `cancel_flag` fires before load finishes.
    pub fn preload(
        &self,
        model_name: Option<&str>,
        language_hint: Option<&str>,
        cancel_flag: Option<&AtomicBool>,
    ) -> Result<String, LocalAsrError> {
        let (engine, _use_guard) = self.acquire_engine(model_name, language_hint, cancel_flag)?;
        Ok(engine.model_id().to_owned())
    }

    /// Return the curated offline [`ModelSpec`] when `model_name` resolves to a catalog model whose
    /// runtime is compiled into this binary and whose weights are not yet cached locally.
    ///
    /// Returns `Ok(None)` when the model is already cached or refers to a non-catalog custom file
    /// path, and `Err(LocalAsrError::MissingRuntime)` in slim builds lacking the required engine
    /// (preventing wasted downloads of models the binary cannot execute).
    pub fn missing_downloadable_spec(
        &self,
        model_name: Option<&str>,
    ) -> Result<Option<&'static ModelSpec>, LocalAsrError> {
        let Some(spec) = resolve_model_spec(model_name) else {
            return Ok(None);
        };
        if spec.engine.is_streaming_preview() {
            return Err(LocalAsrError::InvalidModel(format!(
                "'{}' is a streaming preview model and cannot be used for offline transcription",
                spec.id
            )));
        }
        ensure_runtime_compiled(spec.engine, spec.id)?;
        if self.cache.find_installed(spec).is_some() {
            return Ok(None);
        }
        Ok(Some(spec))
    }

    /// Open a local streaming preview session (Nemotron 3.5) for live interim dictation text.
    ///
    /// Returns `Ok(None)` immediately when `local-sherpa` is not compiled into this binary or when
    /// `nemotron-streaming` is not yet downloaded locally. Never downloads files and never evicts
    /// the primary offline engine.
    pub fn open_streaming_preview(
        &self,
        language_hint: Option<&str>,
        cancel_flag: Option<&AtomicBool>,
    ) -> Result<Option<Box<dyn LocalStreamingSession>>, LocalAsrError> {
        if cancel_flag.is_some_and(|f| f.load(Ordering::Acquire)) {
            return Err(LocalAsrError::Cancelled);
        }
        #[cfg(not(feature = "local-sherpa"))]
        {
            let _ = language_hint;
            Ok(None)
        }
        #[cfg(feature = "local-sherpa")]
        {
            let Some(spec) = resolve_model_spec(Some(crate::catalog::STREAMING_PREVIEW_MODEL_ID))
            else {
                return Ok(None);
            };
            let Some(installed) = self.cache.find_installed(spec) else {
                return Ok(None);
            };

            let model = 'load: {
                let expected_gen = {
                    let guard = self
                        .streaming_preview
                        .lock()
                        .unwrap_or_else(|e| e.into_inner());
                    if let Some(cached) = guard.model.as_ref() {
                        break 'load Arc::clone(cached);
                    }
                    guard.generation
                };

                let load_guard = self
                    .streaming_load_gate
                    .lock()
                    .unwrap_or_else(|e| e.into_inner());
                if cancel_flag.is_some_and(|f| f.load(Ordering::Acquire)) {
                    return Err(LocalAsrError::Cancelled);
                }
                {
                    let guard = self
                        .streaming_preview
                        .lock()
                        .unwrap_or_else(|e| e.into_inner());
                    if let Some(cached) = guard.model.as_ref() {
                        break 'load Arc::clone(cached);
                    }
                    if guard.generation != expected_gen {
                        drop(guard);
                        drop(load_guard);
                        return Ok(None);
                    }
                }

                let loaded = Arc::new(crate::sherpa_backend::SherpaOnlineModel::load(
                    spec,
                    &installed,
                    Some(&self.config_dir),
                )?);

                if cancel_flag.is_some_and(|f| f.load(Ordering::Acquire)) {
                    // Release native weights before releasing `streaming_load_gate` so a waiting
                    // replacement never doubles peak RAM.
                    drop(loaded);
                    drop(load_guard);
                    return Err(LocalAsrError::Cancelled);
                }

                let still_current = {
                    let mut guard = self
                        .streaming_preview
                        .lock()
                        .unwrap_or_else(|e| e.into_inner());
                    if guard.generation == expected_gen {
                        guard.model = Some(Arc::clone(&loaded));
                        true
                    } else {
                        false
                    }
                };

                if !still_current {
                    drop(loaded);
                    drop(load_guard);
                    return Ok(None);
                }
                drop(load_guard);
                loaded
            };

            if cancel_flag.is_some_and(|f| f.load(Ordering::Acquire)) {
                return Err(LocalAsrError::Cancelled);
            }
            let session = model.open_session(language_hint)?;
            Ok(Some(Box::new(session)))
        }
    }

    /// Load (or reuse cached) engine for `model_name` and transcribe `request`.
    ///
    /// Holds a shared in-use guard for the duration of inference so concurrent model swaps wait
    /// for this call to drop its `Arc` before loading replacement weights into RAM.
    pub fn transcribe(
        &self,
        model_name: Option<&str>,
        request: &LocalAsrRequest,
    ) -> Result<String, LocalAsrError> {
        if request.cancel_flag.load(Ordering::Acquire) {
            return Err(LocalAsrError::Cancelled);
        }
        // Bind `_use_guard` before `engine` so `engine` drops first even on panic unwind,
        // ensuring any waiting replacement observes zero remaining strong references.
        let (_use_guard, engine) = {
            let (engine, use_guard) = self.acquire_engine(
                model_name,
                request.language.as_deref(),
                Some(&request.cancel_flag),
            )?;
            (use_guard, engine)
        };
        engine.transcribe(request)
    }

    /// Resolve and load (or return the cached) [`LocalAsrEngine`] for `model_name`.
    pub fn get_or_load(
        &self,
        model_name: Option<&str>,
        language_hint: Option<&str>,
    ) -> Result<Arc<dyn LocalAsrEngine>, LocalAsrError> {
        let (engine, _guard) = self.acquire_engine(model_name, language_hint, None)?;
        Ok(engine)
    }

    fn resolve_load_plan(
        &self,
        model_name: Option<&str>,
        language_hint: Option<&str>,
    ) -> Result<ResolvedLoadPlan, LocalAsrError> {
        // Resolve curated catalog models first so coincidental CWD files (`small`, `base`, …)
        // never shadow built-in model IDs or aliases.
        if let Some(spec) = resolve_model_spec(model_name) {
            if spec.engine.is_streaming_preview() {
                return Err(LocalAsrError::InvalidModel(format!(
                    "'{}' is a streaming preview model and cannot be used for offline transcription",
                    spec.id
                )));
            }

            let canary_lang = if spec.engine == LocalEngineKind::SherpaOfflineCanary {
                Some(resolve_canary_language(language_hint))
            } else {
                None
            };

            let key = LoadedEngineKey {
                model_id: spec.id.to_owned(),
                language_tag: canary_lang.clone(),
            };

            // Validate installation and compiled runtime before touching/evicting the active slot.
            let installed = self
                .cache
                .find_installed(spec)
                .ok_or_else(|| LocalAsrError::ModelNotDownloaded(spec.id.to_owned()))?;
            let primary_whisper_file = if spec.engine == LocalEngineKind::Whisper {
                Some(
                    installed
                        .primary_file(spec)
                        .ok_or_else(|| LocalAsrError::ModelNotDownloaded(spec.id.to_owned()))?,
                )
            } else {
                None
            };
            ensure_runtime_compiled(spec.engine, spec.id)?;

            return Ok(ResolvedLoadPlan::Catalog {
                key,
                spec,
                installed,
                primary_whisper_file,
                canary_lang,
            });
        }

        // Fall back to an explicit local model file path (e.g., custom GGML `.bin`).
        if let Some(raw_path) = model_name.map(str::trim).filter(|s| !s.is_empty()) {
            let candidate = Path::new(raw_path);
            if candidate.is_file() {
                ensure_runtime_compiled(LocalEngineKind::Whisper, raw_path)?;
                return Ok(ResolvedLoadPlan::CustomWhisper {
                    key: LoadedEngineKey {
                        model_id: raw_path.to_owned(),
                        language_tag: None,
                    },
                    path: candidate.to_path_buf(),
                });
            }
        }

        Err(LocalAsrError::UnknownModel(
            model_name.unwrap_or("").trim().to_owned(),
        ))
    }

    fn execute_load_plan(
        &self,
        plan: &ResolvedLoadPlan,
    ) -> Result<Arc<dyn LocalAsrEngine>, LocalAsrError> {
        match plan {
            ResolvedLoadPlan::CustomWhisper { key, path } => {
                load_whisper_engine(&key.model_id, path)
            }
            ResolvedLoadPlan::Catalog {
                spec,
                installed,
                primary_whisper_file,
                canary_lang,
                ..
            } => match spec.engine {
                LocalEngineKind::Whisper => {
                    let primary = primary_whisper_file
                        .as_deref()
                        .ok_or_else(|| LocalAsrError::ModelNotDownloaded(spec.id.to_owned()))?;
                    load_whisper_engine(spec.id, primary)
                }
                LocalEngineKind::SherpaOfflineTransducer
                | LocalEngineKind::SherpaOfflineCanary
                | LocalEngineKind::SherpaOfflineCohere => load_sherpa_offline_engine(
                    spec,
                    installed,
                    Some(&self.config_dir),
                    canary_lang.as_deref(),
                ),
                LocalEngineKind::SherpaOnlineTransducer => unreachable!("guarded above"),
            },
        }
    }

    fn cached_engine_or_gen(&self, key: &LoadedEngineKey) -> Result<Arc<dyn LocalAsrEngine>, u64> {
        let guard = self.active.lock().unwrap_or_else(|e| e.into_inner());
        if let Some(slot) = guard.slot.as_ref() {
            if slot.key == *key {
                return Ok(Arc::clone(&slot.engine));
            }
        }
        Err(guard.generation)
    }

    fn acquire_engine(
        &self,
        model_name: Option<&str>,
        language_hint: Option<&str>,
        cancel_flag: Option<&AtomicBool>,
    ) -> Result<(Arc<dyn LocalAsrEngine>, RwLockReadGuard<'_, ()>), LocalAsrError> {
        if cancel_flag.is_some_and(|f| f.load(Ordering::Acquire)) {
            return Err(LocalAsrError::Cancelled);
        }

        let plan = self.resolve_load_plan(model_name, language_hint)?;
        let key = plan.key();

        loop {
            if cancel_flag.is_some_and(|f| f.load(Ordering::Acquire)) {
                return Err(LocalAsrError::Cancelled);
            }

            // Fast path: cache hit under shared `in_use` guard without waiting on `load_gate`.
            // Capture `expected_gen` before waiting on `load_gate` so a concurrent `unload()`
            // prevents a queued pre-unload request from repopulating the cache.
            let read_guard = self.in_use.read().unwrap_or_else(|e| e.into_inner());
            let expected_gen = match self.cached_engine_or_gen(key) {
                Ok(engine) => return Ok((engine, read_guard)),
                Err(gen) => gen,
            };
            drop(read_guard);

            // Serialize cache misses so two concurrent loads never double peak RAM.
            let load_guard = self.load_gate.lock().unwrap_or_else(|e| e.into_inner());
            if cancel_flag.is_some_and(|f| f.load(Ordering::Acquire)) {
                return Err(LocalAsrError::Cancelled);
            }
            if self.cached_engine_or_gen(key).is_ok() {
                drop(load_guard);
                continue;
            }

            // Wait for any in-flight `transcribe()` calls to drop their `Arc` before evicting
            // the previous model and allocating replacement weights.
            let write_guard = self.in_use.write().unwrap_or_else(|e| e.into_inner());
            if cancel_flag.is_some_and(|f| f.load(Ordering::Acquire)) {
                return Err(LocalAsrError::Cancelled);
            }

            let evicted = {
                let mut guard = self.active.lock().unwrap_or_else(|e| e.into_inner());
                guard.slot.take()
            };
            // Drop native model weights outside `self.active` lock before allocating new ones.
            drop(evicted);

            let engine = self.execute_load_plan(&plan)?;
            let cancelled_during_load = cancel_flag.is_some_and(|f| f.load(Ordering::Acquire));
            {
                let mut guard = self.active.lock().unwrap_or_else(|e| e.into_inner());
                if guard.generation == expected_gen && !cancelled_during_load {
                    guard.slot = Some(LoadedEngineSlot {
                        key: key.clone(),
                        engine: Arc::clone(&engine),
                    });
                }
            }
            // Acquire `in_use.read()` BEFORE dropping `load_guard` so no waiting loader can take
            // `in_use.write()` and evict/allocate a second model while we hold `engine`.
            drop(write_guard);
            if cancelled_during_load {
                // Release native weights before dropping `load_guard` so a queued loader never
                // overlaps allocations and doubles peak RAM.
                drop(engine);
                drop(load_guard);
                return Err(LocalAsrError::Cancelled);
            }
            let read_guard = self.in_use.read().unwrap_or_else(|e| e.into_inner());
            drop(load_guard);

            return Ok((engine, read_guard));
        }
    }
}

fn ensure_runtime_compiled(engine: LocalEngineKind, model_id: &str) -> Result<(), LocalAsrError> {
    match engine {
        LocalEngineKind::Whisper if !cfg!(feature = "local-whisper") => {
            Err(LocalAsrError::MissingRuntime(format!(
                "Whisper backend is not compiled into this build (rebuild with '--features local-whisper' to run '{model_id}')"
            )))
        }
        LocalEngineKind::SherpaOfflineTransducer
        | LocalEngineKind::SherpaOfflineCanary
        | LocalEngineKind::SherpaOfflineCohere
        | LocalEngineKind::SherpaOnlineTransducer
            if !cfg!(feature = "local-sherpa") =>
        {
            Err(LocalAsrError::MissingRuntime(format!(
                "sherpa-onnx backend is not compiled into this build (rebuild with '--features local-sherpa' to run '{model_id}')"
            )))
        }
        _ => Ok(()),
    }
}

#[cfg(feature = "local-whisper")]
fn load_whisper_engine(
    model_id: &str,
    model_path: &Path,
) -> Result<Arc<dyn LocalAsrEngine>, LocalAsrError> {
    let engine = crate::whisper_backend::WhisperEngine::load(model_id, model_path)?;
    Ok(Arc::new(engine))
}

#[cfg(not(feature = "local-whisper"))]
fn load_whisper_engine(
    model_id: &str,
    _model_path: &Path,
) -> Result<Arc<dyn LocalAsrEngine>, LocalAsrError> {
    ensure_runtime_compiled(LocalEngineKind::Whisper, model_id)?;
    unreachable!()
}

#[cfg(feature = "local-sherpa")]
fn load_sherpa_offline_engine(
    spec: &ModelSpec,
    installed: &InstalledModel,
    config_dir: Option<&Path>,
    language_hint: Option<&str>,
) -> Result<Arc<dyn LocalAsrEngine>, LocalAsrError> {
    let engine = crate::sherpa_backend::SherpaOfflineEngine::load(
        spec,
        installed,
        config_dir,
        language_hint,
    )?;
    Ok(Arc::new(engine))
}

#[cfg(not(feature = "local-sherpa"))]
fn load_sherpa_offline_engine(
    spec: &ModelSpec,
    _installed: &InstalledModel,
    _config_dir: Option<&Path>,
    _language_hint: Option<&str>,
) -> Result<Arc<dyn LocalAsrEngine>, LocalAsrError> {
    ensure_runtime_compiled(spec.engine, spec.id)?;
    unreachable!()
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use std::sync::atomic::AtomicU64;
    use std::time::{SystemTime, UNIX_EPOCH};

    static MGR_TEST_SEQ: AtomicU64 = AtomicU64::new(0);

    #[test]
    fn manager_reports_unknown_streaming_missing_and_cancelled_cleanly() {
        let seq = MGR_TEST_SEQ.fetch_add(1, Ordering::Relaxed);
        let nanos = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let dir = std::env::temp_dir().join(format!(
            "pulsescribe-local-mgr-{}-{nanos}-{seq}",
            std::process::id()
        ));
        fs::create_dir_all(&dir).unwrap();

        let mgr = LocalAsrManager::with_cache(&dir, ModelCache::new(dir.join("models"), None));

        assert!(matches!(
            mgr.get_or_load(Some("no-such-model"), None),
            Err(LocalAsrError::UnknownModel(_))
        ));
        assert!(matches!(
            mgr.get_or_load(Some("nemotron-streaming"), None),
            Err(LocalAsrError::InvalidModel(_))
        ));
        assert!(matches!(
            mgr.get_or_load(Some("turbo"), None),
            Err(LocalAsrError::ModelNotDownloaded(_))
        ));

        let cancelled = Arc::new(AtomicBool::new(true));
        let req = LocalAsrRequest::new(vec![0.1; 160]).with_cancel_flag(Arc::clone(&cancelled));
        assert!(matches!(
            mgr.transcribe(Some("turbo"), &req),
            Err(LocalAsrError::Cancelled)
        ));
        assert!(matches!(
            mgr.preload(Some("turbo"), None, Some(&cancelled)),
            Err(LocalAsrError::Cancelled)
        ));
        assert!(matches!(
            mgr.preload(Some("turbo"), None, None),
            Err(LocalAsrError::ModelNotDownloaded(_))
        ));
        assert!(matches!(
            mgr.preload(Some("nemotron-streaming"), None, None),
            Err(LocalAsrError::InvalidModel(_))
        ));
        assert!(matches!(
            mgr.missing_downloadable_spec(Some("nemotron-streaming")),
            Err(LocalAsrError::InvalidModel(_))
        ));
        assert_eq!(
            mgr.missing_downloadable_spec(Some("no-such-model"))
                .unwrap(),
            None
        );
        #[cfg(feature = "local-sherpa")]
        assert_eq!(
            mgr.missing_downloadable_spec(Some("canary-1b-v2"))
                .unwrap()
                .map(|s| s.id),
            Some("canary-1b-v2")
        );
        #[cfg(feature = "local-sherpa")]
        assert_eq!(
            mgr.missing_downloadable_spec(Some("cohere-transcribe"))
                .unwrap()
                .map(|s| s.id),
            Some("cohere-transcribe")
        );
        #[cfg(not(feature = "local-sherpa"))]
        assert!(matches!(
            mgr.missing_downloadable_spec(Some("canary-1b-v2")),
            Err(LocalAsrError::MissingRuntime(_))
        ));
        #[cfg(not(feature = "local-sherpa"))]
        assert!(matches!(
            mgr.missing_downloadable_spec(Some("cohere-transcribe")),
            Err(LocalAsrError::MissingRuntime(_))
        ));
        assert!(matches!(
            mgr.open_streaming_preview(Some("de"), Some(&cancelled)),
            Err(LocalAsrError::Cancelled)
        ));
        assert!(mgr
            .open_streaming_preview(Some("de"), None)
            .unwrap()
            .is_none());

        assert_eq!(mgr.loaded_model_id(), None);
        mgr.unload_streaming_preview();
        mgr.unload();
        assert_eq!(mgr.loaded_model_id(), None);
        assert_eq!(
            LocalAsrManager::capabilities().sherpa_compiled,
            cfg!(feature = "local-sherpa")
        );

        let _ = fs::remove_dir_all(&dir);
    }
}
