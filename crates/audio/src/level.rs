//! Audio level measurement, channel downmixing, and PCM16 conversion helpers.
//!
//! Designed for zero-allocation reuse in hot audio-capture paths.

use crate::AudioError;

/// RMS and peak amplitude of a normalized audio chunk (`0.0..=1.0` for in-range signals).
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct AudioLevel {
    pub rms: f32,
    pub peak: f32,
}

impl AudioLevel {
    pub const ZERO: Self = Self {
        rms: 0.0,
        peak: 0.0,
    };
}

/// Compute both RMS and peak amplitude in a single pass over normalized `f32` samples.
///
/// Non-finite samples are treated as `0.0` so a single corrupt sample never poisons
/// downstream visualization or VAD thresholds.
pub fn compute_level_f32(samples: &[f32]) -> AudioLevel {
    if samples.is_empty() {
        return AudioLevel::ZERO;
    }

    let mut sum_sq = 0.0_f64;
    let mut peak = 0.0_f32;
    for &sample in samples {
        if !sample.is_finite() {
            continue;
        }
        let abs = sample.abs();
        if abs > peak {
            peak = abs;
        }
        let v = f64::from(sample);
        sum_sq += v * v;
    }

    let rms = (sum_sq / samples.len() as f64).sqrt() as f32;
    AudioLevel { rms, peak }
}

/// Root-mean-square amplitude of normalized `f32` samples.
pub fn rms_f32(samples: &[f32]) -> f32 {
    compute_level_f32(samples).rms
}

/// Peak absolute amplitude of normalized `f32` samples.
pub fn peak_f32(samples: &[f32]) -> f32 {
    compute_level_f32(samples).peak
}

/// Root-mean-square amplitude of signed 16-bit PCM samples normalized by `i16::MAX` (`32767`).
///
/// Matches `providers/deepgram_stream.py` (`sqrt(mean(x^2)) / INT16_MAX`).
pub fn rms_i16(samples: &[i16]) -> f32 {
    if samples.is_empty() {
        return 0.0;
    }

    let sum_sq: f64 = samples
        .iter()
        .map(|&s| {
            let v = f64::from(s);
            v * v
        })
        .sum();
    ((sum_sq / samples.len() as f64).sqrt() / f64::from(i16::MAX)) as f32
}

/// Convert a single normalized `f32` sample (`[-1.0, 1.0]`) to signed 16-bit PCM.
///
/// Non-finite values map to `0`; out-of-range values clamp symmetrically to `[-32767, 32767]`.
#[inline]
pub fn f32_to_i16_sample(sample: f32) -> i16 {
    if !sample.is_finite() {
        return 0;
    }
    (sample.clamp(-1.0, 1.0) * f32::from(i16::MAX)).round() as i16
}

/// Convert `f32` samples into signed 16-bit PCM, clearing and reusing `out`.
pub fn f32_to_i16_into(samples: &[f32], out: &mut Vec<i16>) {
    out.clear();
    out.reserve(samples.len());
    out.extend(samples.iter().copied().map(f32_to_i16_sample));
}

/// Convert `f32` samples into little-endian `linear16` bytes, clearing and reusing `out`.
pub fn f32_to_pcm16_le_into(samples: &[f32], out: &mut Vec<u8>) {
    out.clear();
    out.reserve(samples.len() * 2);
    for &sample in samples {
        out.extend_from_slice(&f32_to_i16_sample(sample).to_le_bytes());
    }
}

/// Downmix interleaved `f32` audio to mono by averaging channels per frame, clearing `out`.
pub fn downmix_to_mono_into(
    interleaved: &[f32],
    channels: usize,
    out: &mut Vec<f32>,
) -> Result<(), AudioError> {
    if channels == 0 {
        return Err(AudioError::InvalidParameter(
            "channels must be greater than 0".to_string(),
        ));
    }
    if interleaved.len() % channels != 0 {
        return Err(AudioError::InvalidParameter(format!(
            "interleaved length ({}) is not a multiple of channels ({channels})",
            interleaved.len()
        )));
    }

    out.clear();
    match channels {
        1 => out.extend_from_slice(interleaved),
        2 => {
            out.reserve(interleaved.len() / 2);
            for frame in interleaved.chunks_exact(2) {
                out.push((frame[0] + frame[1]) * 0.5);
            }
        }
        _ => {
            let inv = 1.0 / channels as f32;
            out.reserve(interleaved.len() / channels);
            for frame in interleaved.chunks_exact(channels) {
                let sum: f32 = frame.iter().copied().sum();
                out.push(sum * inv);
            }
        }
    }
    Ok(())
}

