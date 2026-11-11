"""Overlay-Controller und Visualisierung für pulsescribe."""

import math
import random
import time
import weakref

from config import VISUAL_GAIN, VISUAL_NOISE_GATE
from ui.animation import (
    BAR_COUNT,
    BAR_GAP,
    BAR_MAX_HEIGHT,
    BAR_MIN_HEIGHT,
    BAR_WIDTH,
    LISTENING_BAR_HEIGHTS,
    LISTENING_LEVEL_EPSILON,
    listening_bar_heights,
    listening_level,
)
from ui.overlay_feedback import (
    PASTED_BADGE,
    RECORDING_CLOCK_STATES,
    OverlayContent,
    describe_overlay_content,
)
from ui.overlay_style import (
    BACKGROUND_COLOR,
    BADGE_CORNER_RADIUS,
    BADGE_FONT_SIZE,
    BADGE_PADDING_H,
    BADGE_PADDING_V,
    BADGE_TONES,
    BAR_AREA_WIDTH,
    BAR_COLORS,
    BORDER_COLOR,
    DISMISS_FONT_SIZE,
    DISMISS_GLYPH,
    INTERIM_TEXT_COLOR,
    LINE_HEIGHT,
    LINK_HIT_SLOP,
    LIVE_TEXT_HEIGHT,
    LIVE_TEXT_LINE_PITCH,
    LIVE_TEXT_LINES,
    LIVE_TEXT_WIDTH,
    LINK_TEXT_COLOR,
    PHASE_STEP_COLORS,
    PHASE_STEP_GAP,
    PHASE_STEP_WIDTH,
    META_FONT_SIZE,
    META_TEXT_COLOR,
    MUTED_TEXT_COLOR,
    OVERLAY_CORNER_RADIUS,
    OVERLAY_HEIGHT,
    OVERLAY_MAX_WIDTH_RATIO,
    OVERLAY_WIDTH,
    PHASE_ACTIVE_STEP,
    SHIMMER_ACTIVE_STEP_COLOR,
    SHIMMER_BAND_WIDTH,
    SHIMMER_HIGHLIGHT_COLOR,
    SHIMMER_PERIOD_SECONDS,
    TEXT_COLOR,
    TEXT_FONT_SIZE,
    compute_overlay_layout,
    hex_to_rgb,
    shimmer_track,
)
from ui.overlay_text import (
    DEFAULT_RECORDING_INTERIM_MAX_CHARS,
    InterimLines,
    format_recording_duration,
    format_recording_interim_text as _format_recording_interim_text,  # noqa: F401
    wrap_interim_text,
)
from utils.state import AppState

# =============================================================================
# Overlay-Fenster Konfiguration (Maße/Farben: ui/overlay_style.py)
# =============================================================================
OVERLAY_MARGIN_BOTTOM = 110  # Abstand vom unteren Rand (über Dock)
OVERLAY_ALPHA = 1.0  # Solider Hintergrund, keine Transparenz
OVERLAY_WINDOW_LEVEL = 25  # Über allen Fenstern, kCGFloatingWindowLevel
OVERLAY_INTERIM_MAX_CHARS = DEFAULT_RECORDING_INTERIM_MAX_CHARS

# =============================================================================
# Schallwellen-Visualisierung
# =============================================================================
WAVE_BAR_COUNT = BAR_COUNT
WAVE_BAR_WIDTH = BAR_WIDTH
WAVE_BAR_GAP = BAR_GAP
WAVE_BAR_MIN_HEIGHT = BAR_MIN_HEIGHT
WAVE_BAR_MAX_HEIGHT = BAR_MAX_HEIGHT
WAVE_AREA_WIDTH = BAR_AREA_WIDTH

# Glättung für direkte Level-Updates:
# Schneller Anstieg (spricht direkter auf leise Sprache an),
# langsameres Abklingen (wirkt ruhiger).
WAVE_SMOOTHING_ALPHA_RISE = 0.65
WAVE_SMOOTHING_ALPHA_FALL = 0.12

# Nichtlineare Kurve für Audio->Visual Mapping.
# Mit AGC wirkt eine leicht komprimierende Kurve ruhiger.
WAVE_VISUAL_EXPONENT = 1.3

# Zusätzliche Glättung auf dem (normalisierten) Level selbst.
# Das reduziert "Zittern" durch RMS-Fluktuationen zwischen Frames.
WAVE_LEVEL_SMOOTHING_RISE = 0.30
WAVE_LEVEL_SMOOTHING_FALL = 0.10
WAVE_LEVEL_SETTLED_EPSILON = 0.001

# Adaptive Gain Control (AGC):
# Hält die Visualisierung bei leisen/lauteren Mics dynamisch.
# Wir tracken einen rollenden Peak und normalisieren mit etwas Headroom.
WAVE_AGC_DECAY = 0.97  # Peak-Falloff pro Level-Update (~1s Zeitkonstante bei ~15Hz)
WAVE_AGC_MIN_PEAK = 0.01  # Untergrenze gegen Überverstärkung von Stille
WAVE_AGC_HEADROOM = 2.0  # Mehr Headroom = weniger Sensitivität/Clipping

# Rendering: Recording animiert audiogetrieben; der Timer läuft nur, solange
# sich Balken bewegen. Der Shimmer der Verarbeitungslinie läuft per Core
# Animation ohne Timer (siehe SoundWaveView._start_shimmer).
WAVE_ANIMATION_FPS = 60.0
WAVE_ANIMATION_FPS_IDLE = 24.0
WAVE_LEVEL_ACTIVE_THRESHOLD = 0.035
WAVE_LEVEL_IDLE_THRESHOLD = 0.02

# Sanfte, deterministische "Wander"-Modulation (traveling wave) statt Random-Jitter.
WAVE_WANDER_AMOUNT = 0.22  # Relative Modulation (0..~0.35 sinnvoll)
WAVE_WANDER_HZ_PRIMARY = 0.55  # Hz (langsam, organisch)
WAVE_WANDER_HZ_SECONDARY = 0.95  # Hz (leicht schneller, bricht Monotonie)
WAVE_WANDER_PHASE_STEP_PRIMARY = 0.85  # rad/bar (spatial frequency)
WAVE_WANDER_PHASE_STEP_SECONDARY = 1.65  # rad/bar (spatial frequency)
WAVE_WANDER_BLEND = 0.65  # 0..1 mix primary/secondary

# Zusätzlich: "Energie"-Envelope, die als Paket nach links/rechts wandert.
# Ohne das sind die höchsten Balken oft immer in der Mitte (statisch wirkend).
WAVE_ENVELOPE_STRENGTH = 0.85  # 0..1 (1 = nur Envelope, 0 = nur Basisfaktoren)
WAVE_ENVELOPE_BASE = 0.38  # Mindest-Faktor (verhindert zu flache Ränder)
WAVE_ENVELOPE_SIGMA = 1.15  # Breite des Pakets in Balken (kleiner = stärkerer Fokus)
WAVE_ENVELOPE_RANGE_FRACTION = 1.25  # Max. Verschiebung relativ zur halben Balkenanzahl
WAVE_ENVELOPE_HZ_PRIMARY = 0.15  # Drift (öfter links/rechts)
WAVE_ENVELOPE_HZ_SECONDARY = 0.24  # Zweite Frequenz für weniger Periodizität
WAVE_ENVELOPE_BLEND = 0.62  # 0..1 mix primary/secondary

