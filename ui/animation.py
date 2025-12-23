"""Shared Animation Logic for PulseScribe Overlays.

Centralizes the mathematical logic for calculating bar heights, AGC (Adaptive Gain Control),
and various animation states (Traveling Wave, Gaussian Envelope, etc.).

This ensures consistent visual behavior across different UI backends (PySide6, Tkinter, Cocoa).
"""

import math

# =============================================================================
# Constants
# =============================================================================

TAU = math.tau  # 2π

# Bar Configuration
BAR_COUNT = 10
BAR_MIN_HEIGHT = 6
BAR_MAX_HEIGHT = 42

# Smoothing
SMOOTHING_ALPHA_RISE = 0.55
SMOOTHING_ALPHA_FALL = 0.12
LEVEL_SMOOTHING_RISE = 0.30
LEVEL_SMOOTHING_FALL = 0.10

# Audio-Visual Mapping
VISUAL_GAIN = 3.0
VISUAL_NOISE_GATE = 0.001
VISUAL_EXPONENT = 1.2

# Adaptive Gain Control (AGC)
AGC_DECAY = 0.9923
AGC_MIN_PEAK = 0.005
AGC_HEADROOM = 1.5

# Traveling Wave
WAVE_WANDER_AMOUNT = 0.25
WAVE_WANDER_HZ_PRIMARY = 0.5
WAVE_WANDER_HZ_SECONDARY = 0.85
WAVE_WANDER_PHASE_STEP_PRIMARY = 0.8
WAVE_WANDER_PHASE_STEP_SECONDARY = 1.5
WAVE_WANDER_BLEND = 0.6

# Gaussian Envelope
ENVELOPE_STRENGTH = 0.75
ENVELOPE_BASE = 0.4
ENVELOPE_SIGMA = 1.3
ENVELOPE_HZ_PRIMARY = 0.18
ENVELOPE_HZ_SECONDARY = 0.28
ENVELOPE_BLEND = 0.55


# =============================================================================
# Helper Functions
# =============================================================================


def _gaussian(distance: float, sigma: float) -> float:
    """Gaussian function for envelope calculation."""
    if sigma <= 0:
        return 0.0
    x = distance / sigma
    return math.exp(-0.5 * x * x)


def _build_height_factors() -> list[float]:
    """Pre-computes symmetric height factors (center higher than edges)."""
    if BAR_COUNT <= 1:
        return [1.0]

    center = (BAR_COUNT - 1) / 2
    factors = []
    for i in range(BAR_COUNT):
        emphasis = math.cos((abs(i - center) / center) * (math.pi / 2)) ** 2
        factors.append(0.35 + 0.65 * emphasis)
    return factors


_HEIGHT_FACTORS = _build_height_factors()


# =============================================================================
# Animation Logic Class
# =============================================================================


