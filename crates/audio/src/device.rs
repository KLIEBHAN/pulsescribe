//! Input audio device discovery, selection heuristics, and platform block-size defaults.
//!
//! Mirrors `config.py`'s `get_input_device()` selection priorities and `utils/audio_latency.py`
//! while querying `cpal` fresh on each call (avoiding PortAudio's stale macOS device cache).

use cpal::traits::{DeviceTrait, HostTrait};
use cpal::{BufferSize, Device, StreamConfig, SupportedBufferSize, SupportedStreamConfig};

use crate::AudioError;

/// Environment variable selecting an exact input device name ahead of the system default.
pub const INPUT_DEVICE_ENV_VAR: &str = "PULSESCRIBE_INPUT_DEVICE";

/// Windows low-latency capture block duration in milliseconds (`WINDOWS_AUDIO_BLOCK_MS`).
pub const WINDOWS_AUDIO_BLOCK_MS: u32 = 20;

const WINDOWS_OUTPUT_KEYWORDS: &[&str] = &["lautsprecher", "speaker", "output", "monitor"];
const WINDOWS_MIC_ARRAY_KEYWORDS: &[&str] = &["mikrofonarray", "mic array"];
const MICROPHONE_KEYWORDS: &[&str] = &["mikrofon", "mic", "microphone"];

/// Metadata describing an available audio input device.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct InputDeviceInfo {
    /// Stable host device identifier when supported by the audio backend.
    pub id: Option<String>,
    /// Human-readable device name.
    pub name: String,
    /// Native default input sample rate in Hz.
    pub default_sample_rate: u32,
    /// Native default input channel count.
    pub default_channels: u16,
    /// Whether this device is currently the OS default input device.
    pub is_default: bool,
}

/// Read the preferred microphone name from `PULSESCRIBE_INPUT_DEVICE`, if set and non-empty.
pub fn preferred_device_from_env() -> Option<String> {
    std::env::var(INPUT_DEVICE_ENV_VAR)
        .ok()
        .map(|value| value.trim().to_string())
        .filter(|value| !value.is_empty())
}

/// Return a small Windows capture block size (20 ms) for responsive VAD/overlay feedback.
pub fn windows_audio_blocksize(sample_rate: u32) -> u32 {
    let frames = (u64::from(sample_rate) * u64::from(WINDOWS_AUDIO_BLOCK_MS)) / 1000;
    u32::try_from(frames).unwrap_or(u32::MAX).max(1)
}

/// Return the platform-appropriate capture block size in frames.
pub fn platform_audio_blocksize(sample_rate: u32, default_blocksize: u32) -> u32 {
    if cfg!(target_os = "windows") {
        windows_audio_blocksize(sample_rate)
    } else {
        default_blocksize.max(1)
    }
}

fn contains_any_keyword(name: &str, keywords: &[&str]) -> bool {
    let lower = name.to_lowercase();
    keywords.iter().any(|kw| lower.contains(kw))
}

fn is_windows_output_device(name: &str) -> bool {
    contains_any_keyword(name, WINDOWS_OUTPUT_KEYWORDS)
}

fn is_named_microphone(name: &str) -> bool {
    contains_any_keyword(name, MICROPHONE_KEYWORDS)
}

fn is_windows_mic_array(name: &str) -> bool {
    !is_windows_output_device(name) && contains_any_keyword(name, WINDOWS_MIC_ARRAY_KEYWORDS)
}

fn is_windows_microphone(name: &str) -> bool {
    !is_windows_output_device(name) && is_named_microphone(name)
}

