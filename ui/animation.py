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

# Animation Timing
FPS = 60
FRAME_MS = 1000 // FPS  # ~16ms

# Bar Configuration
BAR_COUNT = 10
BAR_WIDTH = 4
BAR_GAP = 5
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
        """Calculates the target height for a specific bar index `i` at time `t`.

        Uses local constants BAR_MIN_HEIGHT and BAR_MAX_HEIGHT.
        For custom min/max, use calculate_bar_normalized() instead.
        """
        normalized = self.calculate_bar_normalized(i, t, state)
        return BAR_MIN_HEIGHT + (BAR_MAX_HEIGHT - BAR_MIN_HEIGHT) * normalized

    def calculate_bar_normalized(self, i: int, t: float, state: str) -> float:
        """Returns normalized height value (0.0-1.0) for a bar.

        This allows each platform to apply its own MIN/MAX heights:
            height = min_height + (max_height - min_height) * normalized
        """
        if state == "RECORDING":
            return self._calc_recording_normalized(i, t)
        elif state == "LISTENING":
            return self._calc_listening_normalized(i, t)
        elif state in ("TRANSCRIBING", "REFINING"):
            return self._calc_processing_normalized(i, t)
        elif state == "LOADING":
            return self._calc_loading_normalized(i, t)
        elif state == "DONE":
            return self._calc_done_normalized(i, t)
        elif state == "ERROR":
            return self._calc_error_normalized(t)

        return 0.0

    def _calc_recording_normalized(self, i: int, t: float) -> float:
        """Recording: Audio-responsive with Traveling Wave and Envelope. Returns 0-1."""
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
        return level * base_factor * wave_factor * env_factor

    def _calc_listening_normalized(self, i: int, t: float) -> float:
        """Listening: Dual sine waves for organic waiting animation. Returns 0-1."""
        # Primary wave (faster, provides the main rhythm)
        phase1 = t * 3.0 + i * 0.5
        # Secondary wave (slower, opposite direction, adds complexity)
        phase2 = t * 1.8 - i * 0.3

        # Combine sine waves: 70% primary, 30% secondary
        mixed = math.sin(phase1) * 0.7 + math.sin(phase2) * 0.3

        # Normalize to 0..1
        wave = (mixed + 1) / 2

        # Apply height factors (center higher), scale to ~40% of range
        return 0.4 * wave * _HEIGHT_FACTORS[i]

    def _calc_processing_normalized(self, i: int, t: float) -> float:
        """Transcribing/Refining: Synchronized pulsing (original macOS style). Returns 0-1."""
        # All bars pulse together with ~1s period
        phase = t * math.pi  # ~1s full cycle (up and down)
        pulse = (math.sin(phase) + 1) / 2  # 0..1

        # Apply height factors (center bars higher)
        return 0.7 * pulse * _HEIGHT_FACTORS[i]

    def _calc_loading_normalized(self, i: int, t: float) -> float:
        """Loading: Slow synchronous pulse. Returns 0-1."""
        phase = t * 0.8
        pulse = (math.sin(phase * math.pi) + 1) / 2
        return 0.5 * pulse * _HEIGHT_FACTORS[i]

    def _calc_done_normalized(self, i: int, t: float) -> float:
        """Done: Multi-phase bounce animation. Returns 0-1.

        Phase 1 (0-0.3s): Rise to max
        Phase 2 (0.3-0.5s): Bouncy oscillation
        Phase 3 (0.5s+): Settle at 70%
        """
        if t < 0.3:
            progress = t / 0.3
            return progress * _HEIGHT_FACTORS[i]
        elif t < 0.5:
            progress = (t - 0.3) / 0.2
            bounce = 1 - abs(math.sin(progress * math.pi * 2)) * 0.3
            return bounce * _HEIGHT_FACTORS[i]
        else:
            return 0.7 * _HEIGHT_FACTORS[i]

    def _calc_error_normalized(self, t: float) -> float:
        """Error: Flash animation. Returns 0-1."""
        flash = (math.sin(t * 8) + 1) / 2
        return 0.5 * flash

    @staticmethod
    def get_height_factors() -> list[float]:
        """Returns the pre-computed height factors for bar positioning."""
        return _HEIGHT_FACTORS.copy()


# =============================================================================
# Exports
# =============================================================================

__all__ = [
    "AnimationLogic",
    "BAR_COUNT",
    "BAR_WIDTH",
    "BAR_GAP",
    "BAR_MIN_HEIGHT",
    "BAR_MAX_HEIGHT",
    "FPS",
    "FRAME_MS",
]
