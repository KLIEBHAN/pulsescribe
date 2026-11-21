//! Live microphone capture, warm-stream management, and `AudioRecorder` via `cpal`.

use std::path::Path;
use std::sync::{Arc, Condvar, Mutex};
use std::thread::{self, JoinHandle};
use std::time::Duration;

use cpal::traits::{DeviceTrait, StreamTrait};
use cpal::{
    BufferSize, FromSample, Sample, SampleFormat, SizedSample, Stream, StreamConfig, I24, U24,
};

use crate::device::{
    build_stream_config, platform_audio_blocksize, preferred_device_from_env, resolve_input_device,
    InputDeviceInfo, ResolvedInputDevice,
};
use crate::ring::{audio_ring_channel, AtomicU32Ref, AudioRingReader, AudioRingWriter};
use crate::wav::write_wav_i16;
use crate::{AudioBuffer, AudioError, DEFAULT_BLOCKSIZE};

const DEFAULT_RING_BUFFER_SECONDS: f32 = 5.0;
const MAX_RING_BUFFER_SECONDS: f32 = 60.0;
const MIN_RING_CAPACITY_SAMPLES: usize = 1024;
const MAX_RING_CAPACITY_SAMPLES: usize = 24_000_000; // ~96 MiB cap even for extreme sample rates
const MAX_INITIAL_RECORDING_CAPACITY: usize = 480_000; // 10 s at 48 kHz; grows dynamically
const DRAIN_POLL_INTERVAL: Duration = Duration::from_millis(10);

/// Configuration for opening a live `CaptureStream` or `AudioRecorder`.
#[derive(Debug, Clone)]
pub struct CaptureConfig {
    /// Exact preferred input device name (falls back to OS default if unavailable).
    pub device_name: Option<String>,
    /// Lock-free ring buffer capacity in seconds (default: `5.0`, max: `60.0`).
    pub buffer_seconds: f32,
    /// Optional requested capture block size in frames; `None` uses the platform default
    /// (20 ms low-latency hint on Windows, driver default elsewhere).
    pub blocksize: Option<u32>,
    /// Whether samples are immediately buffered upon opening (`false` for warm-stream idle mode).
    pub initially_armed: bool,
}

impl Default for CaptureConfig {
    fn default() -> Self {
        Self {
            device_name: None,
            buffer_seconds: DEFAULT_RING_BUFFER_SECONDS,
            blocksize: None,
            initially_armed: true,
        }
    }
}

impl CaptureConfig {
    /// Build a capture configuration picking up `PULSESCRIBE_INPUT_DEVICE` from the environment.
    pub fn from_env() -> Self {
        Self {
            device_name: preferred_device_from_env(),
            ..Self::default()
        }
    }

    /// Build a warm-stream configuration (open and running, disarmed until `arm()` is called).
    pub fn warm_stream_from_env() -> Self {
        Self {
            initially_armed: false,
            ..Self::from_env()
        }
    }

    fn validate(&self) -> Result<(), AudioError> {
        if !self.buffer_seconds.is_finite()
            || self.buffer_seconds <= 0.0
            || self.buffer_seconds > MAX_RING_BUFFER_SECONDS
        {
            return Err(AudioError::InvalidParameter(format!(
                "buffer_seconds must be within (0, {MAX_RING_BUFFER_SECONDS}], got {}",
                self.buffer_seconds
            )));
        }
        if self.blocksize == Some(0) {
            return Err(AudioError::InvalidParameter(
                "blocksize must be greater than 0".to_string(),
            ));
        }
        Ok(())
    }

    fn ring_capacity_samples(&self, sample_rate: u32) -> usize {
        let scaled = (f64::from(sample_rate) * f64::from(self.buffer_seconds)).ceil();
        let bounded = scaled.clamp(
            MIN_RING_CAPACITY_SAMPLES as f64,
            MAX_RING_CAPACITY_SAMPLES as f64,
        );
        bounded as usize
    }

    fn requested_blocksize_for(&self, sample_rate: u32) -> Option<u32> {
        if let Some(explicit) = self.blocksize {
            return Some(explicit);
        }
        if cfg!(target_os = "windows") {
            Some(platform_audio_blocksize(
                sample_rate,
                DEFAULT_BLOCKSIZE as u32,
            ))
        } else {
            None
        }
    }
}

