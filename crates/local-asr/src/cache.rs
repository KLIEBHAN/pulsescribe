//! Local model cache discovery with 1:1 Hugging Face Hub cache coexistence.
//!
//! Resolves models in two locations without duplicating gigabytes of weights:
//! 1. PulseScribe's managed cache (`~/.pulsescribe/models/<model_id>/`)
//! 2. Existing Python/Hugging Face Hub snapshots (`~/.cache/huggingface/hub/models--<org>--<repo>/snapshots/<rev>/`)

use std::fs;
use std::io;
use std::path::{Path, PathBuf};

use pulsescribe_core::paths::{home_dir, AppPaths};
use serde::{Deserialize, Serialize};

use crate::catalog::{
    catalog_models, validate_hf_repo, validate_safe_filename, validate_safe_rel_path,
    LocalEngineKind, ModelSpec,
};
use crate::LocalAsrError;

/// Subdirectory under `~/.pulsescribe` storing Rust-managed local ASR models.
pub const PULSESCRIBE_MODELS_SUBDIR: &str = "models";

/// Hidden marker file persisted inside `~/.pulsescribe/models/<id>/` recording the installed
/// revision key once all model files have been downloaded and verified.
pub(crate) const REVISION_MARKER_FILE: &str = ".pulsescribe-revision";

/// Hidden marker written while a revision download/refresh is in progress so retries can resume
/// already-downloaded files for the same revision without reusing stale files from an older one.
pub(crate) const REVISION_PENDING_FILE: &str = ".pulsescribe-revision.pending";

/// Where an installed local model's weights were resolved from.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ModelInstallSource {
    /// Downloaded into `~/.pulsescribe/models/<model_id>/`.
    PulseScribeCache,
    /// Reused directly from an existing `~/.cache/huggingface/hub/` snapshot.
    HuggingFaceHubCache,
}

/// Verified on-disk model directory with every required file present and non-empty.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct InstalledModel {
    pub model_id: String,
    pub dir: PathBuf,
    pub source: ModelInstallSource,
    pub total_bytes: u64,
}

impl InstalledModel {
    /// Return the path to a specific file inside this installed model directory.
    pub fn file_path(&self, filename: &str) -> PathBuf {
        self.dir.join(filename)
    }

    /// Return the primary model file (e.g., the `.bin` file for Whisper models).
    pub fn primary_file(&self, spec: &ModelSpec) -> Option<PathBuf> {
        spec.files.first().map(|f| self.dir.join(f))
    }
}

/// Serializable UI summary for a catalog model's installation state.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ModelStatus {
    pub id: String,
    pub display_name: String,
    pub engine: LocalEngineKind,
    pub approx_size_mb: u32,
    pub installed: bool,
    pub source: Option<ModelInstallSource>,
    pub installed_bytes: Option<u64>,
    pub path: Option<PathBuf>,
}

/// Model cache locator supporting both `~/.pulsescribe/models/` and `~/.cache/huggingface/hub/`.
#[derive(Debug, Clone)]
pub struct ModelCache {
    models_dir: PathBuf,
    hf_hub_cache_dir: Option<PathBuf>,
}

impl ModelCache {
    /// Construct a cache bound to `AppPaths` plus the standard Hugging Face Hub cache directory.
    pub fn from_app_paths(paths: &AppPaths) -> Self {
        Self {
            models_dir: paths.config_dir().join(PULSESCRIBE_MODELS_SUBDIR),
            hf_hub_cache_dir: default_hf_hub_cache_dir(),
        }
    }

    /// Construct a cache with explicit directories (ideal for isolated tests).
    pub fn new(models_dir: impl Into<PathBuf>, hf_hub_cache_dir: Option<PathBuf>) -> Self {
        Self {
            models_dir: models_dir.into(),
            hf_hub_cache_dir,
        }
    }

    /// Managed directory where PulseScribe stores downloaded weights (`~/.pulsescribe/models/`).
    pub fn models_dir(&self) -> &Path {
        &self.models_dir
    }

