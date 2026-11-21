//! WAV reading and 16-bit PCM WAV encoding via `hound`.

use std::fs::File;
use std::io::{BufWriter, Cursor, Read, Seek, Write};
use std::path::Path;

use hound::{SampleFormat, WavReader, WavSpec, WavWriter};

use crate::level::f32_to_i16_sample;
use crate::{validate_format, AudioBuffer, AudioError};

fn pcm16_spec(sample_rate: u32, channels: u16) -> WavSpec {
    WavSpec {
        channels,
        sample_rate,
        bits_per_sample: 16,
        sample_format: SampleFormat::Int,
    }
}

fn write_wav_i16_writer<W: Write + Seek>(
    writer: W,
    samples: &[f32],
    sample_rate: u32,
    channels: u16,
) -> Result<(), AudioError> {
    validate_format(samples.len(), sample_rate, channels)?;
    if samples.is_empty() {
        return Err(AudioError::EmptyAudio);
    }

    let mut wav = WavWriter::new(writer, pcm16_spec(sample_rate, channels))?;
    for &sample in samples {
        wav.write_sample(f32_to_i16_sample(sample))?;
    }
    wav.finalize()?;
    Ok(())
}

/// Write normalized `f32` samples as a 16-bit signed PCM WAV file.
///
/// Matches Python `soundfile.write(..., subtype="PCM_16")`.
pub fn write_wav_i16(
    path: &Path,
    samples: &[f32],
    sample_rate: u32,
    channels: u16,
) -> Result<(), AudioError> {
    validate_format(samples.len(), sample_rate, channels)?;
    if samples.is_empty() {
        return Err(AudioError::EmptyAudio);
    }

    let file = BufWriter::new(File::create(path)?);
    write_wav_i16_writer(file, samples, sample_rate, channels)
}

/// Encode normalized `f32` samples as an in-memory 16-bit signed PCM WAV byte vector.
pub fn encode_wav_i16_bytes(
    samples: &[f32],
    sample_rate: u32,
    channels: u16,
) -> Result<Vec<u8>, AudioError> {
    let estimated_bytes = 44 + samples.len().saturating_mul(2);
    let mut cursor = Cursor::new(Vec::with_capacity(estimated_bytes));
    write_wav_i16_writer(&mut cursor, samples, sample_rate, channels)?;
    Ok(cursor.into_inner())
}

/// Read a WAV file from disk into normalized `f32` samples.
pub fn read_wav(path: &Path) -> Result<AudioBuffer, AudioError> {
    let reader = WavReader::open(path)?;
    read_samples_from_wav(reader)
}

/// Read a WAV stream from any `Read` source into normalized `f32` samples.
pub fn read_wav_reader<R: Read>(reader: R) -> Result<AudioBuffer, AudioError> {
    let wav = WavReader::new(reader)?;
    read_samples_from_wav(wav)
}

/// Upper bound for upfront `Vec<f32>` preallocation derived from an untrusted WAV header
/// (1 Mi samples = 4 MiB). Larger valid WAV files still decode normally as the vector grows.
const MAX_INITIAL_WAV_CAPACITY: usize = 1_048_576;

fn read_samples_from_wav<R: Read>(mut reader: WavReader<R>) -> Result<AudioBuffer, AudioError> {
    let spec = reader.spec();
    if spec.sample_rate == 0 || spec.channels == 0 {
        return Err(AudioError::InvalidParameter(format!(
            "invalid WAV header: sample_rate={}, channels={}",
            spec.sample_rate, spec.channels
        )));
    }

    let initial_capacity = usize::try_from(reader.len())
        .unwrap_or(MAX_INITIAL_WAV_CAPACITY)
        .min(MAX_INITIAL_WAV_CAPACITY);
    let mut samples = Vec::with_capacity(initial_capacity);

    match spec.sample_format {
        SampleFormat::Float => {
            for sample in reader.samples::<f32>() {
                samples.push(sample?);
            }
        }
        SampleFormat::Int => {
            if spec.bits_per_sample == 0 || spec.bits_per_sample > 32 {
                return Err(AudioError::InvalidParameter(format!(
                    "unsupported integer WAV bit depth: {}",
                    spec.bits_per_sample
                )));
            }
            let scale = (1_u64 << (spec.bits_per_sample - 1)) as f64;
            for sample in reader.samples::<i32>() {
                let normalized = (f64::from(sample?) / scale).clamp(-1.0, 1.0) as f32;
                samples.push(normalized);
            }
        }
    }

    if samples.is_empty() {
        return Err(AudioError::EmptyAudio);
    }

    AudioBuffer::new(samples, spec.sample_rate, spec.channels)
}

#[cfg(test)]
mod tests {
    use std::f32::consts::TAU;

    use super::*;
    use crate::TARGET_SAMPLE_RATE;

