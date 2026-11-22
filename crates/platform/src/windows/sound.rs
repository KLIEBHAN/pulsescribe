//! Native Windows sound player via `winmm.dll` (`PlaySoundW`).
//!
//! Plays the synthesized 12 ms `ready` cue directly from static in-memory WAV bytes
//! (`SND_MEMORY | SND_ASYNC`) with zero temp-file I/O, falling back to system sound aliases.

use std::ffi::OsStr;
use std::os::windows::ffi::OsStrExt;
use std::sync::OnceLock;

use windows::core::PCWSTR;
use windows::Win32::Media::Audio::{PlaySoundW, SND_ALIAS, SND_ASYNC, SND_MEMORY, SND_NODEFAULT};

use crate::{encode_ready_cue_wav, SoundName, SoundPlayer};

/// Static 2-byte-aligned WAV buffer for `SND_MEMORY | SND_ASYNC` so `PlaySoundW` can safely read
/// it asynchronously even after a `WindowsSoundPlayer` instance is dropped.
static READY_CUE_WAV_U16: OnceLock<Vec<u16>> = OnceLock::new();

fn ready_cue_wav_aligned() -> &'static [u16] {
    READY_CUE_WAV_U16.get_or_init(|| {
        let bytes = encode_ready_cue_wav();
        debug_assert!(bytes.len().is_multiple_of(2));
        bytes
            .as_chunks::<2>()
            .0
            .iter()
            .map(|&pair| u16::from_ne_bytes(pair))
            .collect()
    })
}

/// Non-blocking Windows [`SoundPlayer`] backed by `PlaySoundW`.
#[derive(Debug, Default, Clone, Copy)]
pub struct WindowsSoundPlayer;

impl WindowsSoundPlayer {
    pub fn new() -> Self {
        // Pre-warm the 1.1 KB ready-cue WAV buffer once so the first recording cue has zero lag.
        let _ = ready_cue_wav_aligned();
        Self
    }

    fn play_ready_cue() -> bool {
        let wav = ready_cue_wav_aligned();
        #[allow(unsafe_code)]
        // SAFETY: `wav` has `'static` lifetime and 2-byte alignment matching `PCWSTR`, so
        // `PlaySoundW` with `SND_MEMORY | SND_ASYNC` can read it safely after return.
        unsafe {
            PlaySoundW(
                PCWSTR(wav.as_ptr()),
                None,
                SND_MEMORY | SND_ASYNC | SND_NODEFAULT,
            )
        }
        .as_bool()
    }

    fn play_system_alias(alias: &str) {
        let wide: Vec<u16> = OsStr::new(alias)
            .encode_wide()
            .chain(std::iter::once(0))
            .collect();
        #[allow(unsafe_code)]
        // SAFETY: `wide` is NUL-terminated UTF-16; `PlaySoundW` resolves system aliases
        // synchronously before starting asynchronous playback.
        let ok = unsafe {
            PlaySoundW(
                PCWSTR(wide.as_ptr()),
                None,
                SND_ALIAS | SND_ASYNC | SND_NODEFAULT,
            )
        }
        .as_bool();
        if !ok {
            tracing::debug!(alias, "PlaySoundW system alias playback failed");
        }
    }
}

impl SoundPlayer for WindowsSoundPlayer {
    fn play(&self, sound: SoundName) {
        if sound == SoundName::Ready && Self::play_ready_cue() {
            return;
        }
        Self::play_system_alias(sound.windows_system_alias());
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ready_cue_aligned_buffer_preserves_riff_wav_bytes() {
        let wav = ready_cue_wav_aligned();
        assert_eq!(wav.len() * 2, 44 + 529 * 2);
        let header: Vec<u8> = wav[..2].iter().flat_map(|w| w.to_ne_bytes()).collect();
        assert_eq!(&header, b"RIFF");
    }
}