    /// Directory for a specific model inside PulseScribe's managed cache.
    pub fn model_dir(&self, spec: &ModelSpec) -> PathBuf {
        self.models_dir.join(spec.id)
    }

    /// Locate an already-installed copy of `spec` in PulseScribe's cache or Hugging Face's cache.
    pub fn find_installed(&self, spec: &ModelSpec) -> Option<InstalledModel> {
        if validate_safe_filename(spec.id).is_err() {
            return None;
        }
        let managed_dir = self.model_dir(spec);
        if managed_revision_matches(&managed_dir, spec) {
            if let Some(total_bytes) = verify_all_files_present(&managed_dir, spec.files, false) {
                return Some(InstalledModel {
                    model_id: spec.id.to_owned(),
                    dir: managed_dir,
                    source: ModelInstallSource::PulseScribeCache,
                    total_bytes,
                });
            }
        }

        if spec.direct_base_url.is_none() {
            if let Some(hf_root) = self.hf_hub_cache_dir.as_deref() {
                if let Some((snapshot_dir, total_bytes)) = find_hf_snapshot(hf_root, spec) {
                    return Some(InstalledModel {
                        model_id: spec.id.to_owned(),
                        dir: snapshot_dir,
                        source: ModelInstallSource::HuggingFaceHubCache,
                        total_bytes,
                    });
                }
            }
        }

        None
    }

    /// Return installation statuses for every curated model in [`catalog_models`].
    pub fn list_statuses(&self) -> Vec<ModelStatus> {
        catalog_models()
            .iter()
            .map(|spec| {
                let installed = self.find_installed(spec);
                ModelStatus {
                    id: spec.id.to_owned(),
                    display_name: spec.display_name.to_owned(),
                    engine: spec.engine,
                    approx_size_mb: spec.approx_size_mb,
                    installed: installed.is_some(),
                    source: installed.as_ref().map(|m| m.source),
                    installed_bytes: installed.as_ref().map(|m| m.total_bytes),
                    path: installed.map(|m| m.dir),
                }
            })
            .collect()
    }

    /// Delete a model from PulseScribe's managed cache (`~/.pulsescribe/models/<id>`).
    ///
    /// Never modifies or deletes files inside the shared `~/.cache/huggingface/hub/` cache,
    /// validates `spec.id` against traversal, and unlinks symlinks without recursing into targets.
    pub fn remove_managed(&self, spec: &ModelSpec) -> Result<bool, LocalAsrError> {
        validate_safe_filename(spec.id)?;
        let target = self.model_dir(spec);
        let meta = match fs::symlink_metadata(&target) {
            Ok(meta) => meta,
            Err(err) if err.kind() == io::ErrorKind::NotFound => return Ok(false),
            Err(err) => return Err(LocalAsrError::Io(err)),
        };

        let remove_res = if meta.file_type().is_symlink() {
            match fs::remove_file(&target) {
                Ok(()) => Ok(()),
                Err(err) if err.kind() == io::ErrorKind::NotFound => return Ok(false),
                // Windows directory symlinks require `remove_dir` rather than `remove_file`.
                Err(_) if cfg!(windows) => fs::remove_dir(&target),
                Err(err) => Err(err),
            }
        } else if meta.is_dir() {
            fs::remove_dir_all(&target)
        } else {
            fs::remove_file(&target)
        };

        match remove_res {
            Ok(()) => Ok(true),
            Err(err) if err.kind() == io::ErrorKind::NotFound => Ok(false),
            Err(err) => Err(LocalAsrError::Io(err)),
        }
    }
}

fn default_hf_hub_cache_dir() -> Option<PathBuf> {
    if let Some(explicit) = std::env::var_os("HF_HUB_CACHE").filter(|v| !v.is_empty()) {
        return Some(PathBuf::from(explicit));
    }
    if let Some(legacy) = std::env::var_os("HUGGINGFACE_HUB_CACHE").filter(|v| !v.is_empty()) {
        return Some(PathBuf::from(legacy));
    }
    if let Some(hf_home) = std::env::var_os("HF_HOME").filter(|v| !v.is_empty()) {
        return Some(PathBuf::from(hf_home).join("hub"));
    }
    if let Some(xdg) = std::env::var_os("XDG_CACHE_HOME").filter(|v| !v.is_empty()) {
        return Some(PathBuf::from(xdg).join("huggingface").join("hub"));
    }
    Some(home_dir()?.join(".cache").join("huggingface").join("hub"))
}

