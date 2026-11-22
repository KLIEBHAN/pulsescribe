//! Interactive CLI microphone recording (`--record`) using `pulsescribe-audio`.

use std::io::{self, Write};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::Instant;

use pulsescribe_audio::AudioRecorder;

use crate::CliError;

static RECORDING_COUNTER: AtomicU64 = AtomicU64::new(0);

/// RAII guard that removes a temporary CLI recording file on drop (best-effort).
pub struct TempRecordingGuard {
    path: PathBuf,
}

impl TempRecordingGuard {
    pub fn new(path: PathBuf) -> Self {
        Self { path }
    }

    pub fn path(&self) -> &Path {
        &self.path
    }
}

impl Drop for TempRecordingGuard {
    fn drop(&mut self) {
        if self.path.exists() {
            if let Err(err) = std::fs::remove_file(&self.path) {
                tracing::warn!(
                    path = %self.path.display(),
                    error = %err,
                    "failed to remove temporary recording file"
                );
            }
        }
    }
}

/// Prompt on stderr (Enter starts, Enter stops), capture microphone audio, and write a temp WAV.
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

    let temp_path = unique_temp_wav_path();
    let buffer = recorder.stop_to_wav(&temp_path)?;
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
        path = %temp_path.display(),
        "CLI recording saved"
    );

    Ok(TempRecordingGuard::new(temp_path))
}

fn unique_temp_wav_path() -> PathBuf {
    let seq = RECORDING_COUNTER.fetch_add(1, Ordering::Relaxed);
    std::env::temp_dir().join(format!(
        "pulsescribe_recording_{}_{}.wav",
        std::process::id(),
        seq
    ))
}
