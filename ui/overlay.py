"""Overlay-Controller und Visualisierung für whisper_go."""

import math
import random
import time
import weakref

from config import VISUAL_GAIN, VISUAL_NOISE_GATE
from utils.state import AppState

# =============================================================================
# Overlay-Fenster Konfiguration
# =============================================================================
OVERLAY_MIN_WIDTH = 260        # Mindestbreite für kurze Texte
OVERLAY_MAX_WIDTH_RATIO = 0.75 # Max. 75% der Bildschirmbreite für lange Interim-Texte
OVERLAY_HEIGHT = 100           # Feste Höhe für konsistentes Erscheinungsbild
OVERLAY_MARGIN_BOTTOM = 110    # Abstand vom unteren Rand (über Dock)
OVERLAY_CORNER_RADIUS = 22     # Abgerundete Ecken (Apple HIG)
OVERLAY_PADDING_H = 24         # Horizontaler Innenabstand für Text
OVERLAY_ALPHA = 0.95           # Leicht transparent für Kontext
OVERLAY_FONT_SIZE = 15         # SF Pro Standard-Größe
OVERLAY_TEXT_FIELD_HEIGHT = 24 # Einzeilige Textanzeige
OVERLAY_WINDOW_LEVEL = 25      # Über allen Fenstern, kCGFloatingWindowLevel

# =============================================================================
# Schallwellen-Visualisierung
# =============================================================================
WAVE_BAR_COUNT = 10            # Anzahl der animierten Balken
WAVE_BAR_WIDTH = 3             # Schlankere Balkenbreite in Pixel
WAVE_BAR_GAP = 4               # Etwas mehr Abstand zwischen Balken
WAVE_BAR_MIN_HEIGHT = 8        # Ruhezustand-Höhe
WAVE_BAR_MAX_HEIGHT = 48       # Maximale Höhe bei voller Animation
WAVE_AREA_WIDTH = WAVE_BAR_COUNT * WAVE_BAR_WIDTH + (WAVE_BAR_COUNT - 1) * WAVE_BAR_GAP

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

# Adaptive Gain Control (AGC):
# Hält die Visualisierung bei leisen/lauteren Mics dynamisch.
# Wir tracken einen rollenden Peak und normalisieren mit etwas Headroom.
WAVE_AGC_DECAY = 0.97        # Peak-Falloff pro Level-Update (~1s Zeitkonstante bei ~15Hz)
WAVE_AGC_MIN_PEAK = 0.01     # Untergrenze gegen Überverstärkung von Stille
WAVE_AGC_HEADROOM = 2.0      # Mehr Headroom = weniger Sensitivität/Clipping

# Rendering/Animation:
# Wir rendern die Welle unabhängig von Audio-Level-Updates, damit sie auch bei ~15Hz
# RMS-Callbacks smooth "wandert" (ähnlicher zu iOS/Whisper Flow).
WAVE_ANIMATION_FPS = 60.0

# Sanfte, deterministische "Wander"-Modulation (traveling wave) statt Random-Jitter.
WAVE_WANDER_AMOUNT = 0.22         # Relative Modulation (0..~0.35 sinnvoll)
WAVE_WANDER_HZ_PRIMARY = 0.55     # Hz (langsam, organisch)
WAVE_WANDER_HZ_SECONDARY = 0.95   # Hz (leicht schneller, bricht Monotonie)
WAVE_WANDER_PHASE_STEP_PRIMARY = 0.85    # rad/bar (spatial frequency)
WAVE_WANDER_PHASE_STEP_SECONDARY = 1.65  # rad/bar (spatial frequency)
WAVE_WANDER_BLEND = 0.65         # 0..1 mix primary/secondary