class AnimationLogic:
    """Encapsulates state and logic for overlay animations."""

    def __init__(self):
        self._smoothed_level = 0.0
        self._level_smoothed = 0.0  # Second smoothing layer
        self._agc_peak = AGC_MIN_PEAK
        self._normalized_level = 0.0

    def update_level(self, target_level: float):
        """Updates and smoothes the audio level."""
        # First layer smoothing
        alpha = (
            SMOOTHING_ALPHA_RISE
            if target_level > self._smoothed_level
            else SMOOTHING_ALPHA_FALL
        )
        self._smoothed_level += alpha * (target_level - self._smoothed_level)

        # Second layer smoothing
        alpha2 = (
            LEVEL_SMOOTHING_RISE
            if self._smoothed_level > self._level_smoothed
            else LEVEL_SMOOTHING_FALL
        )
        self._level_smoothed += alpha2 * (self._smoothed_level - self._level_smoothed)

    def update_agc(self):
        """Updates Adaptive Gain Control logic."""
        gated = max(self._level_smoothed - VISUAL_NOISE_GATE, 0.0)

        if gated > self._agc_peak:
            self._agc_peak = gated
        else:
            self._agc_peak = max(self._agc_peak * AGC_DECAY, AGC_MIN_PEAK)

        reference_peak = max(self._agc_peak * AGC_HEADROOM, AGC_MIN_PEAK)
        normalized = gated / reference_peak if reference_peak > 0 else 0.0

        shaped = (min(1.0, normalized) ** VISUAL_EXPONENT) * VISUAL_GAIN
        self._normalized_level = min(1.0, shaped)

    def calculate_bar_height(self, i: int, t: float, state: str) -> float:
        """Calculates the target height for a specific bar index `i` at time `t`."""
        if state == "RECORDING":
            return self._calc_recording_height(i, t)
        elif state == "LISTENING":
            return self._calc_listening_height(i, t)
        elif state in ("TRANSCRIBING", "REFINING"):
            return self._calc_processing_height(i, t)
        elif state == "LOADING":
            return self._calc_loading_height(i, t)
        elif state == "DONE":
            return self._calc_done_height(i, t)
        elif state == "ERROR":
            return self._calc_error_height(t)

        return BAR_MIN_HEIGHT

    def _calc_recording_height(self, i: int, t: float) -> float:
        """Recording: Audio-responsive with Traveling Wave and Envelope."""
        center = (BAR_COUNT - 1) / 2
        level = self._normalized_level

        # Traveling Wave
        phase1 = TAU * WAVE_WANDER_HZ_PRIMARY * t + i * WAVE_WANDER_PHASE_STEP_PRIMARY
        phase2 = (
            TAU * WAVE_WANDER_HZ_SECONDARY * t + i * WAVE_WANDER_PHASE_STEP_SECONDARY
        )
        wave1 = (math.sin(phase1) + 1) / 2
        wave2 = (math.sin(phase2) + 1) / 2
        wave_mod = WAVE_WANDER_BLEND * wave1 + (1 - WAVE_WANDER_BLEND) * wave2
        wave_factor = 1.0 - WAVE_WANDER_AMOUNT + WAVE_WANDER_AMOUNT * wave_mod

        # Gaussian Envelope
        env_phase1 = TAU * ENVELOPE_HZ_PRIMARY * t
        env_phase2 = TAU * ENVELOPE_HZ_SECONDARY * t
        env_offset1 = math.sin(env_phase1) * center * 0.8
        env_offset2 = math.sin(env_phase2) * center * 0.6
        env_center = (
            center + ENVELOPE_BLEND * env_offset1 + (1 - ENVELOPE_BLEND) * env_offset2
        )

        distance = abs(i - env_center)
        env_factor = ENVELOPE_BASE + (1 - ENVELOPE_BASE) * _gaussian(
            distance, ENVELOPE_SIGMA
        )
        env_factor = ENVELOPE_STRENGTH * env_factor + (1 - ENVELOPE_STRENGTH) * 1.0

        base_factor = _HEIGHT_FACTORS[i]
        combined = level * base_factor * wave_factor * env_factor

        return BAR_MIN_HEIGHT + (BAR_MAX_HEIGHT - BAR_MIN_HEIGHT) * combined

    def _calc_listening_height(self, i: int, t: float) -> float:
        """Listening: Slow breathing."""
        phase = t * 0.4 + i * 0.25
        breath = (math.sin(phase) + 1) / 2
        return BAR_MIN_HEIGHT + 12 * breath * _HEIGHT_FACTORS[i]

    def _calc_processing_height(self, i: int, t: float) -> float:
        """Transcribing/Refining: Wandering pulse."""
        pulse_pos = (t * 1.5) % (BAR_COUNT + 2) - 1
        distance = abs(i - pulse_pos)
        intensity = max(0, 1 - distance / 2)
        return BAR_MIN_HEIGHT + (BAR_MAX_HEIGHT * 0.6) * intensity

    def _calc_loading_height(self, i: int, t: float) -> float:
        """Loading: Slow synchronous pulse."""
        phase = t * 0.8
        pulse = (math.sin(phase * math.pi) + 1) / 2
        return BAR_MIN_HEIGHT + (BAR_MAX_HEIGHT * 0.5) * pulse * _HEIGHT_FACTORS[i]

    def _calc_done_height(self, i: int, t: float) -> float:
        """Done: Bounce animation."""
        if t < 0.3:
            progress = t / 0.3
            return (
                BAR_MIN_HEIGHT
                + (BAR_MAX_HEIGHT - BAR_MIN_HEIGHT) * progress * _HEIGHT_FACTORS[i]
            )
        elif t < 0.5:
            progress = (t - 0.3) / 0.2
            bounce = 1 - abs(math.sin(progress * math.pi * 2)) * 0.3
            return BAR_MAX_HEIGHT * bounce * _HEIGHT_FACTORS[i]
        else:
            return BAR_MAX_HEIGHT * 0.7 * _HEIGHT_FACTORS[i]

    def _calc_error_height(self, t: float) -> float:
        """Error: Flash animation."""
        flash = (math.sin(t * 8) + 1) / 2
        return BAR_MIN_HEIGHT + (BAR_MAX_HEIGHT - BAR_MIN_HEIGHT) * flash * 0.5