fn hf_repo_cache_dir(hf_hub_root: &Path, hf_repo: &str) -> Option<PathBuf> {
    validate_hf_repo(hf_repo).ok()?;
    let encoded = hf_repo.replace('/', "--");
    Some(hf_hub_root.join(format!("models--{encoded}")))
}

fn find_hf_snapshot(hf_hub_root: &Path, spec: &ModelSpec) -> Option<(PathBuf, u64)> {
    validate_safe_filename(spec.hf_revision).ok()?;
    let repo_dir = hf_repo_cache_dir(hf_hub_root, spec.hf_repo)?;
    let snapshots_dir = repo_dir.join("snapshots");
    if !snapshots_dir.is_dir() {
        return None;
    }

    // Exact pinned commit revision (e.g. Parakeet/Canary/Nemotron) matches directly.
    let direct = snapshots_dir.join(spec.hf_revision);
    if let Some(total_bytes) = verify_all_files_present(&direct, spec.files, true) {
        return Some((direct, total_bytes));
    }

    // For branch refs like `"main"`, resolve `refs/<revision>` -> commit hash first.
    let ref_file = repo_dir.join("refs").join(spec.hf_revision);
    if let Ok(commit) = fs::read_to_string(&ref_file) {
        let trimmed = commit.trim();
        if validate_safe_filename(trimmed).is_ok() {
            let pointed = snapshots_dir.join(trimmed);
            if let Some(total_bytes) = verify_all_files_present(&pointed, spec.files, true) {
                return Some((pointed, total_bytes));
            }
        }
    }

    None
}

/// Read a regular (non-symlink) revision marker file, returning its trimmed contents.
pub(crate) fn read_regular_marker(path: &Path) -> Option<String> {
    let meta = fs::symlink_metadata(path).ok()?;
    if meta.file_type().is_symlink() || !meta.is_file() {
        return None;
    }
    fs::read_to_string(path).ok().map(|s| s.trim().to_owned())
}

/// Returns `true` when the managed cache directory's `.pulsescribe-revision` marker matches
/// `spec.revision_key()`, or when no marker is present and `spec.requires_revision_marker` is
/// `false` (preserving legacy pre-marker installations for unchanged models).
pub(crate) fn managed_revision_matches(dir: &Path, spec: &ModelSpec) -> bool {
    let marker = dir.join(REVISION_MARKER_FILE);
    match fs::symlink_metadata(&marker) {
        Ok(meta) => {
            if meta.file_type().is_symlink() || !meta.is_file() {
                return false;
            }
            fs::read_to_string(&marker)
                .map(|raw| raw.trim() == spec.revision_key())
                .unwrap_or(false)
        }
        Err(err) if err.kind() == io::ErrorKind::NotFound => !spec.requires_revision_marker,
        Err(_) => false,
    }
}

