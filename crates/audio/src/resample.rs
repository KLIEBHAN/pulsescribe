//! Mono audio resampling via `rubato`.
//!
//! Standard rational sample-rate pairs (such as `48_000 -> 16_000` and `44_100 -> 16_000`)
//! use the fast synchronous FFT resampler; arbitrary ratios with large reduced denominators
//! automatically fall back to cubic sinc interpolation so memory and latency stay bounded.

use rubato::audioadapter_buffers::direct::InterleavedSlice;
use rubato::{
    Async, Fft, FixedAsync, FixedSync, Indexing, Resampler, SincInterpolationParameters,
    SincInterpolationType, WindowFunction,
};

use crate::AudioError;

const DEFAULT_CHUNK_SIZE: usize = 1024;
const MAX_FFT_RATIO_DENOM: usize = 1024;

fn gcd(mut a: usize, mut b: usize) -> usize {
    while b != 0 {
        let r = a % b;
        a = b;
        b = r;
    }
    a
}

fn build_mono_resampler(
    from_rate: u32,
    to_rate: u32,
    chunk_size: usize,
) -> Result<Box<dyn Resampler<f32>>, AudioError> {
    if from_rate == 0 || to_rate == 0 {
        return Err(AudioError::InvalidParameter(format!(
            "sample rates must be > 0 (got {from_rate} -> {to_rate})"
        )));
    }
    let from = from_rate as usize;
    let to = to_rate as usize;
    let max_min_block = from.max(to) / gcd(from, to);

    if max_min_block <= MAX_FFT_RATIO_DENOM {
        let fft = Fft::<f32>::new(from, to, chunk_size.max(64), 1, FixedSync::Input)
            .map_err(|err| AudioError::Resample(err.to_string()))?;
        Ok(Box::new(fft))
    } else {
        let params = SincInterpolationParameters {
            sinc_len: 128,
            f_cutoff: Some(0.95),
            interpolation: SincInterpolationType::Cubic,
            oversampling_factor: 128,
            window: WindowFunction::BlackmanHarris2,
        };
        let sinc = Async::<f32>::new_sinc(
            to as f64 / from as f64,
            1.1,
            &params,
            chunk_size.max(64),
            1,
            FixedAsync::Input,
        )
        .map_err(|err| AudioError::Resample(err.to_string()))?;
        Ok(Box::new(sinc))
    }
}

/// Resample a complete mono `f32` buffer from `from_rate` Hz to `to_rate` Hz.
///
/// Startup delay is trimmed automatically and output length matches the exact resampled duration.
pub fn resample_mono(
    samples: &[f32],
    from_rate: u32,
    to_rate: u32,
) -> Result<Vec<f32>, AudioError> {
    if from_rate == 0 || to_rate == 0 {
        return Err(AudioError::InvalidParameter(format!(
            "sample rates must be > 0 (got {from_rate} -> {to_rate})"
        )));
    }
    if samples.is_empty() {
        return Ok(Vec::new());
    }
    if from_rate == to_rate {
        return Ok(samples.to_vec());
    }

    let expected_len =
        ((samples.len() as f64) * (f64::from(to_rate) / f64::from(from_rate))).ceil() as usize;
    let mut out = Vec::with_capacity(expected_len);
    let mut resampler = MonoResampler::new(from_rate, to_rate)?;
    resampler.push(samples, &mut out)?;
    resampler.flush(&mut out)?;
    Ok(out)
}

enum ResamplerBackend {
    PassThrough,
    Active(Box<dyn Resampler<f32>>),
}

/// Stateful streaming mono resampler for chunked live capture (`push` + `flush`).
///
/// Reuses internal input/output buffers across chunks and trims the resampler startup delay
/// automatically so callers receive aligned PCM samples.
pub struct MonoResampler {
    from_rate: u32,
    to_rate: u32,
    backend: ResamplerBackend,
    pending: Vec<f32>,
    pending_offset: usize,
    scratch_out: Vec<f32>,
    delay_remaining: usize,
    total_input_frames: u64,
    total_output_frames: u64,
}

