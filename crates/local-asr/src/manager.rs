//! Single-slot local ASR engine cache and backend dispatcher (`whisper-rs` + `sherpa-onnx`).
//!
//! Mirrors `providers/local_memory.py` by keeping at most one loaded model in RAM at a time and
//! evicting the previous engine before loading a new model or language-bound Canary configuration.

use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};

use pulsescribe_core::paths::AppPaths;
use serde::{Deserialize, Serialize};

use crate::cache::ModelCache;
use crate::catalog::{is_canary_language, resolve_model_spec, LocalEngineKind};
use crate::{normalize_language, LocalAsrEngine, LocalAsrError, LocalAsrRequest};

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

/// Thread-safe local ASR manager resolving catalog models or custom GGML paths, caching the active
/// engine, and dispatching transcription requests.
#[derive(Clone)]
pub struct LocalAsrManager {
    config_dir: PathBuf,
    cache: ModelCache,
    active: Arc<Mutex<ActiveState>>,
    load_gate: Arc<Mutex<()>>,
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
            load_gate: Arc::new(Mutex::new(())),
        }
    }

    /// Construct a manager with an explicit [`ModelCache`] (useful in tests).
    pub fn with_cache(config_dir: impl Into<PathBuf>, cache: ModelCache) -> Self {
        Self {
            config_dir: config_dir.into(),
            cache,
            active: Arc::new(Mutex::new(ActiveState::default())),
            load_gate: Arc::new(Mutex::new(())),
        }
    }

    pub fn cache(&self) -> &ModelCache {
        &self.cache
    }

    pub fn capabilities() -> LocalRuntimeCapabilities {
        LocalRuntimeCapabilities::current()
    }

    /// Unload any cached local ASR engine immediately to release model RAM.
    pub fn unload(&self) {
        let mut guard = self.active.lock().unwrap_or_else(|e| e.into_inner());
        guard.generation = guard.generation.wrapping_add(1);
        guard.slot = None;
    }

    /// Return the currently loaded model identifier, if any.
    pub fn loaded_model_id(&self) -> Option<String> {
        let guard = self.active.lock().unwrap_or_else(|e| e.into_inner());
        guard.slot.as_ref().map(|slot| slot.key.model_id.clone())
    }

    /// Load (or reuse cached) engine for `model_name` and transcribe `request`.
    pub fn transcribe(
        &self,
        model_name: Option<&str>,
        request: &LocalAsrRequest,
    ) -> Result<String, LocalAsrError> {
        let engine = self.get_or_load(model_name, request.language.as_deref())?;
        engine.transcribe(request)
    }

    /// Resolve and load (or return the cached) [`LocalAsrEngine`] for `model_name`.
    pub fn get_or_load(
        &self,
        model_name: Option<&str>,
        language_hint: Option<&str>,
    ) -> Result<Arc<dyn LocalAsrEngine>, LocalAsrError> {
        // Explicit existing file path (e.g. custom GGML `.bin`) bypasses the curated catalog.
        if let Some(raw_path) = model_name.map(str::trim).filter(|s| !s.is_empty()) {
            let candidate = Path::new(raw_path);
            if candidate.is_file() {
                let key = LoadedEngineKey {
                    model_id: raw_path.to_owned(),
                    language_tag: None,
                };
                return self.load_with_slot(key, || load_custom_whisper_file(raw_path, candidate));
            }
        }

        let spec = resolve_model_spec(model_name).ok_or_else(|| {
            LocalAsrError::UnknownModel(model_name.unwrap_or("").trim().to_owned())
        })?;

        if spec.engine.is_streaming_preview() {
            return Err(LocalAsrError::InvalidModel(format!(
                "'{}' is a streaming preview model and cannot be used for offline transcription",
                spec.id
            )));
        }

        let canary_lang = if spec.engine == LocalEngineKind::SherpaOfflineCanary {
            Some(
                normalize_language(language_hint)
                    .filter(|l| is_canary_language(l))
                    .unwrap_or_else(|| "de".to_owned()),
            )
        } else {
            None
        };

        let key = LoadedEngineKey {
            model_id: spec.id.to_owned(),
            language_tag: canary_lang.clone(),
        };

        self.load_with_slot(key, || {
            let installed = self
                .cache
                .find_installed(spec)
                .ok_or_else(|| LocalAsrError::ModelNotDownloaded(spec.id.to_owned()))?;

            match spec.engine {
                LocalEngineKind::Whisper => {
                    let primary = installed
                        .primary_file(spec)
                        .ok_or_else(|| LocalAsrError::ModelNotDownloaded(spec.id.to_owned()))?;
                    load_whisper_engine(spec.id, &primary)
                }
                LocalEngineKind::SherpaOfflineTransducer | LocalEngineKind::SherpaOfflineCanary => {
                    load_sherpa_offline_engine(
                        spec,
                        &installed,
                        Some(&self.config_dir),
                        canary_lang.as_deref(),
                    )
                }
                LocalEngineKind::SherpaOnlineTransducer => unreachable!("guarded above"),
            }
        })
    }

    fn load_with_slot<F>(
        &self,
        key: LoadedEngineKey,
        loader: F,
    ) -> Result<Arc<dyn LocalAsrEngine>, LocalAsrError>
    where
        F: FnOnce() -> Result<Arc<dyn LocalAsrEngine>, LocalAsrError>,
    {
        // Fast path: cache hit without waiting on `load_gate`.
        {
            let guard = self.active.lock().unwrap_or_else(|e| e.into_inner());
            if let Some(slot) = guard.slot.as_ref() {
                if slot.key == key {
                    return Ok(Arc::clone(&slot.engine));
                }
            }
        }

        // Serialize cache misses so two concurrent loads never double peak RAM.
        let _load_guard = self.load_gate.lock().unwrap_or_else(|e| e.into_inner());
        let expected_gen = {
            let mut guard = self.active.lock().unwrap_or_else(|e| e.into_inner());
            if let Some(slot) = guard.slot.as_ref() {
                if slot.key == key {
                    return Ok(Arc::clone(&slot.engine));
                }
            }
            // Evict the previous model BEFORE loading the replacement so peak RAM never doubles.
            guard.slot = None;
            guard.generation
        };

        let engine = loader()?;
        let mut guard = self.active.lock().unwrap_or_else(|e| e.into_inner());
        if guard.generation == expected_gen {
            guard.slot = Some(LoadedEngineSlot {
                key,
                engine: Arc::clone(&engine),
            });
        }
        Ok(engine)
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
    Err(LocalAsrError::MissingRuntime(format!(
        "Whisper backend is not compiled into this build (rebuild with '--features local-whisper' to run '{model_id}')"
    )))
}

