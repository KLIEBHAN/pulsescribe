//! Sound playback trait, OS sound registries, and synthetic Windows ready-cue WAV generator.
//!
//! Mirrors `whisper_platform/sound.py`.

use std::f64::consts::PI;

/// Sample rate for the synthesized Windows ready-cue tick (44.1 kHz).
pub const READY_CUE_SAMPLE_RATE: u32 = 44_100;
/// Fundamental frequency of the synthesized ready-cue tick (Hz).
pub const READY_CUE_FREQ_HZ: f64 = 2_600.0;
/// Total duration of the synthesized ready-cue tick (12 ms).
pub const READY_CUE_DURATION_SEC: f64 = 0.012;
/// Linear attack ramp duration to avoid DC click (0.5 ms).
pub const READY_CUE_ATTACK_SEC: f64 = 0.0005;
/// Exponential decay time constant (3 ms).
pub const READY_CUE_DECAY_TAU_SEC: f64 = 0.003;
/// Peak amplitude as a fraction of full-scale 16-bit signed PCM.
pub const READY_CUE_AMPLITUDE: f64 = 0.5;
/// Temp filename used by the Windows `PlaySound` adapter for the ready-cue WAV.
pub const READY_CUE_FILENAME: &str = "pulsescribe_ready_cue.wav";

/// Named acoustic feedback cues used throughout the dictation lifecycle.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum SoundName {
    Ready,
    Stop,
    Done,
    Error,
    Warmup,
}

impl SoundName {
    /// Canonical lowercase cue identifier.
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Ready => "ready",
            Self::Stop => "stop",
            Self::Done => "done",
            Self::Error => "error",
            Self::Warmup => "warmup",
        }
    }

    /// Parses a sound identifier case-insensitively.
    pub fn from_name(raw: &str) -> Option<Self> {
        match raw.trim().to_ascii_lowercase().as_str() {
            "ready" => Some(Self::Ready),
            "stop" => Some(Self::Stop),
            "done" => Some(Self::Done),
            "error" => Some(Self::Error),
            "warmup" => Some(Self::Warmup),
            _ => None,
        }
    }

    /// Built-in macOS system sound path matching `MACOS_SYSTEM_SOUNDS`.
    pub const fn macos_system_path(self) -> &'static str {
        match self {
            Self::Ready => "/System/Library/Sounds/Tink.aiff",
            Self::Stop => "/System/Library/Sounds/Pop.aiff",
            Self::Done => "/System/Library/Sounds/Bottle.aiff",
            Self::Error => "/System/Library/Sounds/Basso.aiff",
            Self::Warmup => "/System/Library/Sounds/Glass.aiff",
        }
    }

    /// Built-in Windows system sound alias matching `WINDOWS_SYSTEM_SOUNDS`.
    pub const fn windows_system_alias(self) -> &'static str {
        match self {
            Self::Ready => "DeviceConnect",
            Self::Stop => "DeviceDisconnect",
            Self::Done => "Notification.SMS",
            Self::Error => "SystemHand",
            Self::Warmup => "SystemAsterisk",
        }
    }
}

/// Non-blocking sound playback boundary.
pub trait SoundPlayer: Send + Sync {
    /// Plays a named acoustic cue without blocking the dictation pipeline.
    fn play(&self, sound: SoundName);
}

/// No-op sound player for muted or headless environments.
#[derive(Debug, Default, Clone, Copy)]
pub struct NoopSoundPlayer;

impl SoundPlayer for NoopSoundPlayer {
    fn play(&self, _sound: SoundName) {}
}

/// Synthesizes 16-bit mono PCM samples for the instant-onset Windows `ready` cue.
///
/// Mirrors `whisper_platform/sound.py::_ready_cue_samples`: a 0.5 ms linear attack
/// ramp followed by an exponential decay on a 2.6 kHz sine wave.
pub fn synthesize_ready_cue_samples() -> Vec<i16> {
    let sample_rate = READY_CUE_SAMPLE_RATE as f64;
    let total = (sample_rate * READY_CUE_DURATION_SEC) as usize;
    let attack = ((sample_rate * READY_CUE_ATTACK_SEC) as usize).max(1);
    let two_pi_f = 2.0 * PI * READY_CUE_FREQ_HZ;
    let peak = READY_CUE_AMPLITUDE * f64::from(i16::MAX);

    let mut samples = Vec::with_capacity(total);
    for n in 0..total {
        let t = n as f64 / sample_rate;
        let envelope = if n < attack {
            n as f64 / attack as f64
        } else {
            (-(t - READY_CUE_ATTACK_SEC) / READY_CUE_DECAY_TAU_SEC).exp()
        };
        let value = peak * envelope * (two_pi_f * t).sin();
        let clamped = (value as i32).clamp(i32::from(i16::MIN), i32::from(i16::MAX)) as i16;
        samples.push(clamped);
    }
    samples
}

