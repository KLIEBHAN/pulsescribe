//! Audio I/O, multi-format decoding, resampling, and DSP utilities for PulseScribe.
//!
//! All audio buffers in this crate use normalized `f32` samples in `[-1.0, 1.0]`,
//! interleaved by frame when `channels > 1`.

pub mod decode;
pub mod level;
pub mod resample;
pub mod wav;

use thiserror::Error;

/// Canonical ASR sample rate used across PulseScribe (`WHISPER_SAMPLE_RATE`).
pub const TARGET_SAMPLE_RATE: u32 = 16_000;

/// Canonical mono channel count (`WHISPER_CHANNELS`).
pub const TARGET_CHANNELS: u16 = 1;

/// Default capture block size in frames (`WHISPER_BLOCKSIZE`).
pub const DEFAULT_BLOCKSIZE: usize = 1024;

/// Temporary WAV filename written by CLI/recorder flows (`TEMP_RECORDING_FILENAME`).
pub const TEMP_RECORDING_FILENAME: &str = "pulsescribe_recording.wav";

/// Errors returned by offline audio I/O, decoding, and DSP operations.
#[derive(Debug, Error)]
pub enum AudioError {
    #[error("audio I/O error: {0}")]
    Io(#[from] std::io::Error),

    #[error("WAV error: {0}")]
    Wav(#[from] hound::Error),

    #[error("audio decode error: {0}")]
    Decode(String),

    #[error("audio resample error: {0}")]
    Resample(String),

    #[error("invalid audio parameter: {0}")]
    InvalidParameter(String),

    #[error("audio contains no samples")]
    EmptyAudio,
}

/// Interleaved `f32` audio buffer with validated sample rate, channel count, and frame alignment.
#[derive(Debug, Clone, PartialEq)]
pub struct AudioBuffer {
    samples: Vec<f32>,
    sample_rate: u32,
    channels: u16,
}

impl AudioBuffer {
    /// Construct a validated interleaved audio buffer.
    pub fn new(samples: Vec<f32>, sample_rate: u32, channels: u16) -> Result<Self, AudioError> {
        validate_format(samples.len(), sample_rate, channels)?;
        Ok(Self {
            samples,
            sample_rate,
            channels,
        })
    }

    /// Interleaved `f32` samples in `[-1.0, 1.0]`.
    pub fn samples(&self) -> &[f32] {
        &self.samples
    }

    /// Consume the buffer and return the underlying interleaved `f32` samples.
    pub fn into_samples(self) -> Vec<f32> {
        self.samples
    }

    /// Sample rate in Hz (guaranteed `> 0`).
    pub fn sample_rate(&self) -> u32 {
        self.sample_rate
    }

    /// Channel count (guaranteed `> 0`).
    pub fn channels(&self) -> u16 {
        self.channels
    }

    /// Number of multi-channel frames in this buffer.
    pub fn frame_count(&self) -> usize {
        self.samples.len() / usize::from(self.channels)
    }

    /// Duration in seconds.
    pub fn duration_secs(&self) -> f64 {
        self.frame_count() as f64 / f64::from(self.sample_rate)
    }

    /// Returns `true` when the buffer holds no samples.
    pub fn is_empty(&self) -> bool {
        self.samples.is_empty()
    }

    /// Return a mono `f32` copy by averaging interleaved channels per frame.
    pub fn to_mono(&self) -> Vec<f32> {
        let mut mono = Vec::with_capacity(self.frame_count());
        level::downmix_to_mono_into(&self.samples, usize::from(self.channels), &mut mono)
            .expect("AudioBuffer invariants guarantee valid channel count and alignment");
        mono
    }

    /// Consume the buffer into mono `f32` samples without allocating a second vector.
    pub fn into_mono(mut self) -> Vec<f32> {
        level::downmix_in_place(&mut self.samples, usize::from(self.channels));
        self.samples
    }

    /// Convert to 16 kHz mono `f32` samples ready for ASR engines and streaming.
    pub fn into_mono_16k(self) -> Result<Vec<f32>, AudioError> {
        let sample_rate = self.sample_rate;
        let mono = self.into_mono();
        if sample_rate == TARGET_SAMPLE_RATE {
            return Ok(mono);
        }
        resample::resample_mono(&mono, sample_rate, TARGET_SAMPLE_RATE)
    }
}

pub(crate) fn validate_format(
    sample_count: usize,
    sample_rate: u32,
    channels: u16,
) -> Result<(), AudioError> {
    if sample_rate == 0 {
        return Err(AudioError::InvalidParameter(
            "sample_rate must be greater than 0".to_string(),
        ));
    }
    if channels == 0 {
        return Err(AudioError::InvalidParameter(
            "channels must be greater than 0".to_string(),
        ));
    }
    let ch = usize::from(channels);
    if sample_count % ch != 0 {
        return Err(AudioError::InvalidParameter(format!(
            "sample count ({sample_count}) is not a multiple of channel count ({ch})"
        )));
    }
    Ok(())
}

pub use decode::{decode_bytes, decode_file, decode_file_to_mono_16k};
pub use level::{
    compute_level_f32, downmix_to_mono_into, f32_to_i16_into, f32_to_i16_sample,
    f32_to_pcm16_le_into, peak_f32, rms_f32, rms_i16, AudioLevel,
};
pub use resample::{resample_mono, MonoResampler};
pub use wav::{encode_wav_i16_bytes, read_wav, read_wav_reader, write_wav_i16};