    #[test]
    fn round_trip_i16_wav_in_memory_and_on_disk() {
        let input: Vec<f32> = (0..1600)
            .map(|i| 0.75 * (TAU * 440.0 * i as f32 / TARGET_SAMPLE_RATE as f32).sin())
            .collect();

        let bytes = encode_wav_i16_bytes(&input, TARGET_SAMPLE_RATE, 1).unwrap();
        let decoded = read_wav_reader(Cursor::new(bytes)).unwrap();
        assert_eq!(decoded.sample_rate(), TARGET_SAMPLE_RATE);
        assert_eq!(decoded.channels(), 1);
        assert_eq!(decoded.samples().len(), input.len());

        let max_err = input
            .iter()
            .zip(decoded.samples())
            .map(|(a, b)| (a - b).abs())
            .fold(0.0_f32, f32::max);
        assert!(max_err < 1e-4, "max quantization error too high: {max_err}");

        let path = std::env::temp_dir().join(format!(
            "pulsescribe_audio_wav_test_{}.wav",
            std::process::id()
        ));
        write_wav_i16(&path, &input, TARGET_SAMPLE_RATE, 1).unwrap();
        let from_disk = read_wav(&path).unwrap();
        let _ = std::fs::remove_file(&path);
        assert_eq!(from_disk, decoded);
    }

    #[test]
    fn reads_float32_and_24bit_int_wav() {
        let mut float_buf = Cursor::new(Vec::new());
        {
            let mut writer = WavWriter::new(
                &mut float_buf,
                WavSpec {
                    channels: 2,
                    sample_rate: 48_000,
                    bits_per_sample: 32,
                    sample_format: SampleFormat::Float,
                },
            )
            .unwrap();
            for s in [0.25_f32, -0.25, 0.5, -0.5] {
                writer.write_sample(s).unwrap();
            }
            writer.finalize().unwrap();
        }
        let decoded = read_wav_reader(Cursor::new(float_buf.into_inner())).unwrap();
        assert_eq!(decoded.sample_rate(), 48_000);
        assert_eq!(decoded.channels(), 2);
        assert_eq!(decoded.samples(), &[0.25, -0.25, 0.5, -0.5]);

        let mut int24_buf = Cursor::new(Vec::new());
        {
            let mut writer = WavWriter::new(
                &mut int24_buf,
                WavSpec {
                    channels: 1,
                    sample_rate: 44_100,
                    bits_per_sample: 24,
                    sample_format: SampleFormat::Int,
                },
            )
            .unwrap();
            writer.write_sample(4_194_304_i32).unwrap(); // +0.5 in signed 24-bit
            writer.write_sample(-4_194_304_i32).unwrap(); // -0.5 in signed 24-bit
            writer.finalize().unwrap();
        }
        let decoded24 = read_wav_reader(Cursor::new(int24_buf.into_inner())).unwrap();
        assert!((decoded24.samples()[0] - 0.5).abs() < 1e-6);
        assert!((decoded24.samples()[1] + 0.5).abs() < 1e-6);
    }

    #[test]
    fn rejects_empty_and_misaligned_inputs() {
        assert!(matches!(
            encode_wav_i16_bytes(&[], 16_000, 1),
            Err(AudioError::EmptyAudio)
        ));
        assert!(matches!(
            encode_wav_i16_bytes(&[0.0; 3], 16_000, 2),
            Err(AudioError::InvalidParameter(_))
        ));
        assert!(matches!(
            encode_wav_i16_bytes(&[0.0; 4], 0, 1),
            Err(AudioError::InvalidParameter(_))
        ));
    }

    #[test]
    fn truncated_wav_with_huge_declared_data_chunk_does_not_oom() {
        // 44-byte PCM WAV header declaring 0xFFFF_FF00 bytes (~4 GiB) of 8-bit mono samples
        // with zero payload bytes following.
        let mut header = Vec::with_capacity(44);
        header.extend_from_slice(b"RIFF");
        header.extend_from_slice(&0xFFFF_FF24_u32.to_le_bytes());
        header.extend_from_slice(b"WAVE");
        header.extend_from_slice(b"fmt ");
        header.extend_from_slice(&16_u32.to_le_bytes());
        header.extend_from_slice(&1_u16.to_le_bytes()); // PCM
        header.extend_from_slice(&1_u16.to_le_bytes()); // 1 channel
        header.extend_from_slice(&16_000_u32.to_le_bytes()); // sample rate
        header.extend_from_slice(&16_000_u32.to_le_bytes()); // byte rate
        header.extend_from_slice(&1_u16.to_le_bytes()); // block align
        header.extend_from_slice(&8_u16.to_le_bytes()); // bits per sample
        header.extend_from_slice(b"data");
        header.extend_from_slice(&0xFFFF_FF00_u32.to_le_bytes());

        let err = read_wav_reader(Cursor::new(header)).unwrap_err();
        assert!(matches!(err, AudioError::EmptyAudio | AudioError::Wav(_)));
    }
}