# Recording-Helfer für den Hotpath: spart wiederholte Subtraktionen/Divisionen.
WAVE_WANDER_BLEND_COMPLEMENT = 1.0 - WAVE_WANDER_BLEND
WAVE_ENVELOPE_BLEND_COMPLEMENT = 1.0 - WAVE_ENVELOPE_BLEND
WAVE_ENVELOPE_STRENGTH_COMPLEMENT = 1.0 - WAVE_ENVELOPE_STRENGTH
WAVE_ENVELOPE_FACTOR_SPAN = 1.0 - WAVE_ENVELOPE_BASE
WAVE_RECORDING_SHIFT_LEVEL_BASE = 0.15
WAVE_RECORDING_SHIFT_LEVEL_SPAN = 1.0 - WAVE_RECORDING_SHIFT_LEVEL_BASE
WAVE_RECORDING_WANDER_LEVEL_BASE = 0.20
WAVE_RECORDING_WANDER_LEVEL_SPAN = 1.0 - WAVE_RECORDING_WANDER_LEVEL_BASE
WAVE_ENVELOPE_GAUSSIAN_SCALE = (
    -0.5 / (WAVE_ENVELOPE_SIGMA * WAVE_ENVELOPE_SIGMA)
    if WAVE_ENVELOPE_SIGMA > 0
    else None
)

CLOCK_INTERVAL_SECONDS = 1.0  # Aufnahmedauer m:ss, nur LISTENING/RECORDING
CLOCK_TOLERANCE_SECONDS = 0.1  # erlaubt macOS, Wakeups zu bündeln
WAVE_HEIGHT_UPDATE_EPSILON = 0.25  # Spare Layer-Updates für subpixel-kleine Änderungen

# =============================================================================
# Helpers
# =============================================================================


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _clamp01(value: float) -> float:
    return _clamp(value, 0.0, 1.0)


def _fps_to_interval_seconds(fps: float) -> float:
    return 1.0 / max(float(fps), 1.0)


def _wave_heights_are_settled(heights: list[float]) -> bool:
    """Return True when all bars are already visually at rest."""
    return all(
        abs(height - WAVE_BAR_MIN_HEIGHT) < WAVE_HEIGHT_UPDATE_EPSILON
        for height in heights
    )


def _heights_match(heights, goals) -> bool:
    """Return True when every listening bar is close enough to settle.

    Twice the commit epsilon: a final step of up to one epsilon may be dropped
    by ``_apply_bar_heights`` after float rounding (see _render_listening_frame).
    """
    return all(
        abs(height - goal) < 2 * WAVE_HEIGHT_UPDATE_EPSILON
        for height, goal in zip(heights, goals)
    )


def _build_height_factors() -> list[float]:
    """Erzeugt symmetrische Höhenfaktoren für alle Balken."""
    if WAVE_BAR_COUNT <= 1:
        return [1.0]

    center = (WAVE_BAR_COUNT - 1) / 2
    factors: list[float] = []
    for i in range(WAVE_BAR_COUNT):
        # Cosine-Falloff sorgt für weich ansteigende Mitte
        emphasis = math.cos((abs(i - center) / center) * (math.pi / 2)) ** 2
        factors.append(0.35 + 0.65 * emphasis)
    return factors


def _ns_color(hex_color: str):
    """Erstellt NSColor aus einem ``#rrggbb``-Wert."""
    from AppKit import NSColor  # type: ignore[import-not-found]

    r, g, b = hex_to_rgb(hex_color)
    return NSColor.colorWithSRGBRed_green_blue_alpha_(r / 255.0, g / 255.0, b / 255.0, 1.0)


def _reduce_motion() -> bool:
    """True when the user turned on "Reduce motion" (Accessibility > Display)."""
    try:
        from AppKit import NSWorkspace  # type: ignore[import-not-found]

        return bool(NSWorkspace.sharedWorkspace().accessibilityDisplayShouldReduceMotion())
    except Exception:
        return False


def _disabled_layer_actions():
    """Return a begun CATransaction without implicit layer animations."""
    from Quartz import CATransaction  # type: ignore[import-not-found]

    CATransaction.begin()
    CATransaction.setDisableActions_(True)
    return CATransaction