/// Verify that every required file in `files` exists inside `dir`, is a regular file (optionally
/// following symlinks for Hugging Face Hub's blob store), and has non-zero size.
pub(crate) fn verify_all_files_present(
    dir: &Path,
    files: &[&str],
    follow_symlinks: bool,
) -> Option<u64> {
    if files.is_empty() {
        return None;
    }
    let dir_meta = if follow_symlinks {
        fs::metadata(dir).ok()?
    } else {
        fs::symlink_metadata(dir).ok()?
    };
    if !dir_meta.is_dir() {
        return None;
    }

    let mut total = 0_u64;
    for &file in files {
        if validate_safe_rel_path(file).is_err() {
            return None;
        }
        let mut cursor = dir.to_path_buf();
        let segments: Vec<&str> = file.split('/').collect();
        for (idx, segment) in segments.iter().enumerate() {
            cursor.push(segment);
            let is_leaf = idx + 1 == segments.len();
            let meta = if follow_symlinks {
                fs::metadata(&cursor).ok()?
            } else {
                fs::symlink_metadata(&cursor).ok()?
            };
            if !follow_symlinks && meta.file_type().is_symlink() {
                return None;
            }
            if is_leaf {
                if !meta.is_file() || meta.len() == 0 {
                    return None;
                }
                total = total.saturating_add(meta.len());
            } else if !meta.is_dir() {
                return None;
            }
        }
    }
    Some(total)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::catalog::resolve_model_spec;
    use std::sync::atomic::{AtomicU64, Ordering};
    use std::time::{SystemTime, UNIX_EPOCH};

    static CACHE_TEST_SEQ: AtomicU64 = AtomicU64::new(0);

    struct TempDir(PathBuf);

    impl TempDir {
        fn new(tag: &str) -> Self {
            let seq = CACHE_TEST_SEQ.fetch_add(1, Ordering::Relaxed);
            let nanos = SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap()
                .as_nanos();
            let path = std::env::temp_dir().join(format!(
                "pulsescribe-local-cache-{tag}-{}-{nanos}-{seq}",
                std::process::id()
            ));
            fs::create_dir_all(&path).unwrap();
            Self(path)
        }
    }

    impl Drop for TempDir {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }

    fn write_dummy_files(root: &Path, files: &[&str], bytes: &[u8]) {
        for &file in files {
            let path = root.join(file);
            if let Some(parent) = path.parent() {
                fs::create_dir_all(parent).unwrap();
            }
            fs::write(path, bytes).unwrap();
        }
    }

    #[test]
    fn finds_managed_cache_and_reuses_python_hf_hub_snapshots() {
        let dir = TempDir::new("hf-reuse");
        let managed_root = dir.0.join("managed");
        let hf_root = dir.0.join("hf_hub");
        let cache = ModelCache::new(&managed_root, Some(hf_root.clone()));

        let canary = resolve_model_spec(Some("canary-1b-v2")).unwrap();
        assert!(cache.find_installed(canary).is_none());

        // Populate a pinned Hugging Face snapshot matching Python's `huggingface_hub` layout.
        let hf_snap = hf_root
            .join(format!("models--{}", canary.hf_repo.replace('/', "--")))
            .join("snapshots")
            .join(canary.hf_revision);
        write_dummy_files(&hf_snap, canary.files, b"weights");

        let found_hf = cache.find_installed(canary).unwrap();
        assert_eq!(found_hf.source, ModelInstallSource::HuggingFaceHubCache);
        assert_eq!(found_hf.dir, hf_snap);
        assert_eq!(found_hf.total_bytes, (canary.files.len() * 7) as u64);

        // Managed cache takes priority when also present; removing managed leaves HF untouched.
        let managed_dir = cache.model_dir(canary);
        write_dummy_files(&managed_dir, canary.files, b"managed-weights");
        let found_managed = cache.find_installed(canary).unwrap();
        assert_eq!(found_managed.source, ModelInstallSource::PulseScribeCache);
        assert!(cache.remove_managed(canary).unwrap());
        assert_eq!(
            cache.find_installed(canary).unwrap().source,
            ModelInstallSource::HuggingFaceHubCache
        );
    }

    #[test]
    fn resolves_branch_ref_snapshots_for_whisper_ggml() {
        let dir = TempDir::new("whisper-ref");
        let hf_root = dir.0.join("hf_hub");
        let cache = ModelCache::new(dir.0.join("managed"), Some(hf_root.clone()));
        let turbo = resolve_model_spec(Some("turbo")).unwrap();

        let repo_dir = hf_root.join("models--ggerganov--whisper.cpp");
        let commit = "deadbeef1234";
        fs::create_dir_all(repo_dir.join("refs")).unwrap();
        fs::write(repo_dir.join("refs").join("main"), format!("{commit}\n")).unwrap();
        let snap = repo_dir.join("snapshots").join(commit);
        fs::create_dir_all(&snap).unwrap();
        fs::write(snap.join(turbo.files[0]), b"ggml-bytes").unwrap();

        let found = cache.find_installed(turbo).unwrap();
        assert_eq!(found.source, ModelInstallSource::HuggingFaceHubCache);
        assert_eq!(
            found.primary_file(turbo).unwrap(),
            snap.join(turbo.files[0])
        );
    }

    #[test]
    fn remove_managed_rejects_unsafe_ids_and_does_not_recurse_into_symlinks() {
        let dir = TempDir::new("remove-safe");
        let managed = dir.0.join("managed");
        fs::create_dir_all(&managed).unwrap();
        let cache = ModelCache::new(&managed, None);

        let mut bad = *resolve_model_spec(Some("tiny")).unwrap();
        bad.id = "..";
        assert!(cache.remove_managed(&bad).is_err());
        bad.id = "";
        assert!(cache.remove_managed(&bad).is_err());

        #[cfg(unix)]
        {
            let external = dir.0.join("external_target");
            fs::create_dir_all(&external).unwrap();
            let sentinel = external.join("keep.txt");
            fs::write(&sentinel, b"do not delete").unwrap();

            let tiny = resolve_model_spec(Some("tiny")).unwrap();
            std::os::unix::fs::symlink(&external, cache.model_dir(tiny)).unwrap();

            assert!(cache.find_installed(tiny).is_none());
            assert!(cache.remove_managed(tiny).unwrap());
            assert!(sentinel.exists());

            // Symlinked intermediate subdirectory inside managed cache must also be rejected.
            let qwen3 = resolve_model_spec(Some("qwen3-asr-0.6b")).unwrap();
            let qwen_dir = cache.model_dir(qwen3);
            fs::create_dir_all(&qwen_dir).unwrap();
            for flat in [
                "conv_frontend.onnx",
                "encoder.int8.onnx",
                "decoder.int8.onnx",
            ] {
                fs::write(qwen_dir.join(flat), b"weights").unwrap();
            }
            let ext_tok = dir.0.join("external_tokenizer");
            fs::create_dir_all(&ext_tok).unwrap();
            for f in ["merges.txt", "tokenizer_config.json", "vocab.json"] {
                fs::write(ext_tok.join(f), b"tok").unwrap();
            }
            std::os::unix::fs::symlink(&ext_tok, qwen_dir.join("tokenizer")).unwrap();
            assert!(cache.find_installed(qwen3).is_none());
        }
    }

    #[test]
    fn revision_marker_detects_stale_parakeet_ultra_while_preserving_legacy_models() {
        let dir = TempDir::new("revision-marker");
        let cache = ModelCache::new(dir.0.join("managed"), None);

        // `parakeet-ultra` requires a matching `.pulsescribe-revision` marker so older
        // installs without a marker (or from another backend revision) refresh automatically.
        let ultra = resolve_model_spec(Some("parakeet-ultra")).unwrap();
        let ultra_dir = cache.model_dir(ultra);
        write_dummy_files(&ultra_dir, ultra.files, b"old-weights");
        assert!(cache.find_installed(ultra).is_none());

        fs::write(ultra_dir.join(REVISION_MARKER_FILE), "stale-rev\n").unwrap();
        assert!(cache.find_installed(ultra).is_none());

        fs::write(
            ultra_dir.join(REVISION_MARKER_FILE),
            format!("{}\n", ultra.revision_key()),
        )
        .unwrap();
        assert!(cache.find_installed(ultra).is_some());

        // Models without `requires_revision_marker` keep working on legacy installs without a
        // marker, while still invalidating an explicitly mismatched marker.
        let canary = resolve_model_spec(Some("canary-1b-v2")).unwrap();
        let canary_dir = cache.model_dir(canary);
        write_dummy_files(&canary_dir, canary.files, b"canary-weights");
        assert!(cache.find_installed(canary).is_some());
        fs::write(canary_dir.join(REVISION_MARKER_FILE), "wrong-rev\n").unwrap();
        assert!(cache.find_installed(canary).is_none());
    }
}
