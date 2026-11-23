//! Shared managed state for PulseScribe's Tauri desktop shell.

use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};

use pulsescribe_core::paths::AppPaths;
use pulsescribe_core::{
    AppState, DaemonStatusError, HistoryStore, PreferencesStore, PromptsStore, VocabularyStore,
};
use pulsescribe_daemon::{DaemonHandle, LiveDictationBackend};
use pulsescribe_local_asr::{LocalAsrManager, ModelDownloader};
use pulsescribe_overlay::OverlayHandle;
use pulsescribe_platform::PermissionsChecker;

use crate::update_install::UpdateController;

/// Cached runtime status snapshot pushed by the daemon event bridge.
#[derive(Debug, Clone)]
pub struct CachedDaemonStatus {
    pub state: AppState,
    pub detail: Option<String>,
    pub error: Option<DaemonStatusError>,
    pub last_transcript: Option<String>,
    pub last_test_transcript: Option<String>,
    pub last_test_error: Option<String>,
}

impl Default for CachedDaemonStatus {
    fn default() -> Self {
        Self {
            state: AppState::Idle,
            detail: None,
            error: None,
            last_transcript: None,
            last_test_transcript: None,
            last_test_error: None,
        }
    }
}

enum ActiveModelOp {
    Downloading(Arc<AtomicBool>),
    Deleting,
}

/// RAII guard removing an in-flight model operation (download or delete) on completion, error, or drop.
pub struct ModelOpGuard {
    model_id: String,
    active: Arc<Mutex<HashMap<String, ActiveModelOp>>>,
    cancel_flag: Option<Arc<AtomicBool>>,
}

impl ModelOpGuard {
    pub fn cancel_flag(&self) -> Option<&AtomicBool> {
        self.cancel_flag.as_deref()
    }
}

impl Drop for ModelOpGuard {
    fn drop(&mut self) {
        let mut guard = self.active.lock().unwrap_or_else(|e| e.into_inner());
        guard.remove(&self.model_id);
    }
}

/// Application state managed by Tauri (`app.manage(DesktopState)`).
pub struct DesktopState {
    pub config_dir: PathBuf,
    pub paths: AppPaths,
    pub preferences: Arc<PreferencesStore>,
    pub history: Arc<HistoryStore>,
    pub vocabulary: Arc<VocabularyStore>,
    pub prompts: Arc<PromptsStore>,
    pub permissions: Arc<dyn PermissionsChecker>,
    pub local_asr: LocalAsrManager,
    pub downloader: ModelDownloader,
    pub daemon: DaemonHandle,
    pub backend: LiveDictationBackend,
    pub overlay: OverlayHandle,
    pub updater: Arc<UpdateController>,
    active_model_ops: Arc<Mutex<HashMap<String, ActiveModelOp>>>,
    status_cache: Mutex<CachedDaemonStatus>,
    pending_route: Mutex<String>,
    quitting: AtomicBool,
}

