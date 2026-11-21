//! Shared mathematical animation model for PulseScribe recording overlays.
//!
//! Mirrors `ui/animation.py`: two-layer level smoothing, Adaptive Gain Control (AGC),
//! traveling wave + Gaussian envelope for `AppState::Recording`, fixed linear meter
//! for `AppState::Listening`, and zero-allocation `[f64; BAR_COUNT]` frames.

use std::f64::consts::{FRAC_PI_2, TAU};

use crate::state::AppState;

/// Target animation refresh rate for the recording overlay.
pub const FPS: u32 = 60;
pub const FRAME_MS: u32 = 1000 / FPS;

/// Bar geometry constants (pixels).
pub const BAR_COUNT: usize = 10;
pub const BAR_WIDTH: f64 = 3.0;
pub const BAR_GAP: f64 = 3.0;
pub const BAR_MIN_HEIGHT: f64 = 3.0;
pub const BAR_MAX_HEIGHT: f64 = 30.0;

/// Audio thresholds mirrored from `config.py` for the fixed listening meter.
pub const VAD_THRESHOLD: f64 = 0.010;
pub const LISTENING_NOISE_GATE: f64 = 0.0015;

/// Two-layer smoothing coefficients.
pub const SMOOTHING_ALPHA_RISE: f64 = 0.65;
pub const SMOOTHING_ALPHA_FALL: f64 = 0.12;
pub const LEVEL_SMOOTHING_RISE: f64 = 0.30;
pub const LEVEL_SMOOTHING_FALL: f64 = 0.10;

/// Audio-visual AGC shaping constants.
pub const VISUAL_GAIN: f64 = 3.9;
pub const VISUAL_NOISE_GATE: f64 = 0.00075;
pub const VISUAL_EXPONENT: f64 = 1.3;
pub const AGC_DECAY: f64 = 0.97;
pub const AGC_MIN_PEAK: f64 = 0.01;
pub const AGC_HEADROOM: f64 = 2.0;

/// Traveling wave parameters.
pub const WAVE_WANDER_AMOUNT: f64 = 0.22;
pub const WAVE_WANDER_HZ_PRIMARY: f64 = 0.55;
pub const WAVE_WANDER_HZ_SECONDARY: f64 = 0.95;
pub const WAVE_WANDER_PHASE_STEP_PRIMARY: f64 = 0.85;
pub const WAVE_WANDER_PHASE_STEP_SECONDARY: f64 = 1.65;
pub const WAVE_WANDER_BLEND: f64 = 0.65;

/// Gaussian envelope parameters.
pub const ENVELOPE_STRENGTH: f64 = 0.85;
pub const ENVELOPE_BASE: f64 = 0.38;
pub const ENVELOPE_SIGMA: f64 = 1.15;
pub const ENVELOPE_HZ_PRIMARY: f64 = 0.15;
pub const ENVELOPE_HZ_SECONDARY: f64 = 0.24;
pub const ENVELOPE_BLEND: f64 = 0.62;

/// Listening meter span (pixels above rest height) and settle tolerance.
pub const LISTENING_LEVEL_SPAN: f64 = 8.4;
pub const LISTENING_LEVEL_EPSILON: f64 = 0.03;

const CENTER_INDEX: f64 = (BAR_COUNT - 1) as f64 / 2.0;

fn gaussian(distance: f64, sigma: f64) -> f64 {
    if sigma <= 0.0 {
        return 0.0;
    }
    let x = distance / sigma;
    (-0.5 * x * x).exp()
}

/// Symmetric height emphasis factors (center bars taller than edges).
pub fn height_factors() -> [f64; BAR_COUNT] {
    let mut factors = [1.0; BAR_COUNT];
    if BAR_COUNT <= 1 {
        return factors;
    }
    for (i, slot) in factors.iter_mut().enumerate() {
        let emphasis = (((i as f64 - CENTER_INDEX).abs() / CENTER_INDEX) * FRAC_PI_2)
            .cos()
            .powi(2);
        *slot = 0.35 + 0.65 * emphasis;
    }
    factors
}