class SoundWaveView:
    """
    Balken-Visualisierung des Overlays.

    Modi (siehe ``OverlayContent.bars``):
    - level: audiogetriebene Wellenanimation (einziger animierter Modus)
    - low: niedrige Balken (Listening), folgen als kleiner Pegel dem Mikrofon
    - line/phase1/phase2: 3-px-Linie (Preparing/Transcribing/Refining) mit
      Shimmer per Core Animation (kein Python pro Frame)
    - none: ausgeblendet (Ergebnis-Badges)
    """

    # Class defaults keep stop_animating safe on partially built views.
    _shimmer_track = None
    _shimmer_band = None
    current_animation = None

    def __init__(self, frame):
        from AppKit import NSView  # type: ignore[import-not-found]
        from Quartz import CAGradientLayer, CALayer  # type: ignore[import-not-found]

        self._view = NSView.alloc().initWithFrame_(frame)
        self._view.setWantsLayer_(True)
        self.bars = []
        self.current_animation = None
        self._bar_colors = {mode: _ns_color(color) for mode, color in BAR_COLORS.items()}

        self._height_factors = _build_height_factors()
        self._last_heights = [float(WAVE_BAR_MIN_HEIGHT) for _ in range(WAVE_BAR_COUNT)]
        self._bar_positions = [
            i * (WAVE_BAR_WIDTH + WAVE_BAR_GAP) + WAVE_BAR_WIDTH / 2
            for i in range(WAVE_BAR_COUNT)
        ]
        self._center_y = frame.size.height / 2
        self._height_range = WAVE_BAR_MAX_HEIGHT - WAVE_BAR_MIN_HEIGHT
        self._last_center_y = self._center_y

        self._bar_center = (WAVE_BAR_COUNT - 1) / 2
        self._envelope_max_shift_base = max(
            0.0, self._bar_center * WAVE_ENVELOPE_RANGE_FRACTION
        )

        self._agc_peak = WAVE_AGC_MIN_PEAK
        self._target_level = 0.0
        self._smoothed_level = 0.0
        self._level_timer = None
        self._level_timer_interval_seconds = None
        self._level_timer_activity_mode = "idle"
        self._envelope_phase_primary = random.uniform(0.0, 2 * math.pi)
        self._envelope_phase_secondary = random.uniform(0.0, 2 * math.pi)

        # Traveling-wave Offsets: korrelierte Phasen pro Bar → "wandern" statt "zappeln"
        self._wander_offset_primary = [
            (i - self._bar_center) * WAVE_WANDER_PHASE_STEP_PRIMARY
            + random.uniform(-0.10, 0.10)
            for i in range(WAVE_BAR_COUNT)
        ]
        self._wander_offset_secondary = [
            (i - self._bar_center) * WAVE_WANDER_PHASE_STEP_SECONDARY
            + random.uniform(-0.10, 0.10)
            for i in range(WAVE_BAR_COUNT)
        ]
        self._wander_offset_primary_sin = tuple(
            math.sin(offset) for offset in self._wander_offset_primary
        )
        self._wander_offset_primary_cos = tuple(
            math.cos(offset) for offset in self._wander_offset_primary
        )
        self._wander_offset_secondary_sin = tuple(
            math.sin(offset) for offset in self._wander_offset_secondary
        )
        self._wander_offset_secondary_cos = tuple(
            math.cos(offset) for offset in self._wander_offset_secondary
        )
        self._recording_frame_heights = [
            float(WAVE_BAR_MIN_HEIGHT) for _ in range(WAVE_BAR_COUNT)
        ]
        self._envelope_gaussian_scale = WAVE_ENVELOPE_GAUSSIAN_SCALE
        self._rect_factory = None

        center_y = self._center_y
        for i in range(WAVE_BAR_COUNT):
            x = i * (WAVE_BAR_WIDTH + WAVE_BAR_GAP)
            bar = CALayer.alloc().init()
            bar.setCornerRadius_(WAVE_BAR_WIDTH / 2)
            bar.setFrame_(
                (
                    (x, center_y - WAVE_BAR_MIN_HEIGHT / 2),
                    (WAVE_BAR_WIDTH, WAVE_BAR_MIN_HEIGHT),
                )
            )
            self._view.layer().addSublayer_(bar)
            self.bars.append(bar)

        self._line = CALayer.alloc().init()
        self._line.setBackgroundColor_(self._bar_colors["line"].CGColor())
        self._line.setCornerRadius_(LINE_HEIGHT / 2)
        self._line.setFrame_(
            ((0, center_y - LINE_HEIGHT / 2), (WAVE_AREA_WIDTH, LINE_HEIGHT))
        )
        self._line.setHidden_(True)
        self._view.layer().addSublayer_(self._line)

        # Two-step line (Refine follows): colors per mode from PHASE_STEP_COLORS.
        self._step_colors = {
            mode: tuple(_ns_color(color).CGColor() for color in colors)
            for mode, colors in PHASE_STEP_COLORS.items()
        }
        self._steps = []
        for index in range(2):
            step = CALayer.alloc().init()
            step.setCornerRadius_(LINE_HEIGHT / 2)
            step.setFrame_(
                (
                    (index * (PHASE_STEP_WIDTH + PHASE_STEP_GAP), center_y - LINE_HEIGHT / 2),
                    (PHASE_STEP_WIDTH, LINE_HEIGHT),
                )
            )
            step.setHidden_(True)
            self._view.layer().addSublayer_(step)
            self._steps.append(step)

        # Shimmer: a gradient band inside a clipping track layer; one repeating
        # CABasicAnimation moves it on the render server (see _start_shimmer).
        self._shimmer_track = CALayer.alloc().init()
        self._shimmer_track.setMasksToBounds_(True)
        self._shimmer_track.setCornerRadius_(LINE_HEIGHT / 2)
        self._shimmer_track.setHidden_(True)
        band = CAGradientLayer.layer()
        highlight = _ns_color(SHIMMER_HIGHLIGHT_COLOR)
        clear = highlight.colorWithAlphaComponent_(0.0).CGColor()
        band.setColors_([clear, highlight.CGColor(), clear])
        band.setStartPoint_((0.0, 0.5))
        band.setEndPoint_((1.0, 0.5))
        # Parked left of the track; only the animation brings it into view.
        band.setFrame_(((-SHIMMER_BAND_WIDTH, 0), (SHIMMER_BAND_WIDTH, LINE_HEIGHT)))
        self._shimmer_track.addSublayer_(band)
        self._shimmer_band = band
        self._shimmer_active_color = _ns_color(SHIMMER_ACTIVE_STEP_COLOR).CGColor()
        self._view.layer().addSublayer_(self._shimmer_track)

    @property
    def view(self):
        return self._view

    def show_mode(self, mode: str) -> None:
        """Switch to ``level``/``low``/``line``/``phase1``/``phase2``/``none``.

        ``level`` and ``low`` are driven by audio; the processing lines get the
        shimmer unless the user prefers reduced motion; every other mode is static.
        """
        if self.current_animation == mode:
            return
        if self.current_animation == "low" and mode == "level":
            # Recording grows out of the listening row: keep heights and level.
            self.current_animation = mode
            transaction = _disabled_layer_actions()
            try:
                self.set_bar_color(self._bar_colors[mode])
            finally:
                transaction.commit()
            self._wake_level_timer()
            return
        self.stop_animating()
        self.current_animation = mode
        transaction = _disabled_layer_actions()
        try:
            self._view.setHidden_(mode == "none")
            self._line.setHidden_(mode != "line")
            step_colors = self._step_colors.get(mode)
            for step, color in zip(self._steps, step_colors or (None, None)):
                step.setHidden_(color is None)
                if color is not None:
                    step.setBackgroundColor_(color)
            for bar in self.bars:
                bar.setHidden_(mode not in ("level", "low"))
            if mode in ("level", "low"):
                self.set_bar_color(self._bar_colors[mode])
            if mode == "low":
                for index, height in enumerate(LISTENING_BAR_HEIGHTS):
                    self._set_bar_height(index, height, force=True)
            self._start_shimmer(mode)
        finally:
            transaction.commit()

    def _start_shimmer(self, mode: str) -> None:
        """Sweep the highlight over the line or active step of ``mode``.

        No-op for modes without a track and with reduced motion (the static
        line stays). Call inside a transaction without implicit actions.
        """
        track = shimmer_track(mode)
        if track is None or self._shimmer_band is None or _reduce_motion():
            return
        from Quartz import (  # type: ignore[import-not-found]
            CABasicAnimation,
            CAMediaTimingFunction,
            kCAMediaTimingFunctionEaseInEaseOut,
        )

        x, width = track
        self._shimmer_track.setFrame_(
            ((x, self._wave_center_y() - LINE_HEIGHT / 2), (width, LINE_HEIGHT))
        )
        step = PHASE_ACTIVE_STEP.get(mode)
        if step is not None:
            self._steps[step].setBackgroundColor_(self._shimmer_active_color)
        animation = CABasicAnimation.animationWithKeyPath_("position.x")
        animation.setFromValue_(-SHIMMER_BAND_WIDTH / 2)
        animation.setToValue_(width + SHIMMER_BAND_WIDTH / 2)
        animation.setDuration_(SHIMMER_PERIOD_SECONDS)
        animation.setRepeatCount_(float("inf"))
        animation.setTimingFunction_(
            CAMediaTimingFunction.functionWithName_(kCAMediaTimingFunctionEaseInEaseOut)
        )
        self._shimmer_band.addAnimation_forKey_(animation, "shimmer")
        self._shimmer_track.setHidden_(False)

    def _stop_shimmer(self) -> None:
        """Remove the shimmer animation and hide its track."""
        if self._shimmer_band is None:
            return
        self._shimmer_band.removeAnimationForKey_("shimmer")
        self._shimmer_track.setHidden_(True)

    def _ensure_recording_mode(self) -> None:
        if self.current_animation != "level":
            self.show_mode("level")

    def stop_animating(self) -> None:
        """Stoppt Level-Timer und Shimmer und setzt die Balken auf Ruhehöhe."""
        self._stop_level_timer()
        self.current_animation = None
        center_y = self._wave_center_y()
        transaction = _disabled_layer_actions()
        try:
            self._stop_shimmer()
            self._ensure_bar_positions(center_y)
            for i in range(len(self.bars)):
                self._set_bar_height(i, WAVE_BAR_MIN_HEIGHT, force=True)
        finally:
            transaction.commit()
        self._last_heights = [float(WAVE_BAR_MIN_HEIGHT) for _ in range(WAVE_BAR_COUNT)]
        self._agc_peak = WAVE_AGC_MIN_PEAK
        self._smoothed_level = 0.0
        self._target_level = 0.0


    def _ensure_bar_positions(self, center_y: float) -> None:
        if self._last_center_y == center_y:
            return

        for i, bar in enumerate(self.bars):
            bar.setPosition_((self._bar_positions[i], center_y))
        self._last_center_y = center_y

    def _set_bar_height(
        self,
        index: int,
        height: float,
        *,
        rect_factory=None,
        force: bool = False,
    ) -> bool:
        if (
            not force
            and abs(self._last_heights[index] - height) < WAVE_HEIGHT_UPDATE_EPSILON
        ):
            return False

        if rect_factory is None:
            rect_factory = getattr(self, "_rect_factory", None)
            if rect_factory is None:
                from AppKit import NSMakeRect  # type: ignore[import-not-found]

                rect_factory = NSMakeRect
                self._rect_factory = rect_factory

        self.bars[index].setBounds_(rect_factory(0, 0, WAVE_BAR_WIDTH, height))
        self._last_heights[index] = height
        return True

    def set_bar_color(self, ns_color) -> None:
        """Setzt die Farbe aller Balken."""
        cg_color = ns_color.CGColor()
        for bar in self.bars:
            bar.setBackgroundColor_(cg_color)

    def update_levels(self, level: float) -> None:
        """
        Aktualisiert die Ziel-Amplitude basierend auf Audio-RMS (0.0 - 1.0).

        Rendering passiert timer-basiert (smooth, unabhängig von Callback-Rate),
        update_levels() setzt nur den Zielwert und startet den Timer.
        """
        self._ensure_recording_mode()

        self._target_level = self._compute_target_level(level)
        self._wake_level_timer()

    def update_listening_level(self, rms: float) -> None:
        """Follow the microphone on the low listening row (no-op in other modes).

        Fixed scale without AGC (see ``listening_level``); stays active with
        reduced motion because it is an informational level meter.
        """
        if self.current_animation != "low":
            return
        self._target_level = listening_level(rms)
        self._wake_level_timer()

    def _wake_level_timer(self) -> None:
        """Resume rendering only when a new level or geometry needs a frame."""
        if self._level_timer is None:
            if not self._level_frame_is_settled(self._wave_center_y()):
                self._start_level_timer()
        else:
            self._update_level_timer_interval()

    def _compute_target_level(self, rms: float) -> float:
        if not isinstance(rms, (int, float)) or not math.isfinite(rms):
            rms = 0.0

        # Noise Gate: sehr leise Pegel ignorieren, darüber linearisieren.
        gated = max(rms - VISUAL_NOISE_GATE, 0.0)

        # AGC-Peak-Tracking: schneller Attack, langsamer Release.
        if gated > self._agc_peak:
            self._agc_peak = gated
        else:
            self._agc_peak = max(self._agc_peak * WAVE_AGC_DECAY, WAVE_AGC_MIN_PEAK)

        reference_peak = max(self._agc_peak * WAVE_AGC_HEADROOM, WAVE_AGC_MIN_PEAK)
        normalized = gated / reference_peak if reference_peak > 0 else 0.0
        normalized = _clamp01(normalized)

        # Visuelle Kurve + optionaler Gain.
        shaped = normalized**WAVE_VISUAL_EXPONENT
        return _clamp01(shaped * VISUAL_GAIN)

    def _start_level_timer(self) -> None:
        if self._level_timer is not None:
            return

        self._schedule_level_timer(self._level_timer_interval())

    def _schedule_level_timer(self, interval: float) -> None:
        from Foundation import NSTimer  # type: ignore[import-not-found]

        self._level_timer_interval_seconds = interval
        self_ref = weakref.ref(self)

        def tick(_timer) -> None:
            self_obj = self_ref()
            if self_obj is None:
                try:
                    _timer.invalidate()
                except Exception:
                    pass
                return
            self_obj._render_level_frame()

        self._level_timer = NSTimer.scheduledTimerWithTimeInterval_repeats_block_(
            interval, True, tick
        )

    def _recording_level_activity_mode(self) -> str:
        peak_level = max(self._target_level, self._smoothed_level)
        if self._level_timer_activity_mode == "active":
            return "active" if peak_level >= WAVE_LEVEL_IDLE_THRESHOLD else "idle"
        return "active" if peak_level >= WAVE_LEVEL_ACTIVE_THRESHOLD else "idle"

    def _update_level_timer_interval(self) -> None:
        desired_mode = self._recording_level_activity_mode()
        desired_interval = self._level_timer_interval()
        if (
            self._level_timer is None
            or self._level_timer_interval_seconds == desired_interval
            and self._level_timer_activity_mode == desired_mode
        ):
            self._level_timer_activity_mode = desired_mode
            return

        current_timer = self._level_timer
        self._level_timer = None
        try:
            current_timer.invalidate()
        except Exception:
            pass

        self._level_timer_activity_mode = desired_mode
        self._schedule_level_timer(desired_interval)

    def _stop_level_timer(self) -> None:
        if self._level_timer is None:
            return
        try:
            self._level_timer.invalidate()
        except Exception:
            pass
        self._level_timer = None
        self._level_timer_interval_seconds = None
        self._level_timer_activity_mode = "idle"

    def _level_timer_interval(self) -> float:
        activity_mode = self._recording_level_activity_mode()
        fps = (
            WAVE_ANIMATION_FPS
            if activity_mode == "active"
            else WAVE_ANIMATION_FPS_IDLE
        )
        return _fps_to_interval_seconds(fps)

    def _wave_center_y(self) -> float:
        center_y = getattr(self, "_center_y", None)
        if center_y is not None:
            return center_y
        return self._view.frame().size.height / 2

    def _wave_height_range(self) -> float:
        return getattr(self, "_height_range", WAVE_BAR_MAX_HEIGHT - WAVE_BAR_MIN_HEIGHT)

    def _recording_frame_buffer(self) -> list[float]:
        heights = getattr(self, "_recording_frame_heights", None)
        if heights is None or len(heights) != WAVE_BAR_COUNT:
            heights = [float(WAVE_BAR_MIN_HEIGHT) for _ in range(WAVE_BAR_COUNT)]
            self._recording_frame_heights = heights
        return heights

    def _recording_phase_tables(
        self,
    ) -> tuple[tuple[float, ...], tuple[float, ...], tuple[float, ...], tuple[float, ...]]:
        primary_offsets = getattr(self, "_wander_offset_primary", ())
        secondary_offsets = getattr(self, "_wander_offset_secondary", ())

        primary_sin = getattr(self, "_wander_offset_primary_sin", None)
        primary_cos = getattr(self, "_wander_offset_primary_cos", None)
        secondary_sin = getattr(self, "_wander_offset_secondary_sin", None)
        secondary_cos = getattr(self, "_wander_offset_secondary_cos", None)

        if primary_sin is None or len(primary_sin) != len(primary_offsets):
            primary_sin = tuple(math.sin(offset) for offset in primary_offsets)
            self._wander_offset_primary_sin = primary_sin
        if primary_cos is None or len(primary_cos) != len(primary_offsets):
            primary_cos = tuple(math.cos(offset) for offset in primary_offsets)
            self._wander_offset_primary_cos = primary_cos
        if secondary_sin is None or len(secondary_sin) != len(secondary_offsets):
            secondary_sin = tuple(math.sin(offset) for offset in secondary_offsets)
            self._wander_offset_secondary_sin = secondary_sin
        if secondary_cos is None or len(secondary_cos) != len(secondary_offsets):
            secondary_cos = tuple(math.cos(offset) for offset in secondary_offsets)
            self._wander_offset_secondary_cos = secondary_cos

        return primary_sin, primary_cos, secondary_sin, secondary_cos

    def _apply_bar_heights(self, heights: list[float], *, center_y: float) -> bool:
        """Commit bar updates only when positions or heights actually changed."""
        transaction = None
        positions_changed = self._last_center_y != center_y

        if positions_changed:
            from Quartz import CATransaction  # type: ignore[import-not-found]

            transaction = CATransaction
            transaction.begin()
            transaction.setDisableActions_(True)
            self._ensure_bar_positions(center_y)

        for index, height in enumerate(heights):
            if abs(self._last_heights[index] - height) < WAVE_HEIGHT_UPDATE_EPSILON:
                continue

            if transaction is None:
                from Quartz import CATransaction  # type: ignore[import-not-found]

                transaction = CATransaction
                transaction.begin()
                transaction.setDisableActions_(True)

            self._set_bar_height(index, height, force=True)

        if transaction is None:
            return False

        transaction.commit()
        return True

    def _level_frame_is_settled(self, center_y: float) -> bool:
        """A quiet, fully settled frame needs no timer until the next level update."""
        if self.current_animation == "low":
            # Listening settles at any steady level, not only at silence.
            return (
                abs(self._target_level - self._smoothed_level) <= LISTENING_LEVEL_EPSILON
                and self._last_center_y == center_y
                and _heights_match(
                    self._last_heights, listening_bar_heights(self._smoothed_level)
                )
            )
        return (
            self._target_level <= WAVE_LEVEL_SETTLED_EPSILON
            and self._smoothed_level <= WAVE_LEVEL_SETTLED_EPSILON
            and self._last_center_y == center_y
            and _wave_heights_are_settled(self._last_heights)
        )

    def _render_level_frame(self) -> None:
        """Rendert einen Frame der Level-Visualisierung (läuft im Main-Thread via NSTimer)."""
        # Ziel-Level sanft verfolgen (reduziert Zittern durch RMS-Fluktuationen).
        prev_level = self._smoothed_level
        target_level = self._target_level
        alpha_level = (
            WAVE_LEVEL_SMOOTHING_RISE
            if target_level > prev_level
            else WAVE_LEVEL_SMOOTHING_FALL
        )
        level = prev_level + alpha_level * (target_level - prev_level)
        self._smoothed_level = level

        center_y = self._wave_center_y()
        if self._level_frame_is_settled(center_y):
            if self.current_animation == "low":
                # Land exactly on the steady level (a subpixel snap at most).
                self._smoothed_level = target_level
                self._apply_bar_heights(
                    listening_bar_heights(target_level), center_y=center_y
                )
            self._stop_level_timer()
            return
        self._update_level_timer_interval()

        if self.current_animation == "low":
            self._render_listening_frame(level, center_y)
            return

        now = time.perf_counter()
        primary_tau = math.tau * WAVE_WANDER_HZ_PRIMARY
        secondary_tau = math.tau * WAVE_WANDER_HZ_SECONDARY
        phase_primary = primary_tau * now
        phase_secondary = secondary_tau * now + 1.7

        sin_phase_primary = math.sin(phase_primary)
        cos_phase_primary = math.cos(phase_primary)
        sin_phase_secondary = math.sin(phase_secondary)
        cos_phase_secondary = math.cos(phase_secondary)

        # Envelope-Center driftet nach links/rechts (wie ein Paket).
        # Bewegung skaliert mit Level: bei Stille wenig, beim Sprechen deutlich.
        shift_strength = self._envelope_max_shift_base * (
            WAVE_RECORDING_SHIFT_LEVEL_BASE
            + WAVE_RECORDING_SHIFT_LEVEL_SPAN * level
        )
        env_primary = math.sin(
            math.tau * WAVE_ENVELOPE_HZ_PRIMARY * now + self._envelope_phase_primary
        )
        env_secondary = math.sin(
            math.tau * WAVE_ENVELOPE_HZ_SECONDARY * now
            + self._envelope_phase_secondary
        )
        env_mix = (
            env_primary * WAVE_ENVELOPE_BLEND
            + env_secondary * WAVE_ENVELOPE_BLEND_COMPLEMENT
        )
        envelope_center = self._bar_center + shift_strength * env_mix
        envelope_center = _clamp(envelope_center, 0.0, WAVE_BAR_COUNT - 1)

        # Kleine Baseline-Bewegung, damit es nicht "steht" bei leiser Sprache,
        # aber skaliert mit Level, damit es ruhig bleibt.
        wander_strength = WAVE_WANDER_AMOUNT * (
            WAVE_RECORDING_WANDER_LEVEL_BASE
            + WAVE_RECORDING_WANDER_LEVEL_SPAN * level
        )
        max_add = self._wave_height_range()
        level_height_scale = level * max_add
        last_heights = self._last_heights
        height_factors = self._height_factors
        heights = self._recording_frame_buffer()
        (
            primary_offset_sin,
            primary_offset_cos,
            secondary_offset_sin,
            secondary_offset_cos,
        ) = self._recording_phase_tables()
        gaussian_scale = getattr(
            self,
            "_envelope_gaussian_scale",
            WAVE_ENVELOPE_GAUSSIAN_SCALE,
        )

        for i in range(WAVE_BAR_COUNT):
            travel_primary = (
                sin_phase_primary * primary_offset_cos[i]
                + cos_phase_primary * primary_offset_sin[i]
            )
            travel_secondary = (
                sin_phase_secondary * secondary_offset_cos[i]
                + cos_phase_secondary * secondary_offset_sin[i]
            )
            travel = (
                travel_primary * WAVE_WANDER_BLEND
                + travel_secondary * WAVE_WANDER_BLEND_COMPLEMENT
            )
            wiggle = 1.0 + wander_strength * travel

            # Dynamische Envelope verschiebt den "Energie"-Schwerpunkt über die Balken.
            distance = i - envelope_center
            if gaussian_scale is None:
                envelope = 0.0
            else:
                envelope = math.exp((distance * distance) * gaussian_scale)
            envelope_factor = WAVE_ENVELOPE_BASE + WAVE_ENVELOPE_FACTOR_SPAN * envelope

            base_factor = height_factors[i]
            height_factor = (
                base_factor * WAVE_ENVELOPE_STRENGTH_COMPLEMENT
                + envelope_factor * WAVE_ENVELOPE_STRENGTH
            )

            height = WAVE_BAR_MIN_HEIGHT + (level_height_scale * height_factor * wiggle)
            if height < WAVE_BAR_MIN_HEIGHT:
                height = WAVE_BAR_MIN_HEIGHT
            elif height > WAVE_BAR_MAX_HEIGHT:
                height = WAVE_BAR_MAX_HEIGHT

            prev_height = last_heights[i]
            alpha = (
                WAVE_SMOOTHING_ALPHA_RISE
                if height > prev_height
                else WAVE_SMOOTHING_ALPHA_FALL
            )
            heights[i] = prev_height + alpha * (height - prev_height)

        self._apply_bar_heights(heights, center_y=center_y)

    def _render_listening_frame(self, level: float, center_y: float) -> None:
        """Ease the low row toward the listening heights for ``level`` (no wave).

        Steps are at least 1.5x the layer-update epsilon (clearly committed
        despite float rounding) or land on the goal, so the tail never stalls
        short of the settle tolerance and the timer can stop.
        """
        heights = self._recording_frame_buffer()
        last_heights = self._last_heights
        min_step = 1.5 * WAVE_HEIGHT_UPDATE_EPSILON
        for i, goal in enumerate(listening_bar_heights(level)):
            prev_height = last_heights[i]
            gap = goal - prev_height
            step = gap * (
                WAVE_SMOOTHING_ALPHA_RISE if gap > 0 else WAVE_SMOOTHING_ALPHA_FALL
            )
            if -min_step < step < min_step:
                step = max(-min_step, min(min_step, gap))
            heights[i] = prev_height + step
        self._apply_bar_heights(heights, center_y=center_y)