# Zusätzlich: "Energie"-Envelope, die als Paket nach links/rechts wandert.
# Ohne das sind die höchsten Balken oft immer in der Mitte (statisch wirkend).
WAVE_ENVELOPE_STRENGTH = 0.85   # 0..1 (1 = nur Envelope, 0 = nur Basisfaktoren)
WAVE_ENVELOPE_BASE = 0.38       # Mindest-Faktor (verhindert zu flache Ränder)
WAVE_ENVELOPE_SIGMA = 1.15      # Breite des Pakets in Balken (kleiner = stärkerer Fokus)
WAVE_ENVELOPE_RANGE_FRACTION = 1.25  # Max. Verschiebung relativ zur halben Balkenanzahl
WAVE_ENVELOPE_HZ_PRIMARY = 0.15      # Drift (öfter links/rechts)
WAVE_ENVELOPE_HZ_SECONDARY = 0.24    # Zweite Frequenz für weniger Periodizität
WAVE_ENVELOPE_BLEND = 0.62           # 0..1 mix primary/secondary

# Feedback-Anzeigedauer
FEEDBACK_DISPLAY_DURATION = 0.8  # Sekunden für Done/Error-Anzeige

# =============================================================================
# Helpers
# =============================================================================


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _clamp01(value: float) -> float:
    return _clamp(value, 0.0, 1.0)


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def _gaussian(distance: float, sigma: float) -> float:
    if sigma <= 0:
        return 0.0
    x = distance / sigma
    return math.exp(-0.5 * x * x)


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


def _build_recording_durations() -> list[float]:
    """Gibt leicht variierende Dauerwerte für die Aufnahme-Animation zurück."""
    if WAVE_BAR_COUNT <= 1:
        return [0.4]

    durations: list[float] = []
    for i in range(WAVE_BAR_COUNT):
        phase = i / (WAVE_BAR_COUNT - 1)
        durations.append(0.36 + 0.08 * math.sin(phase * math.pi))
    return durations


def _get_overlay_color(r: int, g: int, b: int, a: float = 1.0):
    """Erstellt NSColor aus RGB-Werten."""
    from AppKit import NSColor  # type: ignore[import-not-found]

    return NSColor.colorWithSRGBRed_green_blue_alpha_(
        r / 255.0, g / 255.0, b / 255.0, a
    )


