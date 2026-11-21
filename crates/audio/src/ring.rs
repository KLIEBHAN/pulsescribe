//! Lock-free SPSC audio ring buffer for real-time capture and warm-stream handoff.
//!
//! The producer (`AudioRingWriter`) is designed to run inside OS audio callbacks with zero
//! heap allocations and zero locks: it downmixes interleaved channels on the stack, tags each
//! sample with the active session generation so stale in-flight callbacks can never leak across
//! `clear_and_arm()`, updates an atomic RMS level for overlays/VAD, and respects the atomic
//! session gate so warm streams can stay open while idle and start capturing instantaneously.

use std::sync::atomic::{AtomicU32, AtomicU64, Ordering};
use std::sync::Arc;

use ringbuf::traits::{Consumer, Producer, Split};
use ringbuf::{HeapCons, HeapProd, HeapRb};

use crate::AudioError;

const STACK_DOWNMIX_FRAMES: usize = 256;
const DISARMED_SESSION: u32 = 0;

#[derive(Debug, Clone, Copy)]
struct TaggedSample {
    session: u32,
    value: f32,
}

fn next_session_id(current: u32) -> u32 {
    let next = current.wrapping_add(1);
    if next == DISARMED_SESSION {
        1
    } else {
        next
    }
}

struct SharedRingState {
    /// `0` when disarmed; nonzero generation ID when armed.
    active_session: AtomicU32,
    dropped_samples: AtomicU64,
    latest_rms_bits: AtomicU32,
}

impl SharedRingState {
    fn new(initial_session: u32) -> Self {
        Self {
            active_session: AtomicU32::new(initial_session),
            dropped_samples: AtomicU64::new(0),
            latest_rms_bits: AtomicU32::new(0.0_f32.to_bits()),
        }
    }

    #[inline]
    fn active_session(&self) -> u32 {
        self.active_session.load(Ordering::Acquire)
    }

    #[inline]
    fn record_rms(&self, rms: f32) {
        let sanitized = if rms.is_finite() { rms } else { 0.0 };
        self.latest_rms_bits
            .store(sanitized.to_bits(), Ordering::Relaxed);
    }

    #[inline]
    fn latest_rms(&self) -> f32 {
        f32::from_bits(self.latest_rms_bits.load(Ordering::Relaxed))
    }

    #[inline]
    fn add_dropped(&self, session: u32, count: usize) {
        if count > 0 && self.active_session.load(Ordering::Relaxed) == session {
            self.dropped_samples
                .fetch_add(count as u64, Ordering::Relaxed);
        }
    }
}

/// Real-time producer side of an audio capture ring buffer (zero allocations, lock-free).
pub struct AudioRingWriter {
    prod: HeapProd<TaggedSample>,
    channels: usize,
    shared: Arc<SharedRingState>,
}

impl AudioRingWriter {
    /// Push interleaved `f32` samples from an audio callback, downmixing to mono on the fly.
    pub fn push_interleaved_f32(&mut self, samples: &[f32]) {
        self.push_interleaved_converted(samples, |s| s);
    }

    /// Convert interleaved samples of any audio format to `f32` and downmix to mono on the fly.
    pub fn push_interleaved_converted<T: Copy>(
        &mut self,
        samples: &[T],
        to_f32: impl Fn(T) -> f32,
    ) {
        if samples.is_empty() {
            return;
        }
        let session = self.shared.active_session();
        self.push_for_session(samples, session, to_f32);
    }

    fn push_for_session<T: Copy>(
        &mut self,
        samples: &[T],
        session: u32,
        to_f32: impl Fn(T) -> f32,
    ) {
        let mut tagged_buf = [TaggedSample {
            session,
            value: 0.0,
        }; STACK_DOWNMIX_FRAMES];
        let mut sum_sq = 0.0_f64;
        let mut total_frames = 0_usize;
        let inv_channels = 1.0 / self.channels as f32;

        let mut frames_iter = samples.chunks_exact(self.channels);
        loop {
            let mut count = 0;
            for slot in &mut tagged_buf {
                let Some(frame) = frames_iter.next() else {
                    break;
                };
                let mono = if self.channels == 1 {
                    to_f32(frame[0])
                } else if self.channels == 2 {
                    (to_f32(frame[0]) + to_f32(frame[1])) * 0.5
                } else {
                    frame.iter().copied().map(&to_f32).sum::<f32>() * inv_channels
                };
                slot.value = mono;
                if mono.is_finite() {
                    let v = f64::from(mono);
                    sum_sq += v * v;
                }
                count += 1;
            }
            if count == 0 {
                break;
            }
            total_frames += count;
            if session != DISARMED_SESSION {
                let written = self.prod.push_slice(&tagged_buf[..count]);
                self.shared.add_dropped(session, count - written);
            }
        }

        if total_frames > 0 {
            self.shared
                .record_rms((sum_sq / total_frames as f64).sqrt() as f32);
        }
    }
}