impl MonoResampler {
    /// Create a streaming mono resampler from `from_rate` Hz to `to_rate` Hz.
    pub fn new(from_rate: u32, to_rate: u32) -> Result<Self, AudioError> {
        if from_rate == 0 || to_rate == 0 {
            return Err(AudioError::InvalidParameter(format!(
                "sample rates must be > 0 (got {from_rate} -> {to_rate})"
            )));
        }
        if from_rate == to_rate {
            return Ok(Self {
                from_rate,
                to_rate,
                backend: ResamplerBackend::PassThrough,
                pending: Vec::new(),
                pending_offset: 0,
                scratch_out: Vec::new(),
                delay_remaining: 0,
                total_input_frames: 0,
                total_output_frames: 0,
            });
        }

        let resampler = build_mono_resampler(from_rate, to_rate, DEFAULT_CHUNK_SIZE)?;
        let delay = resampler.output_delay();
        let out_cap = resampler.output_frames_max();
        let in_cap = resampler.input_frames_max();
        Ok(Self {
            from_rate,
            to_rate,
            backend: ResamplerBackend::Active(resampler),
            pending: Vec::with_capacity(in_cap * 2),
            pending_offset: 0,
            scratch_out: vec![0.0; out_cap],
            delay_remaining: delay,
            total_input_frames: 0,
            total_output_frames: 0,
        })
    }

    pub fn from_rate(&self) -> u32 {
        self.from_rate
    }

    pub fn to_rate(&self) -> u32 {
        self.to_rate
    }

    /// Feed an arbitrary-length chunk of mono input samples and append resampled output to `out`.
    pub fn push(&mut self, input: &[f32], out: &mut Vec<f32>) -> Result<(), AudioError> {
        if input.is_empty() {
            return Ok(());
        }
        let ResamplerBackend::Active(resampler) = &mut self.backend else {
            out.extend_from_slice(input);
            return Ok(());
        };

        self.pending.extend_from_slice(input);
        self.total_input_frames += input.len() as u64;

        loop {
            let needed = resampler.input_frames_next();
            let available = self.pending.len() - self.pending_offset;
            if available < needed {
                break;
            }

            let in_slice = &self.pending[self.pending_offset..self.pending_offset + needed];
            let (consumed, produced) =
                process_mono_chunk(resampler.as_mut(), in_slice, &mut self.scratch_out, None)?;
            self.pending_offset += consumed;

            emit_trimmed(
                &self.scratch_out[..produced],
                &mut self.delay_remaining,
                &mut self.total_output_frames,
                None,
                out,
            );
        }

        self.compact_pending();
        Ok(())
    }

    /// Flush all buffered tail samples and internal filter delay into `out`, then reset the resampler.
    pub fn flush(&mut self, out: &mut Vec<f32>) -> Result<(), AudioError> {
        let ResamplerBackend::Active(resampler) = &mut self.backend else {
            return Ok(());
        };
        if self.total_input_frames == 0 {
            self.reset();
            return Ok(());
        }

        let expected_total =
            ((self.total_input_frames as f64) * resampler.resample_ratio()).ceil() as u64;

        let remaining = self.pending.len() - self.pending_offset;
        if remaining > 0 {
            let needed = resampler.input_frames_next();
            let tail_start = self.pending_offset;
            if remaining < needed {
                self.pending.resize(tail_start + needed, 0.0);
            }
            let (_, produced) = process_mono_chunk(
                resampler.as_mut(),
                &self.pending[tail_start..tail_start + needed],
                &mut self.scratch_out,
                Some(remaining),
            )?;

            emit_trimmed(
                &self.scratch_out[..produced],
                &mut self.delay_remaining,
                &mut self.total_output_frames,
                Some(expected_total),
                out,
            );
            self.pending.clear();
            self.pending_offset = 0;
        }

        let silence = vec![0.0_f32; resampler.input_frames_max()];
        while self.total_output_frames < expected_total {
            let needed = resampler.input_frames_next();
            let (_, produced) = process_mono_chunk(
                resampler.as_mut(),
                &silence[..needed],
                &mut self.scratch_out,
                Some(0),
            )?;
            if produced == 0 {
                break;
            }

            emit_trimmed(
                &self.scratch_out[..produced],
                &mut self.delay_remaining,
                &mut self.total_output_frames,
                Some(expected_total),
                out,
            );
        }

        self.reset();
        Ok(())
    }

