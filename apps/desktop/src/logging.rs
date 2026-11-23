//! Size-capped rotating file logger writing to `~/.pulsescribe/logs/pulsescribe.log`.
//!
//! Ensures packaged GUI builds (where `stderr` is discarded by the OS) still record runtime
//! diagnostics consumed by `export_diagnostics`, while teeing to `stderr` for terminal sessions.

use std::fs::{self, File, OpenOptions};
use std::io::{self, Write};
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};

use pulsescribe_core::paths::AppPaths;
use tracing_subscriber::fmt::writer::MakeWriterExt;
use tracing_subscriber::fmt::MakeWriter;
use tracing_subscriber::EnvFilter;

/// Rotate `pulsescribe.log` → `pulsescribe.log.1` once the active log exceeds 5 MiB.
pub const MAX_LOG_FILE_BYTES: u64 = 5 * 1024 * 1024;

struct RotatingFileInner {
    path: PathBuf,
    backup_path: PathBuf,
    file: Option<File>,
    bytes_written: u64,
    max_bytes: u64,
}

impl RotatingFileInner {
    fn open(path: PathBuf, max_bytes: u64) -> io::Result<Self> {
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent)?;
            #[cfg(unix)]
            {
                use std::os::unix::fs::PermissionsExt;
                let _ = fs::set_permissions(parent, fs::Permissions::from_mode(0o700));
            }
        }
        let file = open_log_file(&path, false)?;
        let bytes_written = file.metadata().map(|m| m.len()).unwrap_or(0);
        let mut backup_os = path.as_os_str().to_os_string();
        backup_os.push(".1");
        let backup_path = PathBuf::from(backup_os);
        Ok(Self {
            path,
            backup_path,
            file: Some(file),
            bytes_written,
            max_bytes: max_bytes.max(1024),
        })
    }

    fn rotate(&mut self) -> io::Result<()> {
        if let Some(mut file) = self.file.take() {
            let _ = file.flush();
        }
        // Remove existing backup first so `rename` also succeeds on Windows.
        let _ = fs::remove_file(&self.backup_path);
        match fs::rename(&self.path, &self.backup_path) {
            Ok(()) => {
                let fresh = open_log_file(&self.path, true)?;
                self.file = Some(fresh);
                self.bytes_written = 0;
                Ok(())
            }
            Err(err) => {
                // Preserve active log contents when rotation fails (e.g., obstructed/locked
                // backup path) by reopening in append mode instead of truncating.
                if let Ok(reopened) = open_log_file(&self.path, false) {
                    self.bytes_written = reopened.metadata().map(|m| m.len()).unwrap_or(0);
                    self.file = Some(reopened);
                }
                Err(err)
            }
        }
    }

    fn write_bytes(&mut self, buf: &[u8]) -> io::Result<usize> {
        if self.bytes_written.saturating_add(buf.len() as u64) > self.max_bytes
            && self.bytes_written > 0
        {
            let _ = self.rotate();
        }
        let file = match self.file.as_mut() {
            Some(f) => f,
            None => {
                let reopened = open_log_file(&self.path, false)?;
                self.bytes_written = reopened.metadata().map(|m| m.len()).unwrap_or(0);
                self.file = Some(reopened);
                self.file.as_mut().expect("file initialized above")
            }
        };
        let written = file.write(buf)?;
        self.bytes_written = self.bytes_written.saturating_add(written as u64);
        Ok(written)
    }

    fn flush(&mut self) -> io::Result<()> {
        if let Some(file) = self.file.as_mut() {
            file.flush()?;
        }
        Ok(())
    }
}

fn open_log_file(path: &Path, truncate: bool) -> io::Result<File> {
    let mut opts = OpenOptions::new();
    opts.create(true).write(true);
    if truncate {
        opts.truncate(true);
    } else {
        opts.append(true);
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        opts.mode(0o600);
    }
    let file = opts.open(path)?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let _ = fs::set_permissions(path, fs::Permissions::from_mode(0o600));
    }
    Ok(file)
}

/// Thread-safe `tracing_subscriber` writer backed by a size-capped rotating log file.
#[derive(Clone)]
pub struct RotatingLogWriter {
    inner: Arc<Mutex<RotatingFileInner>>,
}

impl RotatingLogWriter {
    pub fn open(path: PathBuf, max_bytes: u64) -> io::Result<Self> {
        Ok(Self {
            inner: Arc::new(Mutex::new(RotatingFileInner::open(path, max_bytes)?)),
        })
    }
}