/// Consumer side of an audio capture ring buffer yielding mono `f32` samples.
pub struct AudioRingReader {
    cons: HeapCons<TaggedSample>,
    current_session: u32,
    shared: Arc<SharedRingState>,
}

impl AudioRingReader {
    /// Drain all buffered mono `f32` samples belonging to the active session into `out`.
    ///
    /// Samples written by an in-flight callback from an earlier session generation are discarded
    /// automatically so `clear_and_arm()` never leaks previous-session audio.
    pub fn drain_into(&mut self, out: &mut Vec<f32>) -> usize {
        let (first, second) = self.cons.as_slices();
        let total = first.len() + second.len();
        if total == 0 {
            return 0;
        }

        let target = self.current_session;
        let mut appended = 0_usize;
        if target != DISARMED_SESSION {
            out.reserve(total);
            for sample in first.iter().chain(second.iter()) {
                if sample.session == target {
                    out.push(sample.value);
                    appended += 1;
                }
            }
        }
        self.cons.skip(total);
        appended
    }

    /// Discard all currently buffered samples in O(1).
    pub fn clear(&mut self) {
        self.cons.clear();
    }

    /// Enable sample capture into the ring buffer (no-op if already armed).
    pub fn arm(&mut self) {
        if self.is_armed() {
            return;
        }
        self.current_session = next_session_id(self.current_session);
        self.shared
            .active_session
            .store(self.current_session, Ordering::Release);
    }

    /// Disable sample capture (warm-stream idle mode).
    ///
    /// Already-buffered samples from the session that just ended can still be collected via
    /// `drain_into()` until the next `arm()` or `clear_and_arm()`.
    pub fn disarm(&self) {
        self.shared
            .active_session
            .store(DISARMED_SESSION, Ordering::Release);
    }

    /// Start a fresh capture session, discarding any queued or late in-flight samples from earlier
    /// sessions and resetting the drop counter.
    pub fn clear_and_arm(&mut self) {
        self.shared
            .active_session
            .store(DISARMED_SESSION, Ordering::Release);
        self.cons.clear();
        self.current_session = next_session_id(self.current_session);
        self.shared.dropped_samples.store(0, Ordering::Relaxed);
        self.shared
            .active_session
            .store(self.current_session, Ordering::Release);
    }

    /// Returns `true` while incoming callback samples are being pushed into the ring buffer.
    pub fn is_armed(&self) -> bool {
        self.shared.active_session() != DISARMED_SESSION
    }

    /// Number of queued samples currently in the ring buffer for the current session.
    pub fn available(&self) -> usize {
        if self.current_session == DISARMED_SESSION {
            return 0;
        }
        let (first, second) = self.cons.as_slices();
        first
            .iter()
            .chain(second.iter())
            .filter(|s| s.session == self.current_session)
            .count()
    }

    /// Most recent callback RMS amplitude (`0.0..=1.0` for in-range audio).
    pub fn latest_rms(&self) -> f32 {
        self.shared.latest_rms()
    }

    /// Total mono samples dropped due to ring-buffer overflow since creation or last reset.
    pub fn dropped_samples(&self) -> u64 {
        self.shared.dropped_samples.load(Ordering::Relaxed)
    }

    /// Return and reset the overflow drop counter.
    pub fn take_dropped_samples(&self) -> u64 {
        self.shared.dropped_samples.swap(0, Ordering::Relaxed)
    }

    pub(crate) fn shared_rms_handle(&self) -> Arc<AtomicU32Ref> {
        Arc::new(AtomicU32Ref {
            shared: Arc::clone(&self.shared),
        })
    }
}

/// Lightweight read-only handle for reading live RMS from outside the consumer thread.
#[derive(Clone)]
pub(crate) struct AtomicU32Ref {
    shared: Arc<SharedRingState>,
}

impl AtomicU32Ref {
    pub(crate) fn latest_rms(&self) -> f32 {
        self.shared.latest_rms()
    }
}

/// Create a lock-free SPSC audio ring channel carrying mono `f32` samples.
pub fn audio_ring_channel(
    capacity_samples: usize,
    channels: u16,
    initially_armed: bool,
) -> Result<(AudioRingWriter, AudioRingReader), AudioError> {
    if capacity_samples == 0 {
        return Err(AudioError::InvalidParameter(
            "ring buffer capacity must be greater than 0".to_string(),
        ));
    }
    if channels == 0 {
        return Err(AudioError::InvalidParameter(
            "channels must be greater than 0".to_string(),
        ));
    }

    let initial_session = if initially_armed { 1 } else { DISARMED_SESSION };
    let rb = HeapRb::<TaggedSample>::new(capacity_samples);
    let (prod, cons) = rb.split();
    let shared = Arc::new(SharedRingState::new(initial_session));

    let writer = AudioRingWriter {
        prod,
        channels: usize::from(channels),
        shared: Arc::clone(&shared),
    };
    let reader = AudioRingReader {
        cons,
        current_session: initial_session,
        shared,
    };
    Ok((writer, reader))
}