/// Active `cpal` input stream connected to a lock-free mono `AudioRingReader`.
///
/// Supports both immediate recording (`initially_armed: true`) and always-on warm streams
/// (`initially_armed: false`, toggled via `arm()` / `disarm()`).
pub struct CaptureStream {
    stream: Stream,
    device: InputDeviceInfo,
    sample_rate: u32,
    channels: u16,
    reader: AudioRingReader,
    stream_error: Arc<Mutex<Option<String>>>,
}

impl CaptureStream {
    /// Open and start a live microphone capture stream with one automatic device reprobe on failure.
    pub fn open(config: CaptureConfig) -> Result<Self, AudioError> {
        config.validate()?;
        let auto_device = config.device_name.is_none();
        let resolved = resolve_input_device(config.device_name.as_deref())?;

        match open_stream_for_device(&resolved, &config) {
            Ok(stream) => Ok(stream),
            Err(first_err) if auto_device => {
                let Ok(refreshed) = resolve_input_device(None) else {
                    return Err(first_err);
                };
                if same_resolved_target(&resolved.info, &refreshed.info) {
                    return Err(first_err);
                }
                tracing::info!(
                    previous = %resolved.info.name,
                    refreshed = %refreshed.info.name,
                    "input stream open failed; retrying after device reprobe"
                );
                open_stream_for_device(&refreshed, &config)
            }
            Err(err) => Err(err),
        }
    }

    pub fn device(&self) -> &InputDeviceInfo {
        &self.device
    }

    /// Native hardware sample rate in Hz.
    pub fn sample_rate(&self) -> u32 {
        self.sample_rate
    }

    /// Native hardware input channel count (samples drained from `reader_mut()` are always mono).
    pub fn native_channels(&self) -> u16 {
        self.channels
    }

    /// Access the lock-free consumer to drain captured mono `f32` samples.
    pub fn reader_mut(&mut self) -> &mut AudioRingReader {
        &mut self.reader
    }

    /// Clear any stale audio and start buffering samples immediately (warm-stream start).
    pub fn arm(&mut self) {
        self.reader.clear_and_arm();
    }

    /// Stop buffering incoming samples while keeping the hardware stream warm.
    pub fn disarm(&mut self) {
        self.reader.disarm();
    }

    pub fn is_armed(&self) -> bool {
        self.reader.is_armed()
    }

    pub fn latest_rms(&self) -> f32 {
        self.reader.latest_rms()
    }

    pub fn dropped_samples(&self) -> u64 {
        self.reader.dropped_samples()
    }

    /// Return and clear the first runtime stream error reported by `cpal`, if any.
    pub fn take_stream_error(&self) -> Option<String> {
        self.stream_error
            .lock()
            .ok()
            .and_then(|mut guard| guard.take())
    }

    /// Split into the running stream handle (drop to stop capture) and the consumer reader.
    fn split(self) -> (StreamHandle, AudioRingReader) {
        let handle = StreamHandle {
            stream: self.stream,
            device: self.device,
            sample_rate: self.sample_rate,
            stream_error: self.stream_error,
        };
        (handle, self.reader)
    }
}

struct StreamHandle {
    stream: Stream,
    device: InputDeviceInfo,
    sample_rate: u32,
    stream_error: Arc<Mutex<Option<String>>>,
}

impl StreamHandle {
    fn stop(self) -> Option<String> {
        let pause_err = self.stream.pause().err().map(|e| e.to_string());
        drop(self.stream);
        let cb_err = self
            .stream_error
            .lock()
            .ok()
            .and_then(|mut guard| guard.take());
        cb_err.or(pause_err)
    }
}

fn same_resolved_target(a: &InputDeviceInfo, b: &InputDeviceInfo) -> bool {
    a.id == b.id
        && a.name == b.name
        && a.default_sample_rate == b.default_sample_rate
        && a.default_channels == b.default_channels
}