class SoundWaveView:
    """
    Animierte Schallwellen-Visualisierung (aus overlay.py).

    Zeigt verschiedene Animationen je nach State:
    - Recording: Organische Wellenanimation
    - Transcribing: Sequentielle Ladeanimation
    - Done: Einmaliges Hüpfen in Grün
    - Error: Rotes Aufblinken
    """

    def __init__(self, frame):
        from AppKit import NSColor, NSView  # type: ignore[import-not-found]
        from Quartz import CALayer  # type: ignore[import-not-found]

        # NSView erstellen
        self._view = NSView.alloc().initWithFrame_(frame)
        self._view.setWantsLayer_(True)
        self.bars = []
        self.animations_running = False
        self.current_animation = None

        # Farben
        self._color_idle = NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.9)
        self._color_listening = _get_overlay_color(255, 182, 193)
        self._color_recording = _get_overlay_color(255, 82, 82)
        self._color_transcribing = _get_overlay_color(255, 177, 66)
        self._color_refining = _get_overlay_color(156, 39, 176)
        self._color_success = _get_overlay_color(51, 217, 178)
        self._color_error = _get_overlay_color(255, 71, 87)

        self._height_factors = _build_height_factors()
        self._recording_durations = _build_recording_durations()
        self._last_heights = [WAVE_BAR_MIN_HEIGHT for _ in range(WAVE_BAR_COUNT)]

        self._bar_center = (WAVE_BAR_COUNT - 1) / 2
        self._envelope_max_shift_base = max(
            0.0, self._bar_center * WAVE_ENVELOPE_RANGE_FRACTION
        )

        self._agc_peak = WAVE_AGC_MIN_PEAK
        self._target_level = 0.0
        self._smoothed_level = 0.0
        self._level_timer = None
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
        # Balken erstellen
        center_y = frame.size.height / 2
        for i in range(WAVE_BAR_COUNT):
            x = i * (WAVE_BAR_WIDTH + WAVE_BAR_GAP)
            bar = CALayer.alloc().init()
            bar.setBackgroundColor_(self._color_idle.CGColor())
            bar.setCornerRadius_(WAVE_BAR_WIDTH / 2)
            bar.setFrame_(
                (
                    (x, center_y - WAVE_BAR_MIN_HEIGHT / 2),
                    (WAVE_BAR_WIDTH, WAVE_BAR_MIN_HEIGHT),
                )
            )
            self._view.layer().addSublayer_(bar)
            self.bars.append(bar)

    @property
    def view(self):
        return self._view

    def set_bar_color(self, ns_color) -> None:
        """Setzt die Farbe aller Balken."""
        cg_color = ns_color.CGColor()
        for bar in self.bars:
            bar.setBackgroundColor_(cg_color)

    def _create_height_animation(
        self, to_height, duration, delay=0, repeat=float("inf")
    ):
        """Erstellt Höhen-Animation für Balken."""
        from Quartz import (  # type: ignore[import-not-found]
            CABasicAnimation,
            CAMediaTimingFunction,
            kCAMediaTimingFunctionEaseInEaseOut,
        )

        anim = CABasicAnimation.animationWithKeyPath_("bounds.size.height")
        anim.setFromValue_(WAVE_BAR_MIN_HEIGHT)
        anim.setToValue_(to_height)
        anim.setDuration_(duration)
        anim.setAutoreverses_(True)
        anim.setRepeatCount_(repeat)
        anim.setTimingFunction_(
            CAMediaTimingFunction.functionWithName_(kCAMediaTimingFunctionEaseInEaseOut)
        )
        return anim

    def start_listening_animation(self) -> None:
        """Startet sanfte Listening-Animation (Warten auf Sprache)."""
        if self.current_animation == "listening":
            return
        self.stop_animating()
        self.current_animation = "listening"
        self.animations_running = True
        self.set_bar_color(self._color_listening)

        # Langsames Atmen
        for i, bar in enumerate(self.bars):
            anim = self._create_height_animation(WAVE_BAR_MAX_HEIGHT * 0.4, 1.2)
            bar.addAnimation_forKey_(anim, f"listenAnim{i}")

    def update_levels(self, level: float) -> None:
        """
        Aktualisiert die Ziel-Amplitude basierend auf Audio-RMS (0.0 - 1.0).

        Rendering passiert timer-basiert (smooth, unabhängig von Callback-Rate),
        update_levels() setzt nur den Zielwert und startet den Timer.
        """
        # Falls noch Animationen laufen (z.B. Fallback-Welle), stoppen wir sie,
        # damit wir die Höhe manuell setzen können.
        if self.animations_running:
            self.stop_animating()

        self._ensure_recording_mode()

        self._target_level = self._compute_target_level(level)

        # Timer-basiertes Rendering starten (smooth wandering unabhängig von Audio callback rate)
        self._start_level_timer()

    def _ensure_recording_mode(self) -> None:
        if self.current_animation != "recording":
            self.current_animation = "recording"
            self.set_bar_color(self._color_recording)

    def _compute_target_level(self, rms: float) -> float:
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

    def start_recording_animation(self) -> None:
        """Startet organische Schallwellen-Animation (Fallback wenn keine Levels)."""
        if self.current_animation == "recording":
            return
        self.stop_animating()
        self.current_animation = "recording"
        self.animations_running = True
        self.set_bar_color(self._color_recording)

        for i, bar in enumerate(self.bars):
            anim = self._create_height_animation(
                WAVE_BAR_MAX_HEIGHT, self._recording_durations[i]
            )
            bar.addAnimation_forKey_(anim, f"heightAnim{i}")

    def start_transcribing_animation(self) -> None:
        """Startet Loading-Wellen-Animation."""
        if self.current_animation == "transcribing":
            return
        self.stop_animating()
        self.current_animation = "transcribing"
        self.animations_running = True
        self.set_bar_color(self._color_transcribing)

        for i, bar in enumerate(self.bars):
            anim = self._create_height_animation(WAVE_BAR_MAX_HEIGHT * 0.7, 1.0)
            bar.addAnimation_forKey_(anim, f"transcribeAnim{i}")

    def start_refining_animation(self) -> None:
        """Startet Refining-Animation (Pulsieren)."""
        if self.current_animation == "refining":
            return
        self.stop_animating()
        self.current_animation = "refining"
        self.animations_running = True
        self.set_bar_color(self._color_refining)

        # Synchrones Pulsieren
        for i, bar in enumerate(self.bars):
            anim = self._create_height_animation(WAVE_BAR_MAX_HEIGHT * 0.6, 0.8)
            bar.addAnimation_forKey_(anim, f"refineAnim{i}")

    def start_success_animation(self) -> None:
        """Einmaliges Hüpfen in Grün."""
        self.stop_animating()
        self.current_animation = "success"
        self.set_bar_color(self._color_success)

        for i, bar in enumerate(self.bars):
            anim = self._create_height_animation(
                WAVE_BAR_MAX_HEIGHT * 0.8, 0.3, repeat=1
            )
            bar.addAnimation_forKey_(anim, f"successAnim{i}")

    def start_error_animation(self) -> None:
        """Kurzes rotes Aufblinken."""
        self.stop_animating()
        self.current_animation = "error"
        self.set_bar_color(self._color_error)

        for i, bar in enumerate(self.bars):
            anim = self._create_height_animation(WAVE_BAR_MAX_HEIGHT, 0.15, repeat=2)
            bar.addAnimation_forKey_(anim, f"errorAnim{i}")

    def stop_animating(self) -> None:
        """Stoppt alle Animationen."""
        # Auch wenn keine CABasicAnimations laufen, kann der Level-Timer aktiv sein.
        self._stop_level_timer()
        self.animations_running = False
        self.current_animation = None
        from AppKit import NSMakeRect  # type: ignore[import-not-found]

        for bar in self.bars:
            bar.removeAllAnimations()
            bar.setBounds_(NSMakeRect(0, 0, WAVE_BAR_WIDTH, WAVE_BAR_MIN_HEIGHT))
        self._last_heights = [WAVE_BAR_MIN_HEIGHT for _ in range(WAVE_BAR_COUNT)]
        self._agc_peak = WAVE_AGC_MIN_PEAK
        self._smoothed_level = 0.0
        self._target_level = 0.0

    def _start_level_timer(self) -> None:
        if self._level_timer is not None:
            return

        from Foundation import NSTimer  # type: ignore[import-not-found]

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

        interval = 1.0 / max(WAVE_ANIMATION_FPS, 1.0)
        self._level_timer = NSTimer.scheduledTimerWithTimeInterval_repeats_block_(
            interval, True, tick
        )

    def _stop_level_timer(self) -> None:
        if self._level_timer is None:
            return
        try:
            self._level_timer.invalidate()
        except Exception:
            pass
        self._level_timer = None

    def _render_level_frame(self) -> None:
        """Rendert einen Frame der Level-Visualisierung (läuft im Main-Thread via NSTimer)."""
        from AppKit import NSMakeRect  # type: ignore[import-not-found]
        from Quartz import CATransaction  # type: ignore[import-not-found]

        # Ziel-Level sanft verfolgen (reduziert Zittern durch RMS-Fluktuationen).
        prev_level = self._smoothed_level
        target_level = self._target_level
        alpha_level = (
            WAVE_LEVEL_SMOOTHING_RISE
            if target_level > prev_level
            else WAVE_LEVEL_SMOOTHING_FALL
        )
        level = _lerp(prev_level, target_level, alpha_level)
        self._smoothed_level = level

        now = time.perf_counter()
        phase_primary = 2 * math.pi * WAVE_WANDER_HZ_PRIMARY * now
        phase_secondary = 2 * math.pi * WAVE_WANDER_HZ_SECONDARY * now + 1.7

        # Envelope-Center driftet nach links/rechts (wie ein Paket).
        # Bewegung skaliert mit Level: bei Stille wenig, beim Sprechen deutlich.
        shift_strength = self._envelope_max_shift_base * _lerp(0.15, 1.0, level)
        env_primary = math.sin(
            2 * math.pi * WAVE_ENVELOPE_HZ_PRIMARY * now + self._envelope_phase_primary
        )
        env_secondary = math.sin(
            2 * math.pi * WAVE_ENVELOPE_HZ_SECONDARY * now + self._envelope_phase_secondary
        )
        env_mix = _lerp(env_secondary, env_primary, WAVE_ENVELOPE_BLEND)
        envelope_center = self._bar_center + shift_strength * env_mix
        envelope_center = _clamp(envelope_center, 0.0, WAVE_BAR_COUNT - 1)

        CATransaction.begin()
        CATransaction.setDisableActions_(True)

        base_height = WAVE_BAR_MIN_HEIGHT
        max_add = WAVE_BAR_MAX_HEIGHT - WAVE_BAR_MIN_HEIGHT

        # Kleine Baseline-Bewegung, damit es nicht "steht" bei leiser Sprache,
        # aber skaliert mit Level, damit es ruhig bleibt.
        wander_strength = WAVE_WANDER_AMOUNT * _lerp(0.20, 1.0, level)

        for i, bar in enumerate(self.bars):
            travel_primary = math.sin(phase_primary + self._wander_offset_primary[i])
            travel_secondary = math.sin(phase_secondary + self._wander_offset_secondary[i])
            travel = _lerp(travel_secondary, travel_primary, WAVE_WANDER_BLEND)
            wiggle = 1.0 + wander_strength * travel

            # Dynamische Envelope verschiebt den "Energie"-Schwerpunkt über die Balken.
            envelope = _gaussian(i - envelope_center, WAVE_ENVELOPE_SIGMA)
            envelope_factor = _lerp(WAVE_ENVELOPE_BASE, 1.0, envelope)

            base_factor = self._height_factors[i]
            height_factor = _lerp(base_factor, envelope_factor, WAVE_ENVELOPE_STRENGTH)

            height = base_height + (level * height_factor * wiggle * max_add)
            height = _clamp(height, WAVE_BAR_MIN_HEIGHT, WAVE_BAR_MAX_HEIGHT)

            prev_height = self._last_heights[i]
            alpha = (
                WAVE_SMOOTHING_ALPHA_RISE
                if height > prev_height
                else WAVE_SMOOTHING_ALPHA_FALL
            )
            smoothed_height = _lerp(prev_height, height, alpha)

            bar.setBounds_(NSMakeRect(0, 0, WAVE_BAR_WIDTH, smoothed_height))
            self._last_heights[i] = smoothed_height

        CATransaction.commit()