/// Choose the preferred input device index from an enumerated list.
///
/// Precedence mirrors `config.py::get_input_device()`:
/// 1. Exact match on `preferred_name` (when non-empty).
/// 2. Current OS default device (`is_default == true`).
/// 3. Platform heuristics (Windows: mic array -> microphone -> non-output capture -> first;
///    macOS/Linux: named microphone -> first).
pub fn select_input_device_index(
    devices: &[InputDeviceInfo],
    preferred_name: Option<&str>,
    is_windows: bool,
) -> Option<usize> {
    if devices.is_empty() {
        return None;
    }

    if let Some(preferred) = preferred_name.map(str::trim).filter(|s| !s.is_empty()) {
        if let Some(idx) = devices.iter().position(|d| d.name == preferred) {
            return Some(idx);
        }
        tracing::warn!(
            preferred,
            "configured input device not available; falling back to default"
        );
    }

    if let Some(idx) = devices.iter().position(|d| d.is_default) {
        return Some(idx);
    }

    if is_windows {
        devices
            .iter()
            .position(|d| is_windows_mic_array(&d.name))
            .or_else(|| devices.iter().position(|d| is_windows_microphone(&d.name)))
            .or_else(|| {
                devices
                    .iter()
                    .position(|d| !is_windows_output_device(&d.name))
            })
            .or(Some(0))
    } else {
        devices
            .iter()
            .position(|d| is_named_microphone(&d.name))
            .or(Some(0))
    }
}

/// Query all usable input devices fresh from the OS audio host.
pub fn list_input_devices() -> Result<Vec<InputDeviceInfo>, AudioError> {
    Ok(enumerate_input_devices()?
        .into_iter()
        .map(|entry| entry.info)
        .collect())
}

pub(crate) struct ResolvedInputDevice {
    pub device: Device,
    pub info: InputDeviceInfo,
    pub config: SupportedStreamConfig,
}

fn device_name(device: &Device) -> String {
    device
        .description()
        .map(|desc| desc.name().to_string())
        .unwrap_or_else(|_| device.to_string())
}

fn device_id_string(device: &Device) -> Option<String> {
    device.id().ok().map(|id| id.to_string())
}

fn is_same_device(
    candidate: &Device,
    candidate_id: Option<&str>,
    default: &Device,
    default_id: Option<&str>,
) -> bool {
    match (candidate_id, default_id) {
        (Some(a), Some(b)) => a == b,
        _ => candidate == default,
    }
}

fn enumerate_input_devices() -> Result<Vec<ResolvedInputDevice>, AudioError> {
    let host = cpal::default_host();
    let default_device = host.default_input_device();
    let default_id = default_device.as_ref().and_then(device_id_string);

    let iter = host
        .input_devices()
        .map_err(|err| AudioError::Capture(format!("failed to enumerate input devices: {err}")))?;

    let mut devices = Vec::new();
    for device in iter {
        let config = match device.default_input_config() {
            Ok(cfg) if cfg.sample_rate() > 0 && cfg.channels() > 0 => cfg,
            Ok(cfg) => {
                tracing::debug!(
                    device = %device_name(&device),
                    sample_rate = cfg.sample_rate(),
                    channels = cfg.channels(),
                    "skipping input device with zero sample rate or channels"
                );
                continue;
            }
            Err(err) => {
                tracing::debug!(device = %device_name(&device), %err, "skipping input device without usable default config");
                continue;
            }
        };
        let id = device_id_string(&device);
        let is_default = default_device
            .as_ref()
            .is_some_and(|def| is_same_device(&device, id.as_deref(), def, default_id.as_deref()));

        let info = InputDeviceInfo {
            id,
            name: device_name(&device),
            default_sample_rate: config.sample_rate(),
            default_channels: config.channels(),
            is_default,
        };
        devices.push(ResolvedInputDevice {
            device,
            info,
            config,
        });
    }

    // Some hosts expose a default input device even if it was omitted from enumeration.
    if devices.is_empty() {
        if let Some(default) = default_device {
            if let Ok(config) = default.default_input_config() {
                if config.sample_rate() > 0 && config.channels() > 0 {
                    let info = InputDeviceInfo {
                        id: default_id,
                        name: device_name(&default),
                        default_sample_rate: config.sample_rate(),
                        default_channels: config.channels(),
                        is_default: true,
                    };
                    devices.push(ResolvedInputDevice {
                        device: default,
                        info,
                        config,
                    });
                }
            }
        }
    }

    Ok(devices)
}