fn open_stream_for_device(
    resolved: &ResolvedInputDevice,
    config: &CaptureConfig,
) -> Result<CaptureStream, AudioError> {
    let sample_rate = resolved.config.sample_rate();
    let sample_format = resolved.config.sample_format();
    let requested_blocksize = config.requested_blocksize_for(sample_rate);
    let stream_cfg = build_stream_config(&resolved.config, requested_blocksize);

    match build_and_play(resolved, stream_cfg, sample_format, config) {
        Ok(capture) => Ok(capture),
        Err(err) if stream_cfg.buffer_size != BufferSize::Default => {
            tracing::debug!(
                device = %resolved.info.name,
                %err,
                "fixed buffer size rejected by driver; falling back to default buffer size"
            );
            let fallback_cfg = StreamConfig {
                buffer_size: BufferSize::Default,
                ..stream_cfg
            };
            build_and_play(resolved, fallback_cfg, sample_format, config)
        }
        Err(err) => Err(err),
    }
}

fn build_and_play(
    resolved: &ResolvedInputDevice,
    stream_cfg: StreamConfig,
    sample_format: SampleFormat,
    config: &CaptureConfig,
) -> Result<CaptureStream, AudioError> {
    let sample_rate = stream_cfg.sample_rate;
    let channels = stream_cfg.channels;
    let capacity = config.ring_capacity_samples(sample_rate);
    let (writer, reader) = audio_ring_channel(capacity, channels, config.initially_armed)?;
    let stream_error = Arc::new(Mutex::new(None));

    macro_rules! build_converted {
        ($sample_ty:ty) => {
            build_converted_input_stream::<$sample_ty>(
                &resolved.device,
                stream_cfg,
                writer,
                Arc::clone(&stream_error),
            )
        };
    }

    let stream = match sample_format {
        SampleFormat::F32 => build_f32_input_stream(
            &resolved.device,
            stream_cfg,
            writer,
            Arc::clone(&stream_error),
        ),
        SampleFormat::I16 => build_converted!(i16),
        SampleFormat::I32 => build_converted!(i32),
        SampleFormat::U16 => build_converted!(u16),
        SampleFormat::I8 => build_converted!(i8),
        SampleFormat::U8 => build_converted!(u8),
        SampleFormat::I24 => build_converted!(I24),
        SampleFormat::U24 => build_converted!(U24),
        SampleFormat::F64 => build_converted!(f64),
        SampleFormat::I64 => build_converted!(i64),
        SampleFormat::U32 => build_converted!(u32),
        SampleFormat::U64 => build_converted!(u64),
        other => {
            return Err(AudioError::Capture(format!(
                "unsupported cpal sample format: {other:?}"
            )));
        }
    }
    .map_err(|err| AudioError::Capture(format!("failed to build input stream: {err}")))?;

    stream
        .play()
        .map_err(|err| AudioError::Capture(format!("failed to start input stream: {err}")))?;

    tracing::info!(
        device = %resolved.info.name,
        sample_rate,
        channels,
        ?sample_format,
        armed = config.initially_armed,
        "audio input stream started"
    );

    Ok(CaptureStream {
        stream,
        device: resolved.info.clone(),
        sample_rate,
        channels,
        reader,
        stream_error,
    })
}

fn build_f32_input_stream(
    device: &cpal::Device,
    config: StreamConfig,
    mut writer: AudioRingWriter,
    stream_error: Arc<Mutex<Option<String>>>,
) -> Result<Stream, cpal::Error> {
    device.build_input_stream(
        config,
        move |data: &[f32], _: &cpal::InputCallbackInfo| {
            writer.push_interleaved_f32(data);
        },
        make_error_callback(stream_error),
        None,
    )
}

fn build_converted_input_stream<T>(
    device: &cpal::Device,
    config: StreamConfig,
    mut writer: AudioRingWriter,
    stream_error: Arc<Mutex<Option<String>>>,
) -> Result<Stream, cpal::Error>
where
    T: SizedSample,
    f32: FromSample<T>,
{
    device.build_input_stream(
        config,
        move |data: &[T], _: &cpal::InputCallbackInfo| {
            writer.push_interleaved_converted(data, f32::from_sample);
        },
        make_error_callback(stream_error),
        None,
    )
}

