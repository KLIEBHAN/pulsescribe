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

# Smoothing (tuned to match macOS feel)
SMOOTHING_ALPHA_RISE = 0.65  # Faster attack for responsive feel
SMOOTHING_ALPHA_FALL = 0.12
LEVEL_SMOOTHING_RISE = 0.30
LEVEL_SMOOTHING_FALL = 0.10

# Audio-Visual Mapping
VISUAL_GAIN = 3.0
VISUAL_NOISE_GATE = 0.001
VISUAL_EXPONENT = 1.3  # Slightly more compression

# Adaptive Gain Control (AGC) - macOS tuning
AGC_DECAY = 0.97  # Slower decay for smoother response
AGC_MIN_PEAK = 0.01  # Higher floor prevents over-amplification
AGC_HEADROOM = 2.0  # More headroom for dynamic range

# Traveling Wave (macOS values)
WAVE_WANDER_AMOUNT = 0.22
WAVE_WANDER_HZ_PRIMARY = 0.55
WAVE_WANDER_HZ_SECONDARY = 0.95
WAVE_WANDER_PHASE_STEP_PRIMARY = 0.85
WAVE_WANDER_PHASE_STEP_SECONDARY = 1.65
WAVE_WANDER_BLEND = 0.65

# Gaussian Envelope (macOS values - stronger, more focused)
ENVELOPE_STRENGTH = 0.85
ENVELOPE_BASE = 0.38
ENVELOPE_SIGMA = 1.15  # Tighter focus
ENVELOPE_HZ_PRIMARY = 0.15
ENVELOPE_HZ_SECONDARY = 0.24
ENVELOPE_BLEND = 0.62


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
_BAR_INDEXES = tuple(range(BAR_COUNT))
_CENTER_INDEX = (BAR_COUNT - 1) / 2


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
        self._frame_cache_key: tuple[str, float, float] | None = None
        self._frame_cache_values: tuple[float, ...] | None = None

    def _invalidate_frame_cache(self) -> None:
        self._frame_cache_key = None
        self._frame_cache_values = None

    def update_level(self, target_level: float):
        """Updates and smoothes the audio level."""
        # Defensiv gegen fehlerhafte Audio-Callbacks (NaN/Inf/None/out-of-range),
        # damit die Overlay-Animation nicht in einen invaliden Zustand kippt.
        if not isinstance(target_level, (int, float)):
            target_level = 0.0
        elif not math.isfinite(target_level):
            target_level = 0.0
        else:
            target_level = max(0.0, min(1.0, float(target_level)))

        if not math.isfinite(self._smoothed_level):
            self._smoothed_level = 0.0
        if not math.isfinite(self._level_smoothed):
            self._level_smoothed = 0.0

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
        self._invalidate_frame_cache()

    def update_agc(self):
        """Updates Adaptive Gain Control logic."""
        if not math.isfinite(self._level_smoothed):
            self._level_smoothed = 0.0
        if not math.isfinite(self._agc_peak):
            self._agc_peak = AGC_MIN_PEAK

        gated = max(self._level_smoothed - VISUAL_NOISE_GATE, 0.0)

        if gated > self._agc_peak:
            self._agc_peak = gated
        else:
            self._agc_peak = max(self._agc_peak * AGC_DECAY, AGC_MIN_PEAK)

        reference_peak = max(self._agc_peak * AGC_HEADROOM, AGC_MIN_PEAK)
        normalized = gated / reference_peak if reference_peak > 0 else 0.0

        shaped = (min(1.0, normalized) ** VISUAL_EXPONENT) * VISUAL_GAIN
        self._normalized_level = min(1.0, shaped)
        self._invalidate_frame_cache()

    def _frame_values(self, t: float, state: str) -> tuple[float, ...]:
        """Return cached normalized frame values for all bars."""
        cache_key = (state, t, self._normalized_level if state == "RECORDING" else 0.0)
        if self._frame_cache_key != cache_key or self._frame_cache_values is None:
            self._frame_cache_key = cache_key
            self._frame_cache_values = self._build_frame_values(t, state)
        return self._frame_cache_values

    def calculate_frame_normalized(self, t: float, state: str) -> tuple[float, ...]:
        """Return normalized frame values for all bars in one call."""
        return self._frame_values(t, state)

    def calculate_frame_heights(
        self,
        t: float,
        state: str,
        *,
        min_height: float = BAR_MIN_HEIGHT,
        max_height: float = BAR_MAX_HEIGHT,
    ) -> tuple[float, ...]:
        """Return concrete bar heights for the full frame.

        Callers on hot render paths can consume one cached vector instead of
        repeatedly calling ``calculate_bar_height()`` per bar.
        """
        normalized_values = self._frame_values(t, state)
        height_range = max_height - min_height
        if height_range == 0:
            return (float(min_height),) * BAR_COUNT
        return tuple(min_height + height_range * value for value in normalized_values)

    def calculate_bar_height(self, i: int, t: float, state: str) -> float:
        """Calculates the target height for a specific bar index `i` at time `t`.

        Uses local constants BAR_MIN_HEIGHT and BAR_MAX_HEIGHT.
        For custom min/max, use calculate_bar_normalized() instead.
        """
        if not 0 <= i < BAR_COUNT:
            return BAR_MIN_HEIGHT
        return self.calculate_frame_heights(t, state)[i]

    def calculate_bar_normalized(self, i: int, t: float, state: str) -> float:
        """Returns normalized height value (0.0-1.0) for a bar.

        This allows each platform to apply its own MIN/MAX heights:
            height = min_height + (max_height - min_height) * normalized
        """
        if not 0 <= i < BAR_COUNT:
            return 0.0

        return self._frame_values(t, state)[i]

    def _build_frame_values(self, t: float, state: str) -> tuple[float, ...]:
        if state == "RECORDING":
            return self._build_recording_frame_values(t)
        if state == "LISTENING":
            return tuple(self._calc_listening_normalized(i, t) for i in _BAR_INDEXES)
        if state in ("TRANSCRIBING", "REFINING"):
            value = self._calc_processing_normalized(t)
            return (value,) * BAR_COUNT
        if state == "LOADING":
            return tuple(self._calc_loading_normalized(i, t) for i in _BAR_INDEXES)
        if state in ("DONE", "NO_SPEECH"):
            return tuple(self._calc_done_normalized(i, t) for i in _BAR_INDEXES)
        if state == "ERROR":
            value = self._calc_error_normalized(t)
            return (value,) * BAR_COUNT
        return (0.0,) * BAR_COUNT

    def _build_recording_frame_values(self, t: float) -> tuple[float, ...]:
        """Precompute shared recording-frame math once per frame."""
        level = self._normalized_level
        if level <= 0.0:
            return (0.0,) * BAR_COUNT

        env_phase1 = TAU * ENVELOPE_HZ_PRIMARY * t
        env_phase2 = TAU * ENVELOPE_HZ_SECONDARY * t
        env_offset1 = math.sin(env_phase1) * _CENTER_INDEX * 0.8
        env_offset2 = math.sin(env_phase2) * _CENTER_INDEX * 0.6
        env_center = (
            _CENTER_INDEX
            + ENVELOPE_BLEND * env_offset1
            + (1 - ENVELOPE_BLEND) * env_offset2
        )

        values: list[float] = []
        for i in _BAR_INDEXES:
            phase1 = TAU * WAVE_WANDER_HZ_PRIMARY * t + i * WAVE_WANDER_PHASE_STEP_PRIMARY
            phase2 = (
                TAU * WAVE_WANDER_HZ_SECONDARY * t
                + i * WAVE_WANDER_PHASE_STEP_SECONDARY
            )
            wave1 = (math.sin(phase1) + 1) / 2
            wave2 = (math.sin(phase2) + 1) / 2
            wave_mod = WAVE_WANDER_BLEND * wave1 + (1 - WAVE_WANDER_BLEND) * wave2
            wave_factor = 1.0 - WAVE_WANDER_AMOUNT + WAVE_WANDER_AMOUNT * wave_mod

            distance = abs(i - env_center)
            env_factor = ENVELOPE_BASE + (1 - ENVELOPE_BASE) * _gaussian(
                distance, ENVELOPE_SIGMA
            )
            env_factor = ENVELOPE_STRENGTH * env_factor + (1 - ENVELOPE_STRENGTH) * 1.0
            values.append(level * _HEIGHT_FACTORS[i] * wave_factor * env_factor)

        return tuple(values)

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

    def _calc_processing_normalized(self, t: float) -> float:
        """Transcribing/Refining: Synchronized pulsing (original macOS style). Returns 0-1."""
        # All bars pulse together to same height with ~1s period
        phase = t * math.pi  # ~1s full cycle (up and down)
        pulse = (math.sin(phase) + 1) / 2  # 0..1

        # All bars same height (no height factors)
        return 0.7 * pulse

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
