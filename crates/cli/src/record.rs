//! Interactive CLI microphone recording (`--record`) using `pulsescribe-audio`.

use std::fs::DirBuilder;
use std::io::{self, Write};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{Instant, SystemTime, UNIX_EPOCH};

use pulsescribe_audio::{AudioRecorder, TEMP_RECORDING_FILENAME};

use crate::CliError;

static RECORDING_COUNTER: AtomicU64 = AtomicU64::new(0);
const MAX_TEMP_DIR_ATTEMPTS: usize = 64;

/// RAII guard owning a private temporary directory (`0700` on Unix) and its recording WAV path.
///
/// Created before writing audio so failed WAV writes or interrupted status output always attempt
/// cleanup without ever placing microphone recordings in a world-readable shared directory.
pub struct TempRecordingGuard {
    dir: PathBuf,
    wav_path: PathBuf,
}

impl TempRecordingGuard {
    /// Atomically create an owner-only temporary directory and reserve its WAV file path.
    pub fn create_private() -> io::Result<Self> {
        let base = std::env::temp_dir();
        let pid = std::process::id();

        for _ in 0..MAX_TEMP_DIR_ATTEMPTS {
            let seq = RECORDING_COUNTER.fetch_add(1, Ordering::Relaxed);
            let nanos = SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .map(|d| d.subsec_nanos())
                .unwrap_or(0);
            let dir = base.join(format!("pulsescribe_rec_{pid}_{seq}_{nanos}"));

            let mut builder = DirBuilder::new();
            #[cfg(unix)]
            {
                use std::os::unix::fs::DirBuilderExt;
                builder.mode(0o700);
            }

            match builder.create(&dir) {
                Ok(()) => {
                    let wav_path = dir.join(TEMP_RECORDING_FILENAME);
                    return Ok(Self { dir, wav_path });
                }
                Err(err) if err.kind() == io::ErrorKind::AlreadyExists => continue,
                Err(err) => return Err(err),
            }
        }

        Err(io::Error::new(
            io::ErrorKind::AlreadyExists,
            "could not allocate a unique private temporary recording directory",
        ))
    }

    pub fn path(&self) -> &Path {
        &self.wav_path
    }
}

impl Drop for TempRecordingGuard {
    fn drop(&mut self) {
        if self.dir.exists() {
            if let Err(err) = std::fs::remove_dir_all(&self.dir) {
                tracing::warn!(
                    dir = %self.dir.display(),
                    error = %err,
                    "failed to remove temporary recording directory"
                );
            }
        }
    }
}

/// Prompt on stderr (Enter starts, Enter stops), capture microphone audio, and write a temp WAV
/// inside an owner-only temporary directory.
pub fn record_interactive_wav() -> Result<TempRecordingGuard, CliError> {
    let mut stderr = io::stderr();
    let stdin = io::stdin();

    writeln!(stderr, "Drücke ENTER um die Aufnahme zu starten...")?;
    stderr.flush()?;
    let mut line = String::new();
    if stdin.read_line(&mut line)? == 0 {
        return Err(CliError::InvalidArguments(
            "Aufnahme abgebrochen (stdin geschlossen)".to_string(),
        ));
    }

    let mut recorder = AudioRecorder::from_env();
    let device = recorder.start()?;
    let started = Instant::now();
    tracing::info!(
        device = %device.name,
        sample_rate = device.default_sample_rate,
        "CLI recording started"
    );

    writeln!(stderr, "Aufnahme läuft... Drücke ENTER zum Beenden.")?;
    stderr.flush()?;
    line.clear();
    let _ = stdin.read_line(&mut line)?;

    // Acquire the private temp directory guard BEFORE writing the WAV so partial files are
    // cleaned up even if `stop_to_wav` or subsequent stderr output fails.
    let guard = TempRecordingGuard::create_private()?;
    let buffer = recorder.stop_to_wav(guard.path())?;
    let elapsed = started.elapsed();

    writeln!(
        stderr,
        "Aufnahme beendet ({:.1}s, {} Hz).",
        buffer.duration_secs(),
        buffer.sample_rate()
    )?;
    tracing::info!(
        duration_s = format_args!("{:.2}", elapsed.as_secs_f64()),
        audio_s = format_args!("{:.2}", buffer.duration_secs()),
        path = %guard.path().display(),
        "CLI recording saved"
    );

    Ok(guard)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn private_temp_recording_guard_enforces_owner_only_dir_and_cleans_up_on_drop() {
        let dir;
        let wav_path;
        {
            let guard = TempRecordingGuard::create_private().unwrap();
            wav_path = guard.path().to_path_buf();
            dir = wav_path.parent().unwrap().to_path_buf();
            assert!(dir.is_dir());

            #[cfg(unix)]
            {
                use std::os::unix::fs::PermissionsExt;
                let mode = std::fs::metadata(&dir).unwrap().permissions().mode() & 0o777;
                assert_eq!(mode, 0o700);
            }

            std::fs::write(&wav_path, b"partial wav").unwrap();
            assert!(wav_path.exists());
        }
        assert!(!wav_path.exists());
        assert!(!dir.exists());
    }
}