/// Static low listening row heights at zero level (`round(BAR_MIN_HEIGHT + 5 * f)`).
pub fn listening_bar_rest_heights() -> [f64; BAR_COUNT] {
    let factors = height_factors();
    let mut rest = [BAR_MIN_HEIGHT; BAR_COUNT];
    for i in 0..BAR_COUNT {
        rest[i] = (BAR_MIN_HEIGHT + 5.0 * factors[i]).round_ties_even();
    }
    rest
}

/// Map raw RMS to `0.0..=1.0` for the listening row on a fixed linear scale without AGC.
pub fn listening_level(rms: f64) -> f64 {
    if !rms.is_finite() {
        return 0.0;
    }
    let span = VAD_THRESHOLD - LISTENING_NOISE_GATE;
    if span <= 0.0 {
        return 0.0;
    }
    ((rms - LISTENING_NOISE_GATE) / span).clamp(0.0, 1.0)
}

/// Listening row heights for a `0.0..=1.0` `listening_level` (rest heights at `<= 0.0`).
pub fn listening_bar_heights(level: f64) -> [f64; BAR_COUNT] {
    let rest = listening_bar_rest_heights();
    if !level.is_finite() || level <= 0.0 {
        return rest;
    }
    let factors = height_factors();
    let scale = level.min(1.0) * LISTENING_LEVEL_SPAN;
    let mut heights = [0.0; BAR_COUNT];
    for i in 0..BAR_COUNT {
        heights[i] = rest[i] + scale * factors[i];
    }
    heights
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct FrameCacheKey {
    is_recording: bool,
    t_bits: u64,
    level_bits: u64,
}

fn canonical_bits(value: f64) -> u64 {
    let normalized = if value == 0.0 { 0.0 } else { value };
    normalized.to_bits()
}

/// Encapsulates smoothing, AGC, and cached frame math for the recording overlay.
#[derive(Debug, Clone)]
pub struct AnimationLogic {
    smoothed_level: f64,
    level_smoothed: f64,
    agc_peak: f64,
    normalized_level: f64,
    height_factors: [f64; BAR_COUNT],
    frame_cache: Option<(FrameCacheKey, [f64; BAR_COUNT])>,
}

impl Default for AnimationLogic {
    fn default() -> Self {
        Self::new()
    }
}

impl AnimationLogic {
    pub fn new() -> Self {
        Self {
            smoothed_level: 0.0,
            level_smoothed: 0.0,
            agc_peak: AGC_MIN_PEAK,
            normalized_level: 0.0,
            height_factors: height_factors(),
            frame_cache: None,
        }
    }

    pub fn smoothed_level(&self) -> f64 {
        self.smoothed_level
    }

    pub fn level_smoothed(&self) -> f64 {
        self.level_smoothed
    }

    pub fn normalized_level(&self) -> f64 {
        self.normalized_level
    }

    pub fn agc_peak(&self) -> f64 {
        self.agc_peak
    }

    /// Update the two-layer audio level smoother, clamping out-of-range and non-finite inputs.
    pub fn update_level(&mut self, target_level: f64) {
        let target = if target_level.is_finite() {
            target_level.clamp(0.0, 1.0)
        } else {
            0.0
        };

        if !self.smoothed_level.is_finite() {
            self.smoothed_level = 0.0;
        }
        if !self.level_smoothed.is_finite() {
            self.level_smoothed = 0.0;
        }

        let alpha = if target > self.smoothed_level {
            SMOOTHING_ALPHA_RISE
        } else {
            SMOOTHING_ALPHA_FALL
        };
        self.smoothed_level += alpha * (target - self.smoothed_level);

        let alpha2 = if self.smoothed_level > self.level_smoothed {
            LEVEL_SMOOTHING_RISE
        } else {
            LEVEL_SMOOTHING_FALL
        };
        self.level_smoothed += alpha2 * (self.smoothed_level - self.level_smoothed);
        self.frame_cache = None;
    }

    /// Update Adaptive Gain Control and compute `normalized_level`.
    pub fn update_agc(&mut self) {
        if !self.level_smoothed.is_finite() {
            self.level_smoothed = 0.0;
        }
        if !self.agc_peak.is_finite() {
            self.agc_peak = AGC_MIN_PEAK;
        }

        let gated = (self.level_smoothed - VISUAL_NOISE_GATE).max(0.0);
        if gated > self.agc_peak {
            self.agc_peak = gated;
        } else {
            self.agc_peak = (self.agc_peak * AGC_DECAY).max(AGC_MIN_PEAK);
        }

        let reference_peak = (self.agc_peak * AGC_HEADROOM).max(AGC_MIN_PEAK);
        let normalized = if reference_peak > 0.0 {
            gated / reference_peak
        } else {
            0.0
        };

        let shaped = normalized.min(1.0).powf(VISUAL_EXPONENT) * VISUAL_GAIN;
        self.normalized_level = shaped.min(1.0);
        self.frame_cache = None;
    }

    /// Listening row heights derived from the smoothed level (call `update_level` first).
    pub fn listening_frame_heights(&self) -> [f64; BAR_COUNT] {
        listening_bar_heights(listening_level(self.level_smoothed))
    }

    /// Return `true` when the smoothed listening level has caught up with `rms`.
    pub fn listening_is_settled(&self, rms: f64) -> bool {
        (listening_level(rms) - listening_level(self.level_smoothed)).abs()
            <= LISTENING_LEVEL_EPSILON
    }

    /// Return cached normalized `[0.0..=1.0]` bar values for the given time `t` and `state`.
    pub fn calculate_frame_normalized(&mut self, t: f64, state: AppState) -> [f64; BAR_COUNT] {
        let is_recording = state == AppState::Recording;
        let effective_level = if is_recording {
            self.normalized_level
        } else {
            0.0
        };
        let key = FrameCacheKey {
            is_recording,
            t_bits: canonical_bits(t),
            level_bits: canonical_bits(effective_level),
        };
        if let Some((cached_key, cached_values)) = self.frame_cache {
            if cached_key == key {
                return cached_values;
            }
        }
        let values = if is_recording {
            self.build_recording_frame_values(t)
        } else {
            [0.0; BAR_COUNT]
        };
        self.frame_cache = Some((key, values));
        values
    }

    /// Return concrete pixel bar heights for all bars in one call.
    pub fn calculate_frame_heights(
        &mut self,
        t: f64,
        state: AppState,
        min_height: f64,
        max_height: f64,
    ) -> [f64; BAR_COUNT] {
        let normalized = self.calculate_frame_normalized(t, state);
        let height_range = max_height - min_height;
        if height_range == 0.0 {
            return [min_height; BAR_COUNT];
        }
        let mut heights = [0.0; BAR_COUNT];
        for i in 0..BAR_COUNT {
            heights[i] = min_height + height_range * normalized[i];
        }
        heights
    }

    /// Return the pixel bar height for index `i` using default min/max bar heights.
    pub fn calculate_bar_height(&mut self, i: usize, t: f64, state: AppState) -> f64 {
        if i >= BAR_COUNT {
            return BAR_MIN_HEIGHT;
        }
        self.calculate_frame_heights(t, state, BAR_MIN_HEIGHT, BAR_MAX_HEIGHT)[i]
    }

    /// Return the normalized `0.0..=1.0` height for index `i`.
    pub fn calculate_bar_normalized(&mut self, i: usize, t: f64, state: AppState) -> f64 {
        if i >= BAR_COUNT {
            return 0.0;
        }
        self.calculate_frame_normalized(t, state)[i]
    }

    fn build_recording_frame_values(&self, t: f64) -> [f64; BAR_COUNT] {
        let level = self.normalized_level;
        if !t.is_finite() || level <= 0.0 {
            return [0.0; BAR_COUNT];
        }

        let env_phase1 = TAU * ENVELOPE_HZ_PRIMARY * t;
        let env_phase2 = TAU * ENVELOPE_HZ_SECONDARY * t;
        let env_offset1 = env_phase1.sin() * CENTER_INDEX * 0.8;
        let env_offset2 = env_phase2.sin() * CENTER_INDEX * 0.6;
        let env_center =
            CENTER_INDEX + ENVELOPE_BLEND * env_offset1 + (1.0 - ENVELOPE_BLEND) * env_offset2;

        let mut values = [0.0; BAR_COUNT];
        for (i, slot) in values.iter_mut().enumerate() {
            let idx = i as f64;
            let phase1 = TAU * WAVE_WANDER_HZ_PRIMARY * t + idx * WAVE_WANDER_PHASE_STEP_PRIMARY;
            let phase2 =
                TAU * WAVE_WANDER_HZ_SECONDARY * t + idx * WAVE_WANDER_PHASE_STEP_SECONDARY;
            let wave1 = (phase1.sin() + 1.0) / 2.0;
            let wave2 = (phase2.sin() + 1.0) / 2.0;
            let wave_mod = WAVE_WANDER_BLEND * wave1 + (1.0 - WAVE_WANDER_BLEND) * wave2;
            let wave_factor = 1.0 - WAVE_WANDER_AMOUNT + WAVE_WANDER_AMOUNT * wave_mod;

            let distance = (idx - env_center).abs();
            let env_base =
                ENVELOPE_BASE + (1.0 - ENVELOPE_BASE) * gaussian(distance, ENVELOPE_SIGMA);
            let env_factor = ENVELOPE_STRENGTH * env_base + (1.0 - ENVELOPE_STRENGTH);
            *slot = level * self.height_factors[i] * wave_factor * env_factor;
        }
        values
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn non_finite_and_out_of_range_levels_are_clamped_safely() {
        let mut anim = AnimationLogic::new();
        anim.update_level(f64::NAN);
        anim.update_level(f64::INFINITY);
        anim.update_level(f64::NEG_INFINITY);
        assert_eq!(anim.smoothed_level(), 0.0);
        assert_eq!(anim.level_smoothed(), 0.0);

        anim.update_level(5.0);
        assert!(anim.smoothed_level() > 0.0 && anim.smoothed_level() <= 1.0);
        anim.update_agc();
        assert!(anim.normalized_level() > 0.0 && anim.normalized_level() <= 1.0);
    }

    #[test]
    fn only_recording_produces_animated_frames_and_listening_meter_stays_low() {
        let mut anim = AnimationLogic::new();
        for _ in 0..5 {
            anim.update_level(0.4);
            anim.update_agc();
        }
        let rec = anim.calculate_frame_normalized(1.25, AppState::Recording);
        assert!(rec.iter().any(|&v| v > 0.0));
        for non_rec in [
            AppState::Idle,
            AppState::Loading,
            AppState::Listening,
            AppState::Transcribing,
            AppState::Refining,
            AppState::Done,
            AppState::NoSpeech,
            AppState::Error,
        ] {
            assert_eq!(
                anim.calculate_frame_normalized(1.25, non_rec),
                [0.0; BAR_COUNT]
            );
        }

        assert_eq!(listening_level(f64::NAN), 0.0);
        assert_eq!(listening_level(LISTENING_NOISE_GATE), 0.0);
        assert_eq!(listening_level(VAD_THRESHOLD), 1.0);
        let rest = listening_bar_rest_heights();
        assert_eq!(listening_bar_heights(0.0), rest);
        let loud = listening_bar_heights(1.0);
        assert!(loud[4] > rest[4] && loud[4] < BAR_MAX_HEIGHT);
    }
}
