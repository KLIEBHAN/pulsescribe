//! Crash-safe atomic file writing helpers.
//!
//! Mirrors `utils/atomic_io.py`: writes to a sibling temporary file in the same
//! directory and atomically renames it into place so config files are never truncated.

use std::fs::{self, File, OpenOptions};
use std::io::{self, Write};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};

static TEMP_COUNTER: AtomicU64 = AtomicU64::new(0);
const MAX_TEMP_ATTEMPTS: u32 = 64;

/// Write UTF-8 text atomically to `path`, creating parent directories as needed.
pub fn write_text_atomic(path: &Path, content: &str) -> io::Result<()> {
    write_text_atomic_impl(path, content, false)
}

/// Write UTF-8 text atomically to `path` with owner-only (`0600`) permissions on Unix.
///
/// Setting the mode on temporary file creation avoids any window where secret files
/// such as `~/.pulsescribe/.env` are readable by other local users.
pub fn write_text_atomic_restricted(path: &Path, content: &str) -> io::Result<()> {
    write_text_atomic_impl(path, content, true)
}

fn write_text_atomic_impl(path: &Path, content: &str, restricted: bool) -> io::Result<()> {
    let parent = match path.parent() {
        Some(parent) if !parent.as_os_str().is_empty() => parent,
        _ => Path::new("."),
    };
    fs::create_dir_all(parent)?;

    let file_name = path
        .file_name()
        .and_then(|name| name.to_str())
        .unwrap_or("file");

    let (mut tmp_file, tmp_path) = create_unique_temp_file(parent, file_name, restricted)?;

    let write_result = (|| -> io::Result<()> {
        tmp_file.write_all(content.as_bytes())?;
        tmp_file.sync_all()?;
        drop(tmp_file);
        replace_file(&tmp_path, path)
    })();

    if let Err(err) = write_result {
        let _ = fs::remove_file(&tmp_path);
        return Err(err);
    }

    Ok(())
}

fn create_unique_temp_file(
    parent: &Path,
    file_name: &str,
    restricted: bool,
) -> io::Result<(File, PathBuf)> {
    let pid = std::process::id();
    for _ in 0..MAX_TEMP_ATTEMPTS {
        let seq = TEMP_COUNTER.fetch_add(1, Ordering::Relaxed);
        let candidate = parent.join(format!(".{file_name}.{pid}.{seq}.tmp"));

        let mut opts = OpenOptions::new();
        opts.write(true).create_new(true);
        #[cfg(unix)]
        if restricted {
            use std::os::unix::fs::OpenOptionsExt;
            opts.mode(0o600);
        }
        #[cfg(not(unix))]
        let _ = restricted;

        match opts.open(&candidate) {
            Ok(file) => return Ok((file, candidate)),
            Err(err) if err.kind() == io::ErrorKind::AlreadyExists => continue,
            Err(err) => return Err(err),
        }
    }

    Err(io::Error::new(
        io::ErrorKind::AlreadyExists,
        "failed to allocate unique temporary file for atomic write",
    ))
}

fn replace_file(tmp_path: &Path, target: &Path) -> io::Result<()> {
    fs::rename(tmp_path, target)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::{SystemTime, UNIX_EPOCH};

    fn unique_test_dir(tag: &str) -> PathBuf {
        let nanos = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let dir = std::env::temp_dir().join(format!(
            "pulsescribe-core-atomic-{tag}-{}-{nanos}",
            std::process::id()
        ));
        fs::create_dir_all(&dir).unwrap();
        dir
    }

    #[test]
    fn write_text_atomic_creates_parents_and_overwrites_cleanly() {
        let root = unique_test_dir("overwrite");
        let target = root.join("nested").join("config.txt");

        write_text_atomic(&target, "first\n").unwrap();
        assert_eq!(fs::read_to_string(&target).unwrap(), "first\n");

        write_text_atomic(&target, "second\n").unwrap();
        assert_eq!(fs::read_to_string(&target).unwrap(), "second\n");

        let leftovers: Vec<_> = fs::read_dir(target.parent().unwrap())
            .unwrap()
            .map(|e| e.unwrap().file_name())
            .collect();
        assert_eq!(leftovers.len(), 1);

        let _ = fs::remove_dir_all(&root);
    }

    #[cfg(unix)]
    #[test]
    fn write_text_atomic_restricted_sets_0600_mode() {
        use std::os::unix::fs::PermissionsExt;

        let root = unique_test_dir("restricted");
        let target = root.join(".env");

        write_text_atomic_restricted(&target, "SECRET=1\n").unwrap();
        let mode = fs::metadata(&target).unwrap().permissions().mode() & 0o777;
        assert_eq!(mode, 0o600);

        let _ = fs::remove_dir_all(&root);
    }
}