/// Encodes the synthesized Windows `ready` cue as a 44.1 kHz 16-bit mono PCM WAV buffer.
pub fn encode_ready_cue_wav() -> Vec<u8> {
    let samples = synthesize_ready_cue_samples();
    let data_bytes = (samples.len() * std::mem::size_of::<i16>()) as u32;
    let riff_size = 36u32 + data_bytes;
    let byte_rate = READY_CUE_SAMPLE_RATE * 2;

    let mut buf = Vec::with_capacity(44 + data_bytes as usize);
    buf.extend_from_slice(b"RIFF");
    buf.extend_from_slice(&riff_size.to_le_bytes());
    buf.extend_from_slice(b"WAVE");
    buf.extend_from_slice(b"fmt ");
    buf.extend_from_slice(&16u32.to_le_bytes()); // PCM fmt chunk size
    buf.extend_from_slice(&1u16.to_le_bytes()); // PCM format
    buf.extend_from_slice(&1u16.to_le_bytes()); // mono
    buf.extend_from_slice(&READY_CUE_SAMPLE_RATE.to_le_bytes());
    buf.extend_from_slice(&byte_rate.to_le_bytes());
    buf.extend_from_slice(&2u16.to_le_bytes()); // block align
    buf.extend_from_slice(&16u16.to_le_bytes()); // bits per sample
    buf.extend_from_slice(b"data");
    buf.extend_from_slice(&data_bytes.to_le_bytes());
    for sample in samples {
        buf.extend_from_slice(&sample.to_le_bytes());
    }
    buf
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn sound_name_roundtrip_and_os_tables() {
        for (name, expected, mac_suffix, win_alias) in [
            ("ready", SoundName::Ready, "Tink.aiff", "DeviceConnect"),
            ("STOP", SoundName::Stop, "Pop.aiff", "DeviceDisconnect"),
            (" done ", SoundName::Done, "Bottle.aiff", "Notification.SMS"),
            ("Error", SoundName::Error, "Basso.aiff", "SystemHand"),
            ("warmup", SoundName::Warmup, "Glass.aiff", "SystemAsterisk"),
        ] {
            let parsed = SoundName::from_name(name).unwrap();
            assert_eq!(parsed, expected);
            assert_eq!(parsed.as_str(), name.trim().to_ascii_lowercase());
            assert!(parsed.macos_system_path().ends_with(mac_suffix));
            assert_eq!(parsed.windows_system_alias(), win_alias);
        }
        assert_eq!(SoundName::from_name("unknown"), None);
    }

    #[test]
    fn ready_cue_samples_match_python_synthesis_contract() {
        let samples = synthesize_ready_cue_samples();
        // Exact parity with `whisper_platform/sound.py::_ready_cue_samples()`.
        assert_eq!(samples.len(), 529);
        assert_eq!(
            &samples[..10],
            &[0, 269, 1005, 2002, 2967, 3577, 3552, 2718, 1055, -1281]
        );
        assert_eq!(&samples[20..26], &[13442, 15595, 15678, 12789, 8220, 2613]);
        assert_eq!(&samples[samples.len() - 5..], &[-228, -107, 25, 154, 259]);
        let sum: i64 = samples.iter().map(|&s| i64::from(s)).sum();
        assert_eq!(sum, 5599);
    }

    #[test]
    fn ready_cue_wav_header_is_valid_pcm16_mono_44100() {
        let wav = encode_ready_cue_wav();
        assert_eq!(wav.len(), 44 + 529 * 2);
        assert_eq!(&wav[0..4], b"RIFF");
        assert_eq!(
            u32::from_le_bytes(wav[4..8].try_into().unwrap()),
            36 + 529 * 2
        );
        assert_eq!(&wav[8..12], b"WAVE");
        assert_eq!(&wav[12..16], b"fmt ");
        assert_eq!(u16::from_le_bytes(wav[20..22].try_into().unwrap()), 1); // PCM
        assert_eq!(u16::from_le_bytes(wav[22..24].try_into().unwrap()), 1); // mono
        assert_eq!(
            u32::from_le_bytes(wav[24..28].try_into().unwrap()),
            READY_CUE_SAMPLE_RATE
        );
        assert_eq!(u16::from_le_bytes(wav[34..36].try_into().unwrap()), 16); // 16-bit
        assert_eq!(&wav[36..40], b"data");
        assert_eq!(u32::from_le_bytes(wav[40..44].try_into().unwrap()), 529 * 2);
    }
}