class OverlayController:
    """
    Overlay-Fenster für Status und Interim-Text (aus overlay.py).

    Zeigt animiertes Overlay am unteren Bildschirmrand.
    Kein Polling - wird direkt via Callback aktualisiert.
    """

    def __init__(self):
        from AppKit import (  # type: ignore[import-not-found]
            NSBackingStoreBuffered,
            NSColor,
            NSFont,
            NSFontWeightSemibold,
            NSMakeRect,
            NSScreen,
            NSTextField,
            NSTextAlignmentCenter,
            NSVisualEffectView,
            NSWindow,
            NSWindowStyleMaskBorderless,
        )

        screen = NSScreen.mainScreen()
        if not screen:
            self.window = None
            return

        screen_frame = screen.frame()
        width = OVERLAY_MIN_WIDTH
        height = OVERLAY_HEIGHT
        x = (screen_frame.size.width - width) / 2
        y = OVERLAY_MARGIN_BOTTOM

        # Fenster erstellen
        self.window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(x, y, width, height),
            NSWindowStyleMaskBorderless,
            NSBackingStoreBuffered,
            False,
        )
        self.window.setLevel_(OVERLAY_WINDOW_LEVEL)
        self.window.setIgnoresMouseEvents_(True)
        self.window.setOpaque_(False)
        self.window.setBackgroundColor_(NSColor.clearColor())
        self.window.setAlphaValue_(0.0)
        self.window.setHasShadow_(True)

        # Visual Effect View (Blur)
        self._visual_effect_view = NSVisualEffectView.alloc().initWithFrame_(
            NSMakeRect(0, 0, width, height)
        )
        self._visual_effect_view.setMaterial_(13)  # HUD Window
        self._visual_effect_view.setBlendingMode_(0)  # Behind Window
        self._visual_effect_view.setState_(1)  # Active
        self._visual_effect_view.setWantsLayer_(True)
        self._visual_effect_view.layer().setCornerRadius_(OVERLAY_CORNER_RADIUS)
        self._visual_effect_view.layer().setMasksToBounds_(True)
        self._visual_effect_view.layer().setBorderWidth_(1.0)
        self._visual_effect_view.layer().setBorderColor_(
            NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.15).CGColor()
        )
        self.window.setContentView_(self._visual_effect_view)

        # Schallwellen-View
        wave_y = height - (WAVE_BAR_MAX_HEIGHT + 12)
        wave_x = (width - WAVE_AREA_WIDTH) / 2
        self._wave_view = SoundWaveView(
            NSMakeRect(wave_x, wave_y, WAVE_AREA_WIDTH, WAVE_BAR_MAX_HEIGHT)
        )
        self._visual_effect_view.addSubview_(self._wave_view.view)

        # Text-Feld
        self._text_field = NSTextField.alloc().initWithFrame_(
            NSMakeRect(
                OVERLAY_PADDING_H,
                16,
                width - 2 * OVERLAY_PADDING_H,
                OVERLAY_TEXT_FIELD_HEIGHT,
            )
        )
        self._text_field.setStringValue_("")
        self._text_field.setBezeled_(False)
        self._text_field.setDrawsBackground_(False)
        self._text_field.setEditable_(False)
        self._text_field.setSelectable_(False)
        self._text_field.setAlignment_(NSTextAlignmentCenter)
        self._text_field.cell().setLineBreakMode_(4)  # Truncate Tail
        self._text_field.setTextColor_(
            NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.95)
        )
        self._text_field.setFont_(
            NSFont.systemFontOfSize_weight_(OVERLAY_FONT_SIZE, NSFontWeightSemibold)
        )
        self._visual_effect_view.addSubview_(self._text_field)

        # State
        self._target_alpha = 0.0
        self._current_state = AppState.IDLE
        self._state_timestamp = 0.0
        self._feedback_timer = None

    def update_state(self, state: AppState, text: str | None = None) -> None:
        """Aktualisiert Overlay basierend auf State."""
        if not self.window:
            return

        from AppKit import (  # type: ignore[import-not-found]
            NSColor,
            NSFont,
            NSFontWeightMedium,
            NSFontWeightSemibold,
            NSFontWeightLight,
            NSFontManager,
            NSFontItalicTrait,
        )

        self._current_state = state

        if state == AppState.LISTENING:
            self._wave_view.start_listening_animation()
            self._text_field.setStringValue_("Listening ...")
            self._text_field.setFont_(
                NSFont.systemFontOfSize_weight_(
                    OVERLAY_FONT_SIZE, NSFontWeightMedium
                )
            )
            self._text_field.setTextColor_(
                NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.6)
            )
            self._fade_in()

        elif state == AppState.RECORDING:
            if text:
                self._wave_view.start_recording_animation()
                display_text = f"{text} ..."

                # Ghost-Look: Light + Italic + Reduced Opacity
                font = NSFont.systemFontOfSize_weight_(
                    OVERLAY_FONT_SIZE, NSFontWeightLight
                )
                italic_font = NSFontManager.sharedFontManager().convertFont_toHaveTrait_(
                    font, NSFontItalicTrait
                )
                self._text_field.setFont_(italic_font)

                self._text_field.setTextColor_(
                    NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.5)
                )
            else:
                # Fallback falls Recording ohne Text (sollte eigentlich Visualisierung haben)
                self._wave_view.start_recording_animation()
                display_text = "Recording ..."
                self._text_field.setFont_(
                    NSFont.systemFontOfSize_weight_(
                        OVERLAY_FONT_SIZE, NSFontWeightMedium
                    )
                )
                self._text_field.setTextColor_(
                    NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.9)
                )
            self._text_field.setStringValue_(display_text)
            self._fade_in()

        elif state == AppState.TRANSCRIBING:
            self._wave_view.start_transcribing_animation()
            self._text_field.setStringValue_("Transcribing ...")
            self._text_field.setTextColor_(
                NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.6)
            )
            self._fade_in()

        elif state == AppState.REFINING:
            self._wave_view.start_refining_animation()
            self._text_field.setStringValue_("Refining ...")
            self._text_field.setTextColor_(
                NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.6)
            )
            self._fade_in()

        elif state == AppState.DONE:
            self._wave_view.start_success_animation()
            
            # Show actual text if available
            if text:
                display_text = text.replace("\n", " ").strip()
            else:
                display_text = "Done"
                
            self._text_field.setStringValue_(display_text)
            self._text_field.setTextColor_(
                NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.95)
            )
            self._text_field.setFont_(
                NSFont.systemFontOfSize_weight_(OVERLAY_FONT_SIZE, NSFontWeightSemibold)
            )
            self._fade_in()
            self._start_fade_out_timer()

        elif state == AppState.ERROR:
            self._wave_view.start_error_animation()
            self._text_field.setStringValue_("Error")
            self._text_field.setTextColor_(_get_overlay_color(255, 71, 87))
            self._text_field.setFont_(
                NSFont.systemFontOfSize_weight_(OVERLAY_FONT_SIZE, NSFontWeightSemibold)
            )
            self._fade_in()
            self._start_fade_out_timer()

        else:  # idle
            self._wave_view.stop_animating()
            self._fade_out()

    def _fade_in(self) -> None:
        """Blendet Overlay ein."""
        if self._target_alpha != OVERLAY_ALPHA:
            self._target_alpha = OVERLAY_ALPHA
            self.window.orderFront_(None)
            self.window.animator().setAlphaValue_(OVERLAY_ALPHA)

    def _fade_out(self) -> None:
        """Blendet Overlay aus."""
        if self._target_alpha != 0.0:
            self._target_alpha = 0.0
            self.window.animator().setAlphaValue_(0.0)

    def _start_fade_out_timer(self) -> None:
        """Startet Timer für automatisches Ausblenden nach Done/Error."""
        from Foundation import NSTimer  # type: ignore[import-not-found]

        if self._feedback_timer:
            self._feedback_timer.invalidate()

        def fade_out_callback(_timer):
            self._fade_out()
            self._feedback_timer = None

        self._feedback_timer = NSTimer.scheduledTimerWithTimeInterval_repeats_block_(
            FEEDBACK_DISPLAY_DURATION, False, fade_out_callback
        )

    def update_audio_level(self, level: float) -> None:
        """Aktualisiert Audio-Visualisierung (Echtzeit)."""
        if self._current_state == AppState.RECORDING:
            self._wave_view.update_levels(level)
