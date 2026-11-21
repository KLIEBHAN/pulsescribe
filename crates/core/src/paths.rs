//! Standard `~/.pulsescribe/` directory layout and file metadata signatures.
//!
//! Mirrors `utils/paths.py` and `utils/file_signatures.py`.

use std::env;
use std::fs;
use std::io;
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

/// Stable file metadata tuple `(mtime_ns, size, ctime_ns)` for lightweight cache invalidation.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Default)]
pub struct FileSignature {
    pub mtime_ns: i128,
    pub size: u64,
    pub ctime_ns: i128,
}

/// Convert a `SystemTime` into signed nanoseconds relative to `UNIX_EPOCH`,
/// preserving negative timestamps for valid pre-1970 file times.
fn system_time_to_ns(time: SystemTime) -> i128 {
    match time.duration_since(UNIX_EPOCH) {
        Ok(duration) => duration.as_nanos() as i128,
        Err(err) => -(err.duration().as_nanos() as i128),
    }
}

/// Build a stable file signature from already-fetched filesystem metadata
/// (for example from an open `File::metadata()` handle to avoid stat/open TOCTOU).
pub fn file_signature_from_metadata(meta: &fs::Metadata) -> FileSignature {
    let mtime_ns = meta.modified().map(system_time_to_ns).unwrap_or(0);
    let ctime_ns = metadata_ctime_ns(meta);

    FileSignature {
        mtime_ns,
        size: meta.len(),
        ctime_ns,
    }
}

/// Build a stable file signature from filesystem metadata.
pub fn build_file_signature(path: &Path) -> io::Result<FileSignature> {
    let meta = fs::metadata(path)?;
    Ok(file_signature_from_metadata(&meta))
}

#[cfg(unix)]
fn metadata_ctime_ns(meta: &fs::Metadata) -> i128 {
    use std::os::unix::fs::MetadataExt;
    i128::from(meta.ctime()) * 1_000_000_000 + i128::from(meta.ctime_nsec())
}

#[cfg(not(unix))]
fn metadata_ctime_ns(meta: &fs::Metadata) -> i128 {
    meta.created().map(system_time_to_ns).unwrap_or(0)
}

/// Resolve the current user's home directory from standard OS environment variables.
pub fn home_dir() -> Option<PathBuf> {
    #[cfg(windows)]
    {
        if let Some(profile) = env::var_os("USERPROFILE").filter(|v| !v.is_empty()) {
            return Some(PathBuf::from(profile));
        }
        let drive = env::var_os("HOMEDRIVE").filter(|v| !v.is_empty())?;
        let path = env::var_os("HOMEPATH").filter(|v| !v.is_empty())?;
        let mut combined = drive;
        combined.push(path);
        Some(PathBuf::from(combined))
    }
    #[cfg(not(windows))]
    {
        env::var_os("HOME")
            .filter(|v| !v.is_empty())
            .map(PathBuf::from)
    }
}

/// Canonical paths under `~/.pulsescribe/` plus resource path resolution.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AppPaths {
    config_dir: PathBuf,
}

impl AppPaths {
    /// Create an `AppPaths` rooted at an explicit configuration directory.
    pub fn new(config_dir: impl Into<PathBuf>) -> Self {
        Self {
            config_dir: config_dir.into(),
        }
    }

    /// Derive `~/.pulsescribe` from an explicit home directory.
    pub fn from_home(home: &Path) -> Self {
        Self::new(home.join(".pulsescribe"))
    }

    /// Derive `~/.pulsescribe` from the current user's home directory.
    pub fn default_user() -> Option<Self> {
        home_dir().map(|home| Self::from_home(&home))
    }

    pub fn config_dir(&self) -> &Path {
        &self.config_dir
    }

    pub fn env_file(&self) -> PathBuf {
        self.config_dir.join(".env")
    }

    pub fn preferences_file(&self) -> PathBuf {
        self.config_dir.join("preferences.json")
    }

    pub fn history_file(&self) -> PathBuf {
        self.config_dir.join("history.jsonl")
    }

    pub fn vocabulary_file(&self) -> PathBuf {
        self.config_dir.join("vocabulary.json")
    }

    pub fn prompts_file(&self) -> PathBuf {
        self.config_dir.join("prompts.toml")
    }

    pub fn logs_dir(&self) -> PathBuf {
        self.config_dir.join("logs")
    }

    pub fn log_file(&self) -> PathBuf {
        self.logs_dir().join("pulsescribe.log")
    }

    /// Resolve a relative resource path under a bundle or checkout base directory.
    pub fn resolve_resource(base_dir: &Path, relative_path: &str) -> PathBuf {
        base_dir.join(relative_path)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn app_paths_match_pulsescribe_layout() {
        let paths = AppPaths::from_home(Path::new("/tmp/test-home"));
        assert_eq!(paths.config_dir(), Path::new("/tmp/test-home/.pulsescribe"));
        assert_eq!(
            paths.env_file(),
            Path::new("/tmp/test-home/.pulsescribe/.env")
        );
        assert_eq!(
            paths.preferences_file(),
            Path::new("/tmp/test-home/.pulsescribe/preferences.json")
        );
        assert_eq!(
            paths.history_file(),
            Path::new("/tmp/test-home/.pulsescribe/history.jsonl")
        );
        assert_eq!(
            paths.vocabulary_file(),
            Path::new("/tmp/test-home/.pulsescribe/vocabulary.json")
        );
        assert_eq!(
            paths.prompts_file(),
            Path::new("/tmp/test-home/.pulsescribe/prompts.toml")
        );
        assert_eq!(
            paths.log_file(),
            Path::new("/tmp/test-home/.pulsescribe/logs/pulsescribe.log")
        );
        assert_eq!(
            AppPaths::resolve_resource(Path::new("/app"), "ui/assets/check.svg"),
            Path::new("/app/ui/assets/check.svg")
        );
    }

    #[test]
    fn system_time_to_ns_preserves_signed_pre_epoch_timestamps() {
        use std::time::Duration;

        // Use 100 ns multiples to match Windows FILETIME tick granularity.
        let post = UNIX_EPOCH + Duration::new(42, 500);
        assert_eq!(system_time_to_ns(post), 42_000_000_500);

        let pre = UNIX_EPOCH - Duration::new(10, 200);
        assert_eq!(system_time_to_ns(pre), -10_000_000_200);
    }
}