fn make_error_callback(
    stream_error: Arc<Mutex<Option<String>>>,
) -> impl FnMut(cpal::Error) + Send + 'static {
    move |err| {
        tracing::warn!(%err, "audio input stream error");
        if let Ok(mut guard) = stream_error.try_lock() {
            if guard.is_none() {
                *guard = Some(err.to_string());
            }
        }
    }
}

struct StopSignal {
    stopped: Mutex<bool>,
    condvar: Condvar,
}

impl StopSignal {
    fn new() -> Self {
        Self {
            stopped: Mutex::new(false),
            condvar: Condvar::new(),
        }
    }

    fn signal(&self) {
        if let Ok(mut stopped) = self.stopped.lock() {
            *stopped = true;
            self.condvar.notify_all();
        }
    }

    /// Wait up to `timeout`; returns `true` once stop has been requested.
    fn wait_timeout(&self, timeout: Duration) -> bool {
        let Ok(guard) = self.stopped.lock() else {
            return true;
        };
        if *guard {
            return true;
        }
        self.condvar
            .wait_timeout(guard, timeout)
            .map(|(guard, _)| *guard)
            .unwrap_or(true)
    }
}

fn run_drain_loop(
    mut reader: AudioRingReader,
    stop: Arc<StopSignal>,
    initial_capacity: usize,
) -> (Vec<f32>, u64) {
    let mut recorded = Vec::with_capacity(initial_capacity);
    loop {
        reader.drain_into(&mut recorded);
        if stop.wait_timeout(DRAIN_POLL_INTERVAL) {
            break;
        }
    }
    reader.drain_into(&mut recorded);
    (recorded, reader.dropped_samples())
}

struct ActiveRecording {
    handle: StreamHandle,
    stop_signal: Arc<StopSignal>,
    rms_ref: Arc<AtomicU32Ref>,
    drain_thread: Option<JoinHandle<(Vec<f32>, u64)>>,
}

/// High-level microphone recorder mirroring `audio/recording.py::AudioRecorder`.
///
/// Captures mono `f32` audio on a lock-free ring buffer drained by a dedicated thread so
/// arbitrarily long recordings never overflow the real-time callback buffer.
pub struct AudioRecorder {
    config: CaptureConfig,
    active: Option<ActiveRecording>,
}

impl AudioRecorder {
    /// Create a recorder with an explicit `CaptureConfig`.
    pub fn new(config: CaptureConfig) -> Self {
        Self {
            config,
            active: None,
        }
    }

    /// Create a recorder configured from `PULSESCRIBE_INPUT_DEVICE`.
    pub fn from_env() -> Self {
        Self::new(CaptureConfig::from_env())
    }

    /// Start capturing audio from the resolved input device.
    pub fn start(&mut self) -> Result<InputDeviceInfo, AudioError> {
        if self.active.is_some() {
            return Err(AudioError::Capture(
                "recording is already active".to_string(),
            ));
        }

        let mut cfg = self.config.clone();
        cfg.initially_armed = true;
        let capture = CaptureStream::open(cfg)?;
        let device = capture.device().clone();
        let initial_cap = (capture.sample_rate() as usize)
            .saturating_mul(10)
            .min(MAX_INITIAL_RECORDING_CAPACITY);

        let (handle, reader) = capture.split();
        let rms_ref = reader.shared_rms_handle();
        let stop_signal = Arc::new(StopSignal::new());
        let thread_stop = Arc::clone(&stop_signal);

        let drain_thread = match thread::Builder::new()
            .name("pulsescribe-audio-drain".to_string())
            .spawn(move || run_drain_loop(reader, thread_stop, initial_cap))
        {
            Ok(thread) => thread,
            Err(err) => {
                let _ = handle.stop();
                return Err(AudioError::Io(err));
            }
        };

        self.active = Some(ActiveRecording {
            handle,
            stop_signal,
            rms_ref,
            drain_thread: Some(drain_thread),
        });
        Ok(device)
    }

    /// Returns `true` while a recording session is active.
    pub fn is_recording(&self) -> bool {
        self.active.is_some()
    }

    /// Return the currently active input device, if recording.
    pub fn active_device(&self) -> Option<&InputDeviceInfo> {
        self.active.as_ref().map(|a| &a.handle.device)
    }

