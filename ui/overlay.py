"""Overlay-Controller und Visualisierung für whisper_go."""

# Overlay-Fenster Konfiguration
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

# Schallwellen-Visualisierung
WAVE_BAR_COUNT = 5             # Anzahl der animierten Balken
WAVE_BAR_WIDTH = 4             # Breite jedes Balkens in Pixel
WAVE_BAR_GAP = 5               # Abstand zwischen Balken
WAVE_BAR_MIN_HEIGHT = 8        # Ruhezustand-Höhe
WAVE_BAR_MAX_HEIGHT = 48       # Maximale Höhe bei voller Animation
WAVE_AREA_WIDTH = WAVE_BAR_COUNT * WAVE_BAR_WIDTH + (WAVE_BAR_COUNT - 1) * WAVE_BAR_GAP

# Feedback-Anzeigedauer
FEEDBACK_DISPLAY_DURATION = 0.8  # Sekunden für Done/Error-Anzeige

from config import VISUAL_NOISE_GATE, VISUAL_GAIN
from utils.state import AppState
import math

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
        Aktualisiert Balkenhöhe basierend auf Audio-Level (0.0 - 1.0).
        Ersetzt die 'start_recording_animation' Loop mit echten Daten.
        """
        # Falls noch Animationen laufen (z.B. Fallback-Welle), stoppen wir sie,
        # damit wir die Höhe manuell setzen können.
        if self.animations_running:
            self.stop_animating()

        if self.current_animation != "recording":
             self.current_animation = "recording"
             self.set_bar_color(self._color_recording)
        
        # Noise Gate für Visualisierung: Sehr leise Pegel ignorieren
        if level < VISUAL_NOISE_GATE:
             level = 0.0

        # Verstärkung für visuelle Sichtbarkeit mit nicht-linearer Kurve
        # sqrt(level) sorgt dafür, dass leise Töne stärker angehoben werden als laute
        amplified = min(math.sqrt(level) * VISUAL_GAIN, 1.0)
        
        # Balken-Mapping (Symmetrisch: 0-1-2-1-0)
        # Wir fügen etwas Randomness hinzu, damit es lebendig wirkt
        import random
        from AppKit import NSMakeRect # type: ignore[import-not-found]
        
        # Basis-Höhenfaktoren für die 5 Balken (Mitte höher)
        factors = [0.4, 0.7, 1.0, 0.7, 0.4]
        
        for i, bar in enumerate(self.bars):
            # Berechne Zielhöhe
            base_height = WAVE_BAR_MIN_HEIGHT
            max_add = WAVE_BAR_MAX_HEIGHT - WAVE_BAR_MIN_HEIGHT
            
            # Höhe = Min + (Level * Factor * MaxAdd) + Jitter
            height = base_height + (amplified * factors[i] * max_add)
            
            # Kleiner Jitter für Lebendigkeit
            jitter = random.uniform(-2, 2)
            height = max(WAVE_BAR_MIN_HEIGHT, min(WAVE_BAR_MAX_HEIGHT, height + jitter))
            
            # Disable implicit animations for direct update
            # CATransaction.begin() ... commit() wäre sauberer, aber overhead.
            # Wir setzen bounds direkt.
            
            # Da wir CALayer nutzen, ist bounds update normalerweise animiert (implicit).
            # Wir wollen aber schnelle Updates.
            # Man könnte Actions disablen, aber für 5 Balken bei ~20-50Hz ist es oft ok.
            
            # Zentrieren in Y
            # Frame origin ist unten links im Parent (wenn nicht anders transformiert)
            # Aber wir haben frame gesetzt. Bounds ändern ändert Size um AnchorPoint (Center).
            
            # Einfacher: Setze Bounds (Größe)
            bar.setBounds_(NSMakeRect(0, 0, WAVE_BAR_WIDTH, height))


    def start_recording_animation(self) -> None:
        """Startet organische Schallwellen-Animation (Fallback wenn keine Levels)."""
        if self.current_animation == "recording":
            return
        self.stop_animating()
        self.current_animation = "recording"
        self.animations_running = True
        self.set_bar_color(self._color_recording)

        durations = [0.42, 0.38, 0.45, 0.39, 0.41]
        for i, bar in enumerate(self.bars):
            anim = self._create_height_animation(WAVE_BAR_MAX_HEIGHT, durations[i])
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
        if not self.animations_running:
            return
        self.animations_running = False
        self.current_animation = None
        from AppKit import NSMakeRect  # type: ignore[import-not-found]

        for bar in self.bars:
            bar.removeAllAnimations()
            bar.setBounds_(NSMakeRect(0, 0, WAVE_BAR_WIDTH, WAVE_BAR_MIN_HEIGHT))


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
            NSFontWeightBold,
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
