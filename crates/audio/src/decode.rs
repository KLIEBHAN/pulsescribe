//! Multi-format audio file decoding via `symphonia` (WAV, MP3, AAC/M4A, OGG/Vorbis, FLAC).

use std::fs::File;
use std::io::Cursor;
use std::path::Path;

use symphonia::core::codecs::audio::AudioDecoderOptions;
use symphonia::core::codecs::CodecParameters;
use symphonia::core::errors::Error as SymphoniaError;
use symphonia::core::formats::probe::Hint;
use symphonia::core::formats::{FormatOptions, TrackType};
use symphonia::core::io::{MediaSource, MediaSourceStream, MediaSourceStreamOptions};
use symphonia::core::meta::MetadataOptions;

use crate::{AudioBuffer, AudioError};

/// Decode an audio file on disk (WAV, MP3, M4A/AAC, OGG/Vorbis, FLAC) into interleaved `f32` PCM.
pub fn decode_file(path: &Path) -> Result<AudioBuffer, AudioError> {
    let file = File::open(path)?;
    let ext = path.extension().and_then(|e| e.to_str());
    decode_media_source(Box::new(file), ext)
}

/// Decode an in-memory audio container into interleaved `f32` PCM.
pub fn decode_bytes(
    bytes: Vec<u8>,
    extension_hint: Option<&str>,
) -> Result<AudioBuffer, AudioError> {
    decode_media_source(Box::new(Cursor::new(bytes)), extension_hint)
}

/// Decode any supported audio file directly into 16 kHz mono `f32` samples.
pub fn decode_file_to_mono_16k(path: &Path) -> Result<Vec<f32>, AudioError> {
    decode_file(path)?.into_mono_16k()
}

fn decode_media_source(
    source: Box<dyn MediaSource>,
    extension_hint: Option<&str>,
) -> Result<AudioBuffer, AudioError> {
    let mss = MediaSourceStream::new(source, MediaSourceStreamOptions::default());

    let mut hint = Hint::new();
    if let Some(ext) = extension_hint {
        hint.with_extension(ext);
    }

    let mut format = symphonia::default::get_probe()
        .probe(
            &hint,
            mss,
            FormatOptions::default(),
            MetadataOptions::default(),
        )
        .map_err(|err| AudioError::Decode(format!("failed to probe audio container: {err}")))?;

    let (track_id, audio_params) = {
        let track = format
            .default_track(TrackType::Audio)
            .ok_or_else(|| AudioError::Decode("no supported audio track found".to_string()))?;
        let params = match &track.codec_params {
            Some(CodecParameters::Audio(audio)) => audio.clone(),
            _ => {
                return Err(AudioError::Decode(
                    "selected track has no audio codec parameters".to_string(),
                ));
            }
        };
        (track.id, params)
    };

    let mut decoder = symphonia::default::get_codecs()
        .make_audio_decoder(&audio_params, &AudioDecoderOptions::default())
        .map_err(|err| AudioError::Decode(format!("unsupported audio codec: {err}")))?;

    let mut samples = Vec::new();
    let mut packet_buf = Vec::new();
    let mut sample_rate: Option<u32> = None;
    let mut channels: Option<u16> = None;

    while let Some(packet) = format
        .next_packet()
        .map_err(|err| AudioError::Decode(format!("failed reading audio packet: {err}")))?
    {
        if packet.track_id != track_id {
            continue;
        }

        let decoded = match decoder.decode(&packet) {
            Ok(decoded) => decoded,
            Err(SymphoniaError::DecodeError(msg)) => {
                tracing::warn!(%msg, "skipping corrupt audio frame during decode");
                continue;
            }
            Err(err) => {
                return Err(AudioError::Decode(format!(
                    "failed decoding audio packet: {err}"
                )));
            }
        };

        if decoded.is_empty() {
            continue;
        }

        let spec = decoded.spec();
        let pkt_rate = spec.rate();
        let pkt_channels = u16::try_from(spec.channels().count()).map_err(|_| {
            AudioError::Decode(format!(
                "unsupported channel count: {}",
                spec.channels().count()
            ))
        })?;
        if pkt_rate == 0 || pkt_channels == 0 {
            return Err(AudioError::Decode(format!(
                "decoded packet has invalid audio spec: rate={pkt_rate}, channels={pkt_channels}"
            )));
        }

        match (sample_rate, channels) {
            (None, None) => {
                sample_rate = Some(pkt_rate);
                channels = Some(pkt_channels);
            }
            (Some(r), Some(c)) if r == pkt_rate && c == pkt_channels => {}
            (Some(r), Some(c)) => {
                return Err(AudioError::Decode(format!(
                    "mid-stream format change ({r} Hz/{c} ch -> {pkt_rate} Hz/{pkt_channels} ch) is not supported"
                )));
            }
            _ => unreachable!(),
        }

        packet_buf.clear();
        decoded.copy_to_vec_interleaved(&mut packet_buf);
        samples.extend_from_slice(&packet_buf);
    }

    let (Some(sample_rate), Some(channels)) = (sample_rate, channels) else {
        return Err(AudioError::EmptyAudio);
    };
    if samples.is_empty() {
        return Err(AudioError::EmptyAudio);
    }

    AudioBuffer::new(samples, sample_rate, channels)
}

#[cfg(test)]
mod tests {
    use std::f32::consts::TAU;

    use super::*;
    use crate::wav::encode_wav_i16_bytes;
    use crate::TARGET_SAMPLE_RATE;

    #[test]
    fn decodes_stereo_48k_wav_to_16k_mono() {
        let from_rate = 48_000_u32;
        let frames = from_rate as usize / 2; // 0.5 s
        let mut stereo = Vec::with_capacity(frames * 2);
        for i in 0..frames {
            let v = 0.6 * (TAU * 440.0 * i as f32 / from_rate as f32).sin();
            stereo.push(v);
            stereo.push(v);
        }

        let wav_bytes = encode_wav_i16_bytes(&stereo, from_rate, 2).unwrap();
        // Probe without an extension hint to exercise content sniffing as well.
        let decoded = decode_bytes(wav_bytes.clone(), None).unwrap();
        assert_eq!(decoded.sample_rate(), from_rate);
        assert_eq!(decoded.channels(), 2);
        assert_eq!(decoded.frame_count(), frames);
        assert!((decoded.duration_secs() - 0.5).abs() < 1e-6);

        let mono_16k = decoded.into_mono_16k().unwrap();
        assert_eq!(mono_16k.len(), TARGET_SAMPLE_RATE as usize / 2);

        let level = crate::compute_level_f32(&mono_16k[500..7500]);
        let expected_rms = 0.6 * std::f32::consts::FRAC_1_SQRT_2;
        assert!(
            (level.rms - expected_rms).abs() < 1e-2,
            "unexpected decoded RMS: {} vs {}",
            level.rms,
            expected_rms
        );

        let path = std::env::temp_dir().join(format!(
            "pulsescribe_audio_decode_test_{}.wav",
            std::process::id()
        ));
        std::fs::write(&path, &wav_bytes).unwrap();
        let from_file = decode_file_to_mono_16k(&path).unwrap();
        let _ = std::fs::remove_file(&path);
        assert_eq!(from_file, mono_16k);
    }

    #[test]
    fn rejects_invalid_container_bytes() {
        let err = decode_bytes(b"not an audio file".to_vec(), Some("mp3")).unwrap_err();
        assert!(matches!(err, AudioError::Decode(_)));
    }
}