#[cfg(test)]
mod tests {
    use std::thread;

    use super::*;

    #[test]
    fn stereo_push_downmixes_and_updates_rms() {
        let (mut writer, mut reader) = audio_ring_channel(64, 2, true).unwrap();
        let stereo = vec![0.2_f32, 0.6, -0.4, -0.4, 1.0, -1.0];
        writer.push_interleaved_f32(&stereo);

        let mut drained = Vec::new();
        assert_eq!(reader.drain_into(&mut drained), 3);
        assert!((drained[0] - 0.4).abs() < 1e-6);
        assert!((drained[1] + 0.4).abs() < 1e-6);
        assert!(drained[2].abs() < 1e-6);

        let expected_rms = ((0.4_f32 * 0.4 + 0.4 * 0.4) / 3.0).sqrt();
        assert!((reader.latest_rms() - expected_rms).abs() < 1e-5);
        assert_eq!(reader.dropped_samples(), 0);
    }

    #[test]
    fn disarmed_warm_stream_ignores_samples_until_armed() {
        let (mut writer, mut reader) = audio_ring_channel(16, 1, false).unwrap();
        writer.push_interleaved_f32(&[0.5, 0.5, 0.5]);
        assert_eq!(reader.available(), 0);
        assert!((reader.latest_rms() - 0.5).abs() < 1e-5);

        reader.clear_and_arm();
        writer.push_interleaved_f32(&[0.25, -0.25]);
        let mut out = Vec::new();
        assert_eq!(reader.drain_into(&mut out), 2);
        assert_eq!(out, vec![0.25, -0.25]);

        reader.disarm();
        writer.push_interleaved_f32(&[0.9, 0.9]);
        assert_eq!(reader.available(), 0);
    }

    #[test]
    fn in_flight_callback_from_prior_session_cannot_leak_after_clear_and_arm() {
        let (mut writer, mut reader) = audio_ring_channel(16, 1, true).unwrap();
        // Simulate a producer callback that captured session 1 before `clear_and_arm()`.
        let prior_session = writer.shared.active_session();
        assert_eq!(prior_session, 1);

        reader.clear_and_arm();
        assert_eq!(reader.current_session, 2);

        // Late completion of the session-1 callback followed by a new session-2 callback.
        writer.push_for_session(&[0.99, 0.88], prior_session, |s| s);
        writer.push_interleaved_f32(&[0.11, 0.22]);

        assert_eq!(reader.available(), 2);
        let mut out = Vec::new();
        assert_eq!(reader.drain_into(&mut out), 2);
        assert_eq!(out, vec![0.11, 0.22]);
    }

    #[test]
    fn disarm_still_allows_draining_final_tail_of_ending_session() {
        let (mut writer, mut reader) = audio_ring_channel(16, 1, true).unwrap();
        writer.push_interleaved_f32(&[0.1, 0.2]);
        reader.disarm();
        let mut out = Vec::new();
        assert_eq!(reader.drain_into(&mut out), 2);
        assert_eq!(out, vec![0.1, 0.2]);
    }

    #[test]
    fn overflow_counts_dropped_samples_and_converted_i16_scales_correctly() {
        let (mut writer, mut reader) = audio_ring_channel(4, 1, true).unwrap();
        writer.push_interleaved_converted(&[0_i16, i16::MAX, -i16::MAX, 0, 100, 200], |s| {
            f32::from(s) / f32::from(i16::MAX)
        });
        assert_eq!(reader.take_dropped_samples(), 2);
        assert_eq!(reader.dropped_samples(), 0);

        let mut out = Vec::new();
        assert_eq!(reader.drain_into(&mut out), 4);
        assert_eq!(out[0], 0.0);
        assert!((out[1] - 1.0).abs() < 1e-4);
        assert!((out[2] + 1.0).abs() < 1e-4);
        assert_eq!(out[3], 0.0);
    }

    #[test]
    fn concurrent_spsc_transfer_preserves_sample_order_and_accounting() {
        const TOTAL: usize = 65_536;
        let (mut writer, mut reader) = audio_ring_channel(8192, 1, true).unwrap();

        let producer = thread::spawn(move || {
            let mut next = 0_usize;
            while next < TOTAL {
                let end = (next + 128).min(TOTAL);
                let chunk: Vec<f32> = (next..end).map(|i| i as f32).collect();
                writer.push_interleaved_f32(&chunk);
                next = end;
                thread::yield_now();
            }
        });

        let mut collected = Vec::with_capacity(TOTAL);
        while collected.len() + (reader.dropped_samples() as usize) < TOTAL {
            if reader.drain_into(&mut collected) == 0 {
                thread::yield_now();
            }
        }
        producer.join().unwrap();
        reader.drain_into(&mut collected);

        assert_eq!(collected.len() + reader.dropped_samples() as usize, TOTAL);
        assert!(collected.windows(2).all(|w| w[1] > w[0]));
    }
}