pub struct RotatingLogGuard<'a>(&'a RotatingLogWriter);

impl Write for RotatingLogGuard<'_> {
    fn write(&mut self, buf: &[u8]) -> io::Result<usize> {
        let mut inner = self.0.inner.lock().unwrap_or_else(|e| e.into_inner());
        inner.write_bytes(buf)
    }

    fn flush(&mut self) -> io::Result<()> {
        let mut inner = self.0.inner.lock().unwrap_or_else(|e| e.into_inner());
        inner.flush()
    }
}

impl<'a> MakeWriter<'a> for RotatingLogWriter {
    type Writer = RotatingLogGuard<'a>;

    fn make_writer(&'a self) -> Self::Writer {
        RotatingLogGuard(self)
    }
}

/// Initialize global `tracing` subscriber writing to `~/.pulsescribe/logs/pulsescribe.log`
/// (with 5 MiB rotation) and `stderr`. Falls back to `stderr`-only if the log file cannot open.
pub fn init_desktop_logging(config_dir: &Path) {
    let filter = EnvFilter::try_from_default_env().unwrap_or_else(|_| EnvFilter::new("info"));
    let log_path = AppPaths::new(config_dir).log_file();
    match RotatingLogWriter::open(log_path, MAX_LOG_FILE_BYTES) {
        Ok(file_writer) => {
            let _ = tracing_subscriber::fmt()
                .with_env_filter(filter)
                .with_target(false)
                .with_ansi(false)
                .with_writer(file_writer.and(io::stderr))
                .try_init();
        }
        Err(_) => {
            let _ = tracing_subscriber::fmt()
                .with_env_filter(filter)
                .with_target(false)
                .with_writer(io::stderr)
                .try_init();
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::{SystemTime, UNIX_EPOCH};

    fn temp_log_path(tag: &str) -> PathBuf {
        let nanos = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        std::env::temp_dir()
            .join(format!("ps-log-{tag}-{}-{nanos}", std::process::id()))
            .join("logs")
            .join("pulsescribe.log")
    }

    #[test]
    fn rotating_log_writer_creates_restricted_file_and_rotates_at_cap() {
        let log_path = temp_log_path("rotate");
        let backup_path = PathBuf::from(format!("{}.1", log_path.display()));
        let writer = RotatingLogWriter::open(log_path.clone(), 1024).unwrap();

        {
            let mut w = writer.make_writer();
            w.write_all(&vec![b'a'; 700]).unwrap();
            w.flush().unwrap();
        }
        assert!(log_path.is_file());
        assert!(!backup_path.exists());

        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let mode = fs::metadata(&log_path).unwrap().permissions().mode() & 0o777;
            assert_eq!(mode, 0o600);
        }

        // Exceeding 1024 bytes rotates the previous contents to `.1` and writes fresh bytes.
        {
            let mut w = writer.make_writer();
            w.write_all(&vec![b'b'; 500]).unwrap();
            w.flush().unwrap();
        }
        assert_eq!(fs::read(&backup_path).unwrap().len(), 700);
        assert_eq!(fs::read(&log_path).unwrap().len(), 500);

        let _ = fs::remove_dir_all(log_path.parent().unwrap().parent().unwrap());
    }

    #[test]
    fn rotating_log_writer_preserves_active_log_when_rename_fails() {
        let log_path = temp_log_path("obstructed");
        let backup_path = PathBuf::from(format!("{}.1", log_path.display()));
        let writer = RotatingLogWriter::open(log_path.clone(), 1024).unwrap();

        {
            let mut w = writer.make_writer();
            w.write_all(&vec![b'a'; 700]).unwrap();
            w.flush().unwrap();
        }
        // Obstruct `.1` with a directory so both `remove_file` and `rename` fail.
        fs::create_dir_all(&backup_path).unwrap();

        {
            let mut w = writer.make_writer();
            w.write_all(&vec![b'b'; 500]).unwrap();
            w.flush().unwrap();
        }
        let contents = fs::read(&log_path).unwrap();
        assert_eq!(contents.len(), 1200);
        assert!(contents.starts_with(&[b'a'; 16]));
        assert!(contents.ends_with(&[b'b'; 16]));

        let _ = fs::remove_dir_all(log_path.parent().unwrap().parent().unwrap());
    }
}