    /// Clear buffered state so the resampler can be reused for a fresh stream.
    pub fn reset(&mut self) {
        self.pending.clear();
        self.pending_offset = 0;
        self.total_input_frames = 0;
        self.total_output_frames = 0;
        if let ResamplerBackend::Active(resampler) = &mut self.backend {
            resampler.reset();
            self.delay_remaining = resampler.output_delay();
        }
    }

    fn compact_pending(&mut self) {
        if self.pending_offset == self.pending.len() {
            self.pending.clear();
            self.pending_offset = 0;
        } else if self.pending_offset >= self.pending.len() / 2 {
            self.pending.drain(..self.pending_offset);
            self.pending_offset = 0;
        }
    }
}

fn process_mono_chunk(
    resampler: &mut dyn Resampler<f32>,
    in_slice: &[f32],
    scratch_out: &mut Vec<f32>,
    partial_len: Option<usize>,
) -> Result<(usize, usize), AudioError> {
    let out_needed = resampler.output_frames_next();
    if scratch_out.len() < out_needed {
        scratch_out.resize(out_needed, 0.0);
    }

    let in_buf = InterleavedSlice::new(in_slice, 1, in_slice.len())
        .map_err(|err| AudioError::Resample(err.to_string()))?;
    let mut out_buf = InterleavedSlice::new_mut(&mut scratch_out[..out_needed], 1, out_needed)
        .map_err(|err| AudioError::Resample(err.to_string()))?;

    let indexing = partial_len.map(|len| Indexing {
        partial_len: Some(len),
        ..Indexing::default()
    });
    resampler
        .process_into_buffer(&in_buf, &mut out_buf, indexing.as_ref())
        .map_err(|err| AudioError::Resample(err.to_string()))
}

fn emit_trimmed(
    produced: &[f32],
    delay_remaining: &mut usize,
    total_output_frames: &mut u64,
    cap_total: Option<u64>,
    out: &mut Vec<f32>,
) {
    let skip = (*delay_remaining).min(produced.len());
    *delay_remaining -= skip;
    let valid = &produced[skip..];
    if valid.is_empty() {
        return;
    }

    let take = match cap_total {
        Some(limit) => {
            let remaining = limit.saturating_sub(*total_output_frames) as usize;
            valid.len().min(remaining)
        }
        None => valid.len(),
    };
    out.extend_from_slice(&valid[..take]);
    *total_output_frames += take as u64;
}

#[cfg(test)]
mod tests {
    use std::f32::consts::TAU;

    use super::*;
    use crate::TARGET_SAMPLE_RATE;

    fn sine_wave(freq_hz: f32, sample_rate: u32, num_samples: usize) -> Vec<f32> {
        (0..num_samples)
            .map(|i| 0.8 * (TAU * freq_hz * i as f32 / sample_rate as f32).sin())
            .collect()
    }

    #[test]
    fn identity_and_empty_fast_paths() {
        let input = sine_wave(440.0, TARGET_SAMPLE_RATE, 512);
        assert_eq!(
            resample_mono(&input, TARGET_SAMPLE_RATE, TARGET_SAMPLE_RATE).unwrap(),
            input
        );
        assert!(resample_mono(&[], 48_000, TARGET_SAMPLE_RATE)
            .unwrap()
            .is_empty());
        assert!(matches!(
            resample_mono(&input, 0, TARGET_SAMPLE_RATE),
            Err(AudioError::InvalidParameter(_))
        ));
    }