_AUTO_HIDE_STATES = (AppState.DONE, AppState.NO_SPEECH)
# Seconds a short result stays before it fades out. "Pasted" is brief because
# the overlay can cover the text it just pasted.
FEEDBACK_DISPLAY_DURATION = 0.8
PASTED_DISPLAY_DURATION = 0.3
_LABEL_INSET = 2  # NSTextField-Labels zeichnen Text 2 px innerhalb ihres Frames
_NONACTIVATING_PANEL_MASK = 1 << 7  # NSWindowStyleMaskNonactivatingPanel
_CLICK_VIEW_CLASS = None


def _click_view_class():
    """NSView subclass that reports every click on the overlay (lazy: PyObjC)."""
    global _CLICK_VIEW_CLASS
    if _CLICK_VIEW_CLASS is None:
        import objc  # type: ignore[import-not-found]
        from AppKit import NSView  # type: ignore[import-not-found]

        class PulseScribeOverlayContentView(NSView):
            def acceptsFirstMouse_(self, _event):
                return True

            def hitTest_(self, point):
                # Badge/Text-Labels sollen Klicks nicht abfangen.
                hit = objc.super(PulseScribeOverlayContentView, self).hitTest_(point)
                return self if hit is not None else None

            def mouseDown_(self, event):
                handler = getattr(self, "pulsescribe_on_click", None)
                if handler is not None:
                    x = None
                    if event is not None:
                        x = self.convertPoint_fromView_(event.locationInWindow(), None).x
                    handler(x)

        _CLICK_VIEW_CLASS = PulseScribeOverlayContentView
    return _CLICK_VIEW_CLASS