    /// Live RMS level (`0.0..=1.0`) from the active audio callback, or `0.0` when idle.
    pub fn latest_rms(&self) -> f32 {
        self.active
            .as_ref()
            .map(|a| a.rms_ref.latest_rms())
            .unwrap_or(0.0)
    }

    /// Stop recording, drain all remaining samples, and return a validated mono `AudioBuffer`.
    pub fn stop(&mut self) -> Result<AudioBuffer, AudioError> {
        let mut active = self
            .active
            .take()
            .ok_or_else(|| AudioError::Capture("no active recording".to_string()))?;

        let sample_rate = active.handle.sample_rate;
        let stream_err = active.handle.stop();
        active.stop_signal.signal();

        let (samples, dropped) = active
            .drain_thread
            .take()
            .ok_or_else(|| AudioError::Capture("drain thread missing".to_string()))?
            .join()
            .map_err(|_| AudioError::Capture("audio drain thread panicked".to_string()))?;

        if dropped > 0 {
            tracing::warn!(
                dropped_samples = dropped,
                "audio samples dropped during recording"
            );
        }
        if let Some(err) = stream_err {
            return Err(AudioError::Capture(err));
        }
        if samples.is_empty() {
            return Err(AudioError::EmptyAudio);
        }

        AudioBuffer::new(samples, sample_rate, 1)
    }

    /// Stop recording and write the captured mono audio as a 16-bit PCM WAV file.
    pub fn stop_to_wav(&mut self, output_path: &Path) -> Result<AudioBuffer, AudioError> {
        let buffer = self.stop()?;
        write_wav_i16(
            output_path,
            buffer.samples(),
            buffer.sample_rate(),
            buffer.channels(),
        )?;
        Ok(buffer)
    }
}

impl Drop for AudioRecorder {
    fn drop(&mut self) {
        if let Some(mut active) = self.active.take() {
            let _ = active.handle.stop();
            active.stop_signal.signal();
            if let Some(thread) = active.drain_thread.take() {
                let _ = thread.join();
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn capture_config_validation_rejects_invalid_buffer_seconds() {
        for bad in [0.0_f32, -1.0, f32::NAN, f32::INFINITY, 61.0] {
            let cfg = CaptureConfig {
                buffer_seconds: bad,
                ..CaptureConfig::default()
            };
            assert!(matches!(
                cfg.validate(),
                Err(AudioError::InvalidParameter(_))
            ));
        }

        let bad_blocksize = CaptureConfig {
            blocksize: Some(0),
            ..CaptureConfig::default()
        };
        assert!(matches!(
            bad_blocksize.validate(),
            Err(AudioError::InvalidParameter(_))
        ));

        let valid = CaptureConfig::default();
        assert!(valid.validate().is_ok());
        assert_eq!(valid.ring_capacity_samples(16_000), 80_000);
        assert_eq!(valid.ring_capacity_samples(100), MIN_RING_CAPACITY_SAMPLES);
        assert_eq!(
            valid.ring_capacity_samples(u32::MAX),
            MAX_RING_CAPACITY_SAMPLES
        );
    }

    #[test]
    fn drain_loop_collects_all_samples_and_exits_promptly_on_stop() {
        let (mut writer, reader) = audio_ring_channel(4096, 1, true).unwrap();
        let stop = Arc::new(StopSignal::new());
        let drain_stop = Arc::clone(&stop);

        let drain_handle = thread::spawn(move || run_drain_loop(reader, drain_stop, 1024));

        let expected: Vec<f32> = (0..2048).map(|i| i as f32 * 0.0001).collect();
        for chunk in expected.chunks(64) {
            writer.push_interleaved_f32(chunk);
        }
        drop(writer);
        stop.signal();

        let (recorded, dropped) = drain_handle.join().unwrap();
        assert_eq!(dropped, 0);
        assert_eq!(recorded, expected);
    }

    #[test]
    fn stopping_idle_recorder_returns_capture_error() {
        let mut recorder = AudioRecorder::new(CaptureConfig::default());
        assert!(!recorder.is_recording());
        assert_eq!(recorder.latest_rms(), 0.0);
        assert!(matches!(recorder.stop(), Err(AudioError::Capture(_))));
    }
}