/// Resolve the active input device freshly from the OS audio host.
pub(crate) fn resolve_input_device(
    preferred_name: Option<&str>,
) -> Result<ResolvedInputDevice, AudioError> {
    let mut candidates = enumerate_input_devices()?;
    if candidates.is_empty() {
        return Err(AudioError::DeviceNotFound(
            "no audio input devices found".to_string(),
        ));
    }

    let infos: Vec<InputDeviceInfo> = candidates.iter().map(|c| c.info.clone()).collect();
    let idx = select_input_device_index(&infos, preferred_name, cfg!(target_os = "windows"))
        .ok_or_else(|| {
            AudioError::DeviceNotFound("no usable audio input device found".to_string())
        })?;
    Ok(candidates.swap_remove(idx))
}

/// Build a native `StreamConfig` respecting the driver's supported buffer-size range.
pub(crate) fn build_stream_config(
    supported: &SupportedStreamConfig,
    requested_blocksize: Option<u32>,
) -> StreamConfig {
    let buffer_size = match (requested_blocksize, supported.buffer_size()) {
        (Some(frames), SupportedBufferSize::Range { min, max }) if frames > 0 => {
            BufferSize::Fixed(frames.clamp(*min, *max))
        }
        _ => BufferSize::Default,
    };

    StreamConfig {
        channels: supported.channels(),
        sample_rate: supported.sample_rate(),
        buffer_size,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn dev(name: &str, is_default: bool) -> InputDeviceInfo {
        InputDeviceInfo {
            id: None,
            name: name.to_string(),
            default_sample_rate: 48_000,
            default_channels: 1,
            is_default,
        }
    }

    #[test]
    fn windows_blocksize_matches_20ms_contract() {
        assert_eq!(windows_audio_blocksize(16_000), 320);
        assert_eq!(windows_audio_blocksize(48_000), 960);
        assert_eq!(windows_audio_blocksize(44_100), 882);
        assert_eq!(windows_audio_blocksize(0), 1);
    }

    #[test]
    fn preferred_name_overrides_default_and_falls_back_when_missing() {
        let devices = vec![dev("MacBook Pro Microphone", true), dev("USB Yeti", false)];
        assert_eq!(
            select_input_device_index(&devices, Some("  USB Yeti "), false),
            Some(1)
        );
        assert_eq!(
            select_input_device_index(&devices, Some("Missing Mic"), false),
            Some(0)
        );
    }

    #[test]
    fn windows_heuristics_prioritize_mic_array_over_mic_over_capture_excluding_outputs() {
        let devices = vec![
            dev("Lautsprecher (Realtek Audio)", false),
            dev("Monitor of HDMI", false),
            dev("Mikrofon (USB Audio)", false),
            dev("Mikrofonarray (Intel Smart Sound)", false),
        ];
        assert_eq!(select_input_device_index(&devices, None, true), Some(3));

        let no_array = vec![
            dev("Speaker Loopback", false),
            dev("Line In (USB)", false),
            dev("USB Microphone", false),
        ];
        assert_eq!(select_input_device_index(&no_array, None, true), Some(2));

        let capture_only = vec![
            dev("Output Loopback", false),
            dev("Line In (Realtek)", false),
        ];
        assert_eq!(
            select_input_device_index(&capture_only, None, true),
            Some(1)
        );

        let outputs_only = vec![dev("Speaker Monitor", false)];
        assert_eq!(
            select_input_device_index(&outputs_only, None, true),
            Some(0)
        );
    }

    #[test]
    fn non_windows_heuristics_prefer_named_microphone_when_no_default() {
        let devices = vec![
            dev("BlackHole 2ch", false),
            dev("MacBook Pro Microphone", false),
        ];
        assert_eq!(select_input_device_index(&devices, None, false), Some(1));

        let no_mic_name = vec![dev("BlackHole 2ch", false), dev("Aggregate Device", false)];
        assert_eq!(
            select_input_device_index(&no_mic_name, None, false),
            Some(0)
        );
        assert_eq!(select_input_device_index(&[], None, false), None);
    }

    #[test]
    fn host_device_enumeration_returns_valid_specs_or_clean_error() {
        match list_input_devices() {
            Ok(devices) => {
                for device in devices {
                    assert!(device.default_sample_rate > 0);
                    assert!(device.default_channels > 0);
                }
            }
            Err(AudioError::Capture(_)) => {}
            Err(other) => panic!("unexpected enumeration error: {other:?}"),
        }
    }
}