/// Collapse interleaved multi-channel audio to mono in-place without allocating.
pub(crate) fn downmix_in_place(samples: &mut Vec<f32>, channels: usize) {
    if channels <= 1 {
        return;
    }
    let frames = samples.len() / channels;
    if channels == 2 {
        for frame in 0..frames {
            let base = frame * 2;
            samples[frame] = (samples[base] + samples[base + 1]) * 0.5;
        }
    } else {
        let inv = 1.0 / channels as f32;
        for frame in 0..frames {
            let base = frame * channels;
            let sum: f32 = samples[base..base + channels].iter().copied().sum();
            samples[frame] = sum * inv;
        }
    }
    samples.truncate(frames);
}

#[cfg(test)]
mod tests {
    use std::f32::consts::TAU;

    use super::*;

    #[test]
    fn empty_and_silence_return_zero_level() {
        assert_eq!(compute_level_f32(&[]), AudioLevel::ZERO);
        assert_eq!(compute_level_f32(&[0.0; 128]), AudioLevel::ZERO);
        assert_eq!(rms_i16(&[]), 0.0);
    }

    #[test]
    fn full_scale_sine_has_expected_rms_and_peak() {
        let samples: Vec<f32> = (0..1600)
            .map(|i| (TAU * 400.0 * i as f32 / 16_000.0).sin())
            .collect();
        let level = compute_level_f32(&samples);
        let expected_rms = std::f32::consts::FRAC_1_SQRT_2;
        assert!((level.rms - expected_rms).abs() < 1e-3);
        assert!((level.peak - 1.0).abs() < 1e-3);

        let mut pcm = Vec::new();
        f32_to_i16_into(&samples, &mut pcm);
        assert!((rms_i16(&pcm) - expected_rms).abs() < 1e-3);
    }

    #[test]
    fn non_finite_and_out_of_range_samples_are_handled_safely() {
        let level = compute_level_f32(&[f32::NAN, f32::INFINITY, 0.5, -0.5]);
        assert!((level.peak - 0.5).abs() < 1e-6);
        assert!((level.rms - (0.5_f32 / 4.0).sqrt()).abs() < 1e-6);

        assert_eq!(f32_to_i16_sample(f32::NAN), 0);
        assert_eq!(f32_to_i16_sample(f32::NEG_INFINITY), 0);
        assert_eq!(f32_to_i16_sample(2.0), i16::MAX);
        assert_eq!(f32_to_i16_sample(-2.0), -i16::MAX);
    }

    #[test]
    fn pcm16_le_encoding_clears_and_reuses_buffer() {
        let mut bytes = vec![0xFF; 10];
        f32_to_pcm16_le_into(&[0.0, 1.0, -1.0], &mut bytes);
        assert_eq!(bytes.len(), 6);
        assert_eq!(i16::from_le_bytes([bytes[0], bytes[1]]), 0);
        assert_eq!(i16::from_le_bytes([bytes[2], bytes[3]]), i16::MAX);
        assert_eq!(i16::from_le_bytes([bytes[4], bytes[5]]), -i16::MAX);
    }

    #[test]
    fn downmix_handles_stereo_and_multichannel() {
        let stereo = vec![1.0, -1.0, 0.25, 0.75];
        let mut mono = vec![99.0];
        downmix_to_mono_into(&stereo, 2, &mut mono).unwrap();
        assert_eq!(mono, vec![0.0, 0.5]);

        let mut inplace = vec![0.3, 0.6, 0.9, -0.3, 0.0, 0.3];
        downmix_in_place(&mut inplace, 3);
        assert_eq!(inplace.len(), 2);
        assert!((inplace[0] - 0.6).abs() < 1e-6);
        assert!(inplace[1].abs() < 1e-6);

        assert!(matches!(
            downmix_to_mono_into(&[0.0; 3], 2, &mut mono),
            Err(AudioError::InvalidParameter(_))
        ));
    }
}