fn load_custom_whisper_file(
    label: &str,
    path: &Path,
) -> Result<Arc<dyn LocalAsrEngine>, LocalAsrError> {
    load_whisper_engine(label, path)
}

#[cfg(feature = "local-sherpa")]
fn load_sherpa_offline_engine(
    spec: &crate::catalog::ModelSpec,
    installed: &crate::cache::InstalledModel,
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
    spec: &crate::catalog::ModelSpec,
    _installed: &crate::cache::InstalledModel,
    _config_dir: Option<&Path>,
    _language_hint: Option<&str>,
) -> Result<Arc<dyn LocalAsrEngine>, LocalAsrError> {
    Err(LocalAsrError::MissingRuntime(format!(
        "sherpa-onnx backend is not compiled into this build (rebuild with '--features local-sherpa' to run '{}')",
        spec.id
    )))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use std::sync::atomic::{AtomicU64, Ordering};
    use std::time::{SystemTime, UNIX_EPOCH};

    static MGR_TEST_SEQ: AtomicU64 = AtomicU64::new(0);

    #[test]
    fn manager_reports_unknown_streaming_and_missing_model_cleanly() {
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
        assert_eq!(mgr.loaded_model_id(), None);
        mgr.unload();
        assert_eq!(mgr.loaded_model_id(), None);
        assert_eq!(
            LocalAsrManager::capabilities().sherpa_compiled,
            cfg!(feature = "local-sherpa")
        );

        let _ = fs::remove_dir_all(&dir);
    }
}