    #[test]
    fn downsamples_48k_and_44100_to_16k_preserving_sine_tone() {
        for from_rate in [48_000_u32, 44_100_u32] {
            let input = sine_wave(440.0, from_rate, from_rate as usize); // 1.0 s
            let out = resample_mono(&input, from_rate, TARGET_SAMPLE_RATE).unwrap();
            assert_eq!(out.len(), TARGET_SAMPLE_RATE as usize);

            // Check RMS and single-tone spectral energy in the steady-state middle segment.
            let seg = &out[1000..15_000];
            let level = crate::compute_level_f32(seg);
            let expected_rms = 0.8 * std::f32::consts::FRAC_1_SQRT_2;
            assert!(
                (level.rms - expected_rms).abs() < 2e-3,
                "RMS drift after {from_rate} -> 16000 resample: {} vs {}",
                level.rms,
                expected_rms
            );

            // Project onto 440 Hz sin/cos basis (phase-invariant amplitude check; odd FFT sizes
            // such as 44100->16000 have a ~0.18-sample fractional group delay).
            let mut sum_sin = 0.0_f64;
            let mut sum_cos = 0.0_f64;
            for (idx, &sample) in seg.iter().enumerate() {
                let phase =
                    f64::from(TAU) * 440.0 * (idx + 1000) as f64 / f64::from(TARGET_SAMPLE_RATE);
                sum_sin += f64::from(sample) * phase.sin();
                sum_cos += f64::from(sample) * phase.cos();
            }
            let tone_amp = 2.0 * sum_sin.hypot(sum_cos) / seg.len() as f64;
            assert!(
                (tone_amp - 0.8).abs() < 2e-3,
                "440 Hz tone amplitude distorted after {from_rate} -> 16000: {tone_amp}"
            );

            // Ensure no large delay misalignment or glitch (pointwise error bounded by sub-sample phase).
            let expected = sine_wave(440.0, TARGET_SAMPLE_RATE, TARGET_SAMPLE_RATE as usize);
            let max_err = seg
                .iter()
                .zip(&expected[1000..15_000])
                .map(|(a, b)| (a - b).abs())
                .fold(0.0_f32, f32::max);
            assert!(
                max_err < 0.04,
                "440 Hz phase/glitch error after {from_rate} -> 16000 resample: max_err={max_err}"
            );
        }
    }

    #[test]
    fn short_clip_shorter_than_one_chunk_resamples_cleanly() {
        let input = sine_wave(440.0, 48_000, 300); // 6.25 ms -> 100 samples at 16 kHz
        let out = resample_mono(&input, 48_000, TARGET_SAMPLE_RATE).unwrap();
        assert_eq!(out.len(), 100);
        assert!(crate::rms_f32(&out) > 0.2);
    }

    #[test]
    fn streaming_resampler_matches_batch_resample() {
        let input = sine_wave(330.0, 48_000, 48_000 / 2 + 173); // non-aligned length
        let batch = resample_mono(&input, 48_000, TARGET_SAMPLE_RATE).unwrap();

        let mut streaming = MonoResampler::new(48_000, TARGET_SAMPLE_RATE).unwrap();
        let mut streamed = Vec::new();
        for chunk in input.chunks(137) {
            streaming.push(chunk, &mut streamed).unwrap();
        }
        streaming.flush(&mut streamed).unwrap();

        assert_eq!(streamed.len(), batch.len());
        let max_diff = streamed
            .iter()
            .zip(&batch)
            .map(|(a, b)| (a - b).abs())
            .fold(0.0_f32, f32::max);
        assert!(
            max_diff < 1e-6,
            "streaming vs batch diff too high: {max_diff}"
        );

        // Verify the resampler resets cleanly after `flush()` and reproduces the exact output.
        let mut second_pass = Vec::new();
        for chunk in input.chunks(511) {
            streaming.push(chunk, &mut second_pass).unwrap();
        }
        streaming.flush(&mut second_pass).unwrap();
        assert_eq!(second_pass, streamed);
    }

    #[test]
    fn upsamples_8k_to_16k_preserving_duration_and_level() {
        let input = sine_wave(440.0, 8_000, 4_000); // 0.5 s
        let out = resample_mono(&input, 8_000, TARGET_SAMPLE_RATE).unwrap();
        assert_eq!(out.len(), 8_000);
        let level = crate::compute_level_f32(&out[500..7_500]);
        let expected_rms = 0.8 * std::f32::consts::FRAC_1_SQRT_2;
        assert!((level.rms - expected_rms).abs() < 2e-3);
    }

    #[test]
    fn odd_prime_sample_rate_falls_back_to_sinc_resampler() {
        let from_rate = 44_101_u32;
        let input = sine_wave(440.0, from_rate, from_rate as usize / 4);
        let out = resample_mono(&input, from_rate, TARGET_SAMPLE_RATE).unwrap();
        let expected_len =
            ((input.len() as f64) * (TARGET_SAMPLE_RATE as f64 / from_rate as f64)).ceil() as usize;
        assert_eq!(out.len(), expected_len);
    }
}