impl DesktopState {
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        config_dir: PathBuf,
        preferences: Arc<PreferencesStore>,
        permissions: Arc<dyn PermissionsChecker>,
        local_asr: LocalAsrManager,
        downloader: ModelDownloader,
        daemon: DaemonHandle,
        backend: LiveDictationBackend,
        overlay: OverlayHandle,
        updater: Arc<UpdateController>,
    ) -> Self {
        let initial_state = daemon.state();
        let paths = AppPaths::new(&config_dir);
        // Reuse the daemon's HistoryStore so UI deletes/clears serialize against daemon appends.
        let history = daemon.history_store();
        let vocabulary = Arc::new(VocabularyStore::new(&config_dir));
        let prompts = Arc::new(PromptsStore::new(&config_dir));
        Self {
            config_dir,
            paths,
            preferences,
            history,
            vocabulary,
            prompts,
            permissions,
            local_asr,
            downloader,
            daemon,
            backend,
            overlay,
            updater,
            active_model_ops: Arc::new(Mutex::new(HashMap::new())),
            status_cache: Mutex::new(CachedDaemonStatus {
                state: initial_state,
                ..CachedDaemonStatus::default()
            }),
            pending_route: Mutex::new("general".to_owned()),
            quitting: AtomicBool::new(false),
        }
    }

    pub fn set_quitting(&self) {
        self.quitting.store(true, Ordering::Release);
    }

    pub fn is_quitting(&self) -> bool {
        self.quitting.load(Ordering::Acquire)
    }

    pub fn set_pending_route(&self, route: impl Into<String>) {
        let mut guard = self.pending_route.lock().unwrap_or_else(|e| e.into_inner());
        *guard = route.into();
    }

    pub fn pending_route(&self) -> String {
        self.pending_route
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .clone()
    }

    pub fn cached_status(&self) -> CachedDaemonStatus {
        self.status_cache
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .clone()
    }

    pub fn update_status(&self, f: impl FnOnce(&mut CachedDaemonStatus)) {
        let mut guard = self.status_cache.lock().unwrap_or_else(|e| e.into_inner());
        f(&mut guard);
    }

    /// Register an in-flight download guard; rejects concurrent download or deletion of the same model.
    pub fn begin_download(&self, model_id: &str) -> Result<ModelOpGuard, String> {
        let mut guard = self
            .active_model_ops
            .lock()
            .unwrap_or_else(|e| e.into_inner());
        match guard.get(model_id) {
            Some(ActiveModelOp::Downloading(_)) => {
                return Err(format!("Model '{model_id}' is already downloading"));
            }
            Some(ActiveModelOp::Deleting) => {
                return Err(format!("Model '{model_id}' is currently being deleted"));
            }
            None => {}
        }
        let flag = Arc::new(AtomicBool::new(false));
        guard.insert(
            model_id.to_owned(),
            ActiveModelOp::Downloading(Arc::clone(&flag)),
        );
        Ok(ModelOpGuard {
            model_id: model_id.to_owned(),
            active: Arc::clone(&self.active_model_ops),
            cancel_flag: Some(flag),
        })
    }

    /// Register an exclusive deletion guard; rejects while downloading or deleting the same model.
    pub fn begin_delete(&self, model_id: &str) -> Result<ModelOpGuard, String> {
        let mut guard = self
            .active_model_ops
            .lock()
            .unwrap_or_else(|e| e.into_inner());
        match guard.get(model_id) {
            Some(ActiveModelOp::Downloading(_)) => {
                return Err(format!(
                    "Model '{model_id}' is currently downloading; cancel the download first"
                ));
            }
            Some(ActiveModelOp::Deleting) => {
                return Err(format!("Model '{model_id}' is already being deleted"));
            }
            None => {}
        }
        guard.insert(model_id.to_owned(), ActiveModelOp::Deleting);
        Ok(ModelOpGuard {
            model_id: model_id.to_owned(),
            active: Arc::clone(&self.active_model_ops),
            cancel_flag: None,
        })
    }

    /// Signal cancellation for an in-flight download, returning `true` if one was active.
    pub fn cancel_download(&self, model_id: &str) -> bool {
        let guard = self
            .active_model_ops
            .lock()
            .unwrap_or_else(|e| e.into_inner());
        if let Some(ActiveModelOp::Downloading(flag)) = guard.get(model_id) {
            flag.store(true, Ordering::Release);
            true
        } else {
            false
        }
    }

    /// Cancel all in-flight downloads (called during application shutdown).
    pub fn cancel_all_downloads(&self) {
        let guard = self
            .active_model_ops
            .lock()
            .unwrap_or_else(|e| e.into_inner());
        for op in guard.values() {
            if let ActiveModelOp::Downloading(flag) = op {
                flag.store(true, Ordering::Release);
            }
        }
    }

    pub fn active_download_ids(&self) -> Vec<String> {
        let mut ids: Vec<String> = self
            .active_model_ops
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .iter()
            .filter(|(_, op)| matches!(op, ActiveModelOp::Downloading(_)))
            .map(|(id, _)| id.clone())
            .collect();
        ids.sort();
        ids
    }
}