def _make_label(font):
    from AppKit import NSTextField  # type: ignore[import-not-found]

    field = NSTextField.labelWithString_("")
    field.setFont_(font)
    field.setEditable_(False)
    field.setSelectable_(False)
    field.setBezeled_(False)
    field.setDrawsBackground_(False)
    field.cell().setLineBreakMode_(4)  # Truncate Tail
    field.cell().setUsesSingleLineMode_(True)
    return field


class OverlayController:
    """
    Overlay-Fenster für Status, Ergebnis-Badges und Interim-Text.

    Layout ``[Balken] [Badge] [Text] … [×]`` (ui/overlay_style.py); das Fenster
    hat eine feste Breite; nur bleibende Meldungen wachsen. Kein Polling - wird direkt via Callback aktualisiert.

    Recordings with live text use a larger overlay with two text lines for the
    whole run. Call ``set_live_text_expected`` before the run's first state so
    the overlay opens at that size instead of growing with the first words.

    Fehler bleiben sichtbar, bis der Nutzer klickt oder ein neuer State sie
    ersetzt; nur dann nimmt das Fenster Mausereignisse an, sonst klickt man
    hindurch.
    """

    def __init__(self, on_open_settings=None):
        """``on_open_settings`` runs when the user clicks a "Settings" link."""
        from AppKit import (  # type: ignore[import-not-found]
            NSBackingStoreBuffered,
            NSColor,
            NSFont,
            NSFontWeightRegular,
            NSFontWeightSemibold,
            NSMakeRect,
            NSPanel,
            NSScreen,
            NSTextAlignmentRight,
            NSView,
            NSWindowStyleMaskBorderless,
        )
        import Quartz  # noqa: F401  # registriert CGColorRef für .CGColor()

        screen = NSScreen.mainScreen()
        if not screen:
            self.window = None
            return

        self._rect = NSMakeRect
        self._screen_frame = screen.frame()
        self._max_width = self._screen_frame.size.width * OVERLAY_MAX_WIDTH_RATIO
        width = OVERLAY_WIDTH
        height = OVERLAY_HEIGHT
        y = self._screen_frame.origin.y + OVERLAY_MARGIN_BOTTOM

        # Non-activating panel: a click on a persistent message must not take
        # focus away from the app the user dictates into.
        self.window = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(self._window_x(width), y, width, height),
            NSWindowStyleMaskBorderless | _NONACTIVATING_PANEL_MASK,
            NSBackingStoreBuffered,
            False,
        )
        self.window.setHidesOnDeactivate_(False)
        self.window.setLevel_(OVERLAY_WINDOW_LEVEL)
        self.window.setIgnoresMouseEvents_(True)
        # Nicht-opak nur für die 3-px-Ecken; der Inhalt selbst ist solide.
        self.window.setOpaque_(False)
        self.window.setBackgroundColor_(NSColor.clearColor())
        self.window.setAlphaValue_(0.0)
        self.window.setHasShadow_(False)

        content = _click_view_class().alloc().initWithFrame_(
            NSMakeRect(0, 0, width, height)
        )
        self_ref = weakref.ref(self)

        def on_click(x: float | None) -> None:
            controller = self_ref()
            if controller is not None:
                controller._on_click(x)

        content.pulsescribe_on_click = on_click
        content.setWantsLayer_(True)
        layer = content.layer()
        layer.setBackgroundColor_(_ns_color(BACKGROUND_COLOR).CGColor())
        layer.setBorderColor_(_ns_color(BORDER_COLOR).CGColor())
        layer.setBorderWidth_(1.0)
        layer.setCornerRadius_(OVERLAY_CORNER_RADIUS)
        layer.setMasksToBounds_(True)
        self.window.setContentView_(content)
        self._content_view = content

        self._wave_view = SoundWaveView(
            NSMakeRect(
                0, (height - WAVE_BAR_MAX_HEIGHT) / 2, WAVE_AREA_WIDTH, WAVE_BAR_MAX_HEIGHT
            )
        )
        content.addSubview_(self._wave_view.view)

        self._text_font = NSFont.systemFontOfSize_weight_(
            TEXT_FONT_SIZE, NSFontWeightRegular
        )
        # Text zone, full overlay height: one label per text line. Status texts
        # use the first label, live text one label per wrapped line.
        self._text_clip = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, 0, 0))
        self._text_clip.setWantsLayer_(True)
        self._text_clip.layer().setMasksToBounds_(True)
        self._text_fields = [_make_label(self._text_font) for _ in range(LIVE_TEXT_LINES)]
        for field in self._text_fields:
            field.setHidden_(True)
            self._text_clip.addSubview_(field)
        self._text_field = self._text_fields[0]
        content.addSubview_(self._text_clip)

        self._meta_font = NSFont.monospacedDigitSystemFontOfSize_weight_(
            META_FONT_SIZE, NSFontWeightRegular
        )
        self._meta_field = _make_label(self._meta_font)
        self._meta_field.setTextColor_(_ns_color(META_TEXT_COLOR))
        self._meta_field.setAlignment_(NSTextAlignmentRight)
        self._meta_field.setHidden_(True)
        content.addSubview_(self._meta_field)

        self._dismiss_field = _make_label(
            NSFont.systemFontOfSize_weight_(DISMISS_FONT_SIZE, NSFontWeightRegular)
        )
        self._dismiss_field.setStringValue_(DISMISS_GLYPH)
        self._dismiss_field.setTextColor_(_ns_color(MUTED_TEXT_COLOR))
        self._dismiss_field.setHidden_(True)
        content.addSubview_(self._dismiss_field)
        self._dismiss_size = self._dismiss_field.cell().cellSize()

        self._badge_view = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, 0, 0))
        self._badge_view.setWantsLayer_(True)
        self._badge_view.layer().setCornerRadius_(BADGE_CORNER_RADIUS)
        self._badge_view.setHidden_(True)
        self._badge_label = _make_label(
            NSFont.systemFontOfSize_weight_(BADGE_FONT_SIZE, NSFontWeightSemibold)
        )
        self._badge_view.addSubview_(self._badge_label)
        content.addSubview_(self._badge_view)

        self._badge_label.setStringValue_("Ag")
        self._badge_label_height = math.ceil(self._badge_label.cell().cellSize().height)
        self._text_field.setStringValue_("Ag")
        self._text_height = math.ceil(self._text_field.cell().cellSize().height)
        self._meta_field.setStringValue_("0:00")
        self._meta_height = math.ceil(self._meta_field.cell().cellSize().height)

        self._colors = {
            "text": _ns_color(TEXT_COLOR),
            "muted": _ns_color(MUTED_TEXT_COLOR),
            "interim": _ns_color(INTERIM_TEXT_COLOR),
            "link": _ns_color(LINK_TEXT_COLOR),
        }
        self._badge_colors = {
            tone: (_ns_color(background), _ns_color(foreground))
            for tone, (background, foreground) in BADGE_TONES.items()
        }

        self._target_alpha = 0.0
        self._current_state = AppState.IDLE
        self._feedback_timer = None
        self._last_state_payload: tuple[AppState, str | None] | None = None
        self._interim_lines: InterimLines | None = None
        self._live_text_expected = False
        self._live_size = False
        self._live_zone_width: float | None = None
        self._presentation = None
        self._window_size = (float(width), float(height))
        self._persistent = False
        self._meta_text = ""
        self._clock_timer = None
        self._clock_start = 0.0
        self._has_text = False
        self._on_open_settings = on_open_settings
        self._link_span: tuple[float, float] | None = None

    def _window_x(self, width: float) -> float:
        frame = self._screen_frame
        return frame.origin.x + (frame.size.width - width) / 2

    def update_state(self, state: AppState, text: str | None = None) -> None:
        """Update state; retain brief success feedback while already ready to record."""
        if not self.window:
            return

        previous_state = self._current_state
        if getattr(self, "_last_state_payload", None) == (state, text):
            return
        self._last_state_payload = (state, text)
        self._current_state = state
        state_changed = state != previous_state

        if (
            state == AppState.IDLE
            and previous_state == AppState.DONE
            and self._feedback_timer is not None
        ):
            return

        # Cancel stale feedback timer from previous DONE/ERROR state.
        # Without this, a quick re-recording within the auto-hide delay
        # causes the timer to fire _fade_out() during the new recording.
        if state_changed and self._feedback_timer:
            self._feedback_timer.invalidate()
            self._feedback_timer = None

        if state == AppState.IDLE:
            self._stop_clock()
            # Daemon returns to IDLE so hotkeys work; a persistent message stays.
            if state_changed and not self._persistent:
                self._wave_view.stop_animating()
                self._fade_out()
            return

        if state.name in RECORDING_CLOCK_STATES:
            self._start_clock()
        else:
            self._stop_clock()
        content = describe_overlay_content(state.name, text)
        # A new error after a dismissed one must show even within ERROR.
        reshow = content.dismissible and not getattr(self, "_persistent", False)
        self._set_persistent(content.dismissible)
        interim: tuple[tuple[str, str], ...] = ()
        if state == AppState.RECORDING:
            if state_changed:
                self._interim_lines = None
            if text:
                self._interim_lines = wrap_interim_text(
                    text, self._interim_lines, self._fits_live_line, LIVE_TEXT_LINES
                )
                interim = self._interim_lines.lines
        self._update_live_size(state, previous_state, bool(interim))

        # Same state, new payload can change the mode (TRANSCRIBING + Refine);
        # show_mode is a no-op when the mode is unchanged.
        self._wave_view.show_mode(content.bars)
        self._render(content, interim)
        if state_changed or reshow:
            self._fade_in()
        if state_changed and state in _AUTO_HIDE_STATES:
            self._start_fade_out_timer(
                PASTED_DISPLAY_DURATION
                if content.badge == PASTED_BADGE
                else FEEDBACK_DISPLAY_DURATION
            )

    def set_live_text_expected(self, expected: bool) -> None:
        """Tell whether the recording that starts now will show live text.

        Call it right before the run's LISTENING state; a call that arrives
        just after it still sizes the run.
        """
        self._live_text_expected = expected
        if self._current_state == AppState.LISTENING and self._live_size != expected:
            self._live_size = expected
            if self._presentation is not None:
                self._render(*self._presentation[:2])

    def _update_live_size(
        self, state: AppState, previous_state: AppState, has_live_text: bool
    ) -> None:
        """Pick the overlay size for a run: large with live text, else compact.

        The size is set when a run starts (LISTENING) and kept until its
        feedback is gone, so nothing resizes while the user watches. Live text
        that was not announced still gets the large size. A message outside a
        run is compact.
        """
        if has_live_text:
            self._live_size = True
        elif state == AppState.LISTENING:
            if previous_state != AppState.LISTENING:
                self._live_size = self._live_text_expected
        elif previous_state == AppState.IDLE:
            self._live_size = False

    def _fits_live_line(self, line: str) -> bool:
        """Whether ``line`` fits one line of the live text zone."""
        from AppKit import NSFontAttributeName  # type: ignore[import-not-found]
        from Foundation import NSString  # type: ignore[import-not-found]

        if self._live_zone_width is None:
            # Reserve the clock at its widest so a tenth minute does not reflow.
            self._meta_field.setStringValue_("00:00")
            clock = math.ceil(self._meta_field.cell().cellSize().width) - 2 * _LABEL_INSET
            self._meta_field.setStringValue_(self._meta_text)
            self._live_zone_width = compute_overlay_layout(
                text_width=0, meta_width=clock, base_width=LIVE_TEXT_WIDTH
            ).text_width
        width = NSString.stringWithString_(line).sizeWithAttributes_(
            {NSFontAttributeName: self._text_font}
        ).width
        return width <= self._live_zone_width

    def _start_clock(self) -> None:
        """Show ``0:00`` and tick once per second until ``_stop_clock``."""
        if getattr(self, "_clock_timer", None) is not None:
            return
        from Foundation import NSTimer  # type: ignore[import-not-found]

        self._clock_start = time.monotonic()
        self._meta_text = format_recording_duration(0)
        self_ref = weakref.ref(self)

        def tick(timer) -> None:
            controller = self_ref()
            if controller is None or timer is not controller._clock_timer:
                timer.invalidate()
                return
            controller._tick_clock()

        timer = NSTimer.scheduledTimerWithTimeInterval_repeats_block_(
            CLOCK_INTERVAL_SECONDS, True, tick
        )
        timer.setTolerance_(CLOCK_TOLERANCE_SECONDS)
        self._clock_timer = timer

    def _tick_clock(self) -> None:
        # round(): a tick that fires a little early still shows the full second.
        elapsed = round(time.monotonic() - self._clock_start)
        self._meta_text = format_recording_duration(elapsed)
        if self._presentation is not None:
            content, interim = self._presentation[:2]
            self._render(content, interim)

    def _stop_clock(self) -> None:
        timer = getattr(self, "_clock_timer", None)
        self._clock_timer = None
        self._meta_text = ""
        if timer is not None:
            timer.invalidate()

    def _set_persistent(self, persistent: bool) -> None:
        """Accept clicks only while a dismissible message is shown."""
        if getattr(self, "_persistent", False) == persistent:
            return
        self._persistent = persistent
        self.window.setIgnoresMouseEvents_(not persistent)

    def _on_click(self, x: float | None) -> None:
        """Every click dismisses; a click on the "Settings" link also opens it."""
        span = getattr(self, "_link_span", None)
        on_link = (
            getattr(self, "_persistent", False)
            and span is not None
            and x is not None
            and span[0] <= x <= span[1]
        )
        self._dismiss()
        callback = getattr(self, "_on_open_settings", None)
        if on_link and callback is not None:
            callback()

    def _dismiss(self) -> None:
        """Hide a persistent message after a click on the overlay."""
        if not getattr(self, "_persistent", False):
            return
        self._set_persistent(False)
        self._wave_view.stop_animating()
        self._fade_out()

    def _render(
        self, content: OverlayContent, interim: tuple[tuple[str, str], ...]
    ) -> None:
        """Apply badge, text, meta and layout; skips identical presentations.

        ``interim`` holds ``(confirmed, fresh)`` per live text line; empty, the
        content's own text shows. Meta is the recording clock while it runs,
        else the content's meta. A clock tick only changes the meta text, so
        the text and badge are left alone and just the layout is refreshed.
        """
        meta = getattr(self, "_meta_text", "") or content.meta
        live_size = getattr(self, "_live_size", False)
        presentation = (content, interim, meta, live_size)
        previous = self._presentation
        if previous == presentation:
            return
        self._presentation = presentation

        if previous is None or previous[:2] != (content, interim):
            if interim:
                # Confirmed words stand out; fresh ones may still change.
                lines = [((confirmed, "text"), (fresh, "interim")) for confirmed, fresh in interim]
            else:
                lines = [((content.text, "text" if content.emphasized else "muted"),)]
            self._has_text = any(part for parts in lines for part, _color in parts)
            for index, field in enumerate(self._text_fields):
                shown = index < len(lines) and self._has_text
                if shown:
                    field.setAttributedStringValue_(self._attributed_text(lines[index]))
                field.setHidden_(not shown)

            if content.badge:
                background, foreground = self._badge_colors[content.tone]
                self._badge_view.layer().setBackgroundColor_(background.CGColor())
                self._badge_label.setTextColor_(foreground)
                self._badge_label.setStringValue_(content.badge)
            self._badge_view.setHidden_(not content.badge)
            self._dismiss_field.setHidden_(not content.dismissible)
        if previous is None or (previous[2], previous[0].action) != (meta, content.action):
            if content.action:
                self._meta_field.setAttributedStringValue_(self._link_text(meta))
            else:
                self._meta_field.setTextColor_(self._colors["muted"])
                self._meta_field.setStringValue_(meta)
            self._meta_field.setHidden_(not meta)
        self._layout(content, self._has_text, len(interim), meta)

    def _link_text(self, text: str):
        """White, underlined meta link (e.g. "Settings")."""
        from AppKit import (  # type: ignore[import-not-found]
            NSAttributedString,
            NSFontAttributeName,
            NSForegroundColorAttributeName,
            NSUnderlineStyleAttributeName,
        )

        return NSAttributedString.alloc().initWithString_attributes_(
            text,
            {
                NSFontAttributeName: self._meta_font,
                NSForegroundColorAttributeName: self._colors["link"],
                NSUnderlineStyleAttributeName: 1,
            },
        )

    def _attributed_text(self, parts):
        from AppKit import (  # type: ignore[import-not-found]
            NSAttributedString,
            NSFontAttributeName,
            NSForegroundColorAttributeName,
            NSMutableAttributedString,
            NSMutableParagraphStyle,
            NSParagraphStyleAttributeName,
        )

        paragraph = NSMutableParagraphStyle.alloc().init()
        paragraph.setLineBreakMode_(4)  # Truncate Tail
        result = NSMutableAttributedString.alloc().init()
        for part, color_key in parts:
            if not part:
                continue
            result.appendAttributedString_(
                NSAttributedString.alloc().initWithString_attributes_(
                    part,
                    {
                        NSFontAttributeName: self._text_font,
                        NSForegroundColorAttributeName: self._colors[color_key],
                        NSParagraphStyleAttributeName: paragraph,
                    },
                )
            )
        return result

    def _layout(
        self, content: OverlayContent, has_text: bool, live_lines: int, meta: str
    ) -> None:
        """Place every part; ``live_lines`` is the number of live text lines shown."""
        rect = self._rect
        live_size = getattr(self, "_live_size", False)
        height = LIVE_TEXT_HEIGHT if live_size else OVERLAY_HEIGHT
        text_width = (
            math.ceil(self._text_field.cell().cellSize().width) - 2 * _LABEL_INSET
            if has_text
            else 0
        )
        meta_width = (
            math.ceil(self._meta_field.cell().cellSize().width) - 2 * _LABEL_INSET
            if meta
            else 0
        )
        badge_width = 0
        if content.badge:
            badge_width = (
                math.ceil(self._badge_label.cell().cellSize().width)
                - 2 * _LABEL_INSET
                + 2 * BADGE_PADDING_H
            )
        dismiss_size = self._dismiss_size
        dismiss_width = (
            math.ceil(dismiss_size.width) - 2 * _LABEL_INSET if content.dismissible else 0
        )
        layout = compute_overlay_layout(
            text_width=text_width,
            meta_width=meta_width,
            dismiss_width=dismiss_width,
            status_width=badge_width,
            grow=content.dismissible,
            max_width=self._max_width,
            base_width=LIVE_TEXT_WIDTH if live_size else OVERLAY_WIDTH,
        )
        if layout.dismiss_x is not None:
            dismiss_height = math.ceil(dismiss_size.height)
            self._dismiss_field.setFrame_(
                rect(
                    layout.dismiss_x - _LABEL_INSET,
                    round((height - dismiss_height) / 2),
                    dismiss_width + 2 * _LABEL_INSET,
                    dismiss_height,
                )
            )
        self._link_span = (
            (
                layout.meta_x - LINK_HIT_SLOP,
                layout.meta_x + meta_width + LINK_HIT_SLOP,
            )
            if content.action and meta_width
            else None
        )
        if meta_width:
            self._meta_field.setFrame_(
                rect(
                    layout.meta_x - _LABEL_INSET,
                    round((height - self._meta_height) / 2),
                    meta_width + 2 * _LABEL_INSET,
                    self._meta_height,
                )
            )
        if content.bars != "none":
            self._wave_view.view.setFrameOrigin_(
                (layout.status_x, (height - WAVE_BAR_MAX_HEIGHT) / 2)
            )
        if content.badge:
            badge_height = self._badge_label_height + 2 * BADGE_PADDING_V
            self._badge_view.setFrame_(
                rect(
                    layout.status_x,
                    round((height - badge_height) / 2),
                    badge_width,
                    badge_height,
                )
            )
            self._badge_label.setFrame_(
                rect(
                    BADGE_PADDING_H - _LABEL_INSET,
                    BADGE_PADDING_V,
                    badge_width - 2 * BADGE_PADDING_H + 2 * _LABEL_INSET,
                    self._badge_label_height,
                )
            )
        zone = layout.text_width
        self._text_clip.setFrame_(rect(layout.text_x, 0, zone, height))
        # Live text keeps the place of a full block of lines from its first
        # word on, so a second line does not move the first. Other text sits
        # on the center line and is elided at the end.
        block = self._text_height + (LIVE_TEXT_LINES - 1) * LIVE_TEXT_LINE_PITCH
        top = round((height + block) / 2) if live_lines else round((height + self._text_height) / 2)
        for index, field in enumerate(self._text_fields):
            field.setFrame_(
                rect(
                    -_LABEL_INSET,
                    top - self._text_height - index * LIVE_TEXT_LINE_PITCH,
                    zone + 2 * _LABEL_INSET,
                    self._text_height,
                )
            )
        self._resize_window(layout.width, height)

    def _resize_window(self, width: float, height: float) -> None:
        if (width, height) == self._window_size:
            return
        self._window_size = (width, height)
        origin_y = self.window.frame().origin.y
        self.window.setFrame_display_(
            self._rect(self._window_x(width), origin_y, width, height), True
        )

    def _fade_in(self) -> None:
        """Blendet Overlay ein."""
        window = self.window
        if window is None or self._target_alpha == OVERLAY_ALPHA:
            return
        self._target_alpha = OVERLAY_ALPHA
        window.orderFront_(None)
        window.animator().setAlphaValue_(OVERLAY_ALPHA)

    def _fade_out(self) -> None:
        """Blendet Overlay aus."""
        window = self.window
        if window is None or self._target_alpha == 0.0:
            return
        self._target_alpha = 0.0
        window.animator().setAlphaValue_(0.0)

    def _start_fade_out_timer(self, seconds: float) -> None:
        """Blendet ein Done/No-Speech-Ergebnis nach ``seconds`` automatisch aus."""
        from Foundation import NSTimer  # type: ignore[import-not-found]

        if self._feedback_timer:
            self._feedback_timer.invalidate()

        def fade_out_callback(timer):
            if timer is not self._feedback_timer:
                return
            self._feedback_timer = None
            self._wave_view.stop_animating()
            self._fade_out()

        self._feedback_timer = NSTimer.scheduledTimerWithTimeInterval_repeats_block_(
            seconds, False, fade_out_callback
        )

    def update_audio_level(self, level: float) -> None:
        """Aktualisiert Audio-Visualisierung (Echtzeit)."""
        if self._current_state == AppState.RECORDING:
            self._wave_view.update_levels(level)
        elif self._current_state == AppState.LISTENING:
            self._wave_view.update_listening_level(level)
