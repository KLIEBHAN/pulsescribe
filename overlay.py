#!/usr/bin/env python3
"""
overlay.py – Untertitel-Overlay für whisper_go

Zeigt Interim-Results als elegantes Overlay am unteren Bildschirmrand
mit animierter Schallwellen-Visualisierung.

Nutzung:
    python overlay.py

Voraussetzung:
    PyObjC (bereits für NSWorkspace installiert)
"""

from pathlib import Path
import time

from AppKit import (
    NSApplication,
    NSBackingStoreBuffered,
    NSBezierPath,
    NSColor,
    NSFont,
    NSFontAttributeName,
    NSFontWeightMedium,
    NSFontWeightRegular,  # Standard-Gewichtung
    NSFontWeightSemibold,  # Fetter für bessere Lesbarkeit
    NSMakeRect,
    NSScreen,
    NSTextField,
    NSTextAlignmentCenter,
    NSView,
    NSVisualEffectView,
    NSWindow,
    NSWindowStyleMaskBorderless,
)
from Foundation import NSObject, NSString, NSTimer
from objc import super  # noqa: A004 - PyObjC braucht das
from Quartz import (
    CABasicAnimation,
    CAMediaTimingFunction,
    kCAMediaTimingFunctionEaseInEaseOut,
)

# NSVisualEffectMaterial Konstanten
NS_VISUAL_EFFECT_MATERIAL_HUD_WINDOW = 13
NS_VISUAL_EFFECT_BLENDING_MODE_BEHIND_WINDOW = 0
NS_VISUAL_EFFECT_STATE_ACTIVE = 1

# IPC-Dateien (synchron mit transcribe.py)
STATE_FILE = Path("/tmp/whisper_go.state")
INTERIM_FILE = Path("/tmp/whisper_go.interim")

# Konfiguration - MODERN VERTICAL LAYOUT
POLL_INTERVAL = 0.2
OVERLAY_MIN_WIDTH = 260
OVERLAY_MAX_WIDTH_RATIO = 0.75
OVERLAY_HEIGHT = 100
OVERLAY_MARGIN_BOTTOM = 110
OVERLAY_CORNER_RADIUS = 22
OVERLAY_PADDING_H = 24
OVERLAY_ALPHA = 0.95
FONT_SIZE = 15
TEXT_FIELD_HEIGHT = 24

# Schallwellen-Konfiguration
WAVE_BAR_COUNT = 5
WAVE_BAR_WIDTH = 4
WAVE_BAR_GAP = 5
WAVE_BAR_MIN_HEIGHT = 8
WAVE_BAR_MAX_HEIGHT = 32
WAVE_AREA_WIDTH = WAVE_BAR_COUNT * WAVE_BAR_WIDTH + (WAVE_BAR_COUNT - 1) * WAVE_BAR_GAP

OVERLAY_WINDOW_LEVEL = 25

# Feedback Timing
FEEDBACK_DISPLAY_DURATION = 2.0  # Sekunden für Done/Error
FEEDBACK_FADE_START = 1.5  # Fade beginnt nach X Sekunden


# Design Colors (P3 Display optimized)
def get_custom_color(r, g, b, a=1.0):
    return NSColor.colorWithSRGBRed_green_blue_alpha_(
        r / 255.0, g / 255.0, b / 255.0, a
    )


COLOR_IDLE = NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.9)
COLOR_RECORDING = get_custom_color(255, 82, 82)  # Soft Red
COLOR_TRANSCRIBING = get_custom_color(255, 177, 66)  # Soft Orange
COLOR_SUCCESS = get_custom_color(51, 217, 178)  # Soft Teal/Green
COLOR_ERROR = get_custom_color(255, 71, 87)  # Bright Red


def create_height_animation(
    bar, to_height, duration, delay=0, repeat=float("inf"), ease=True
):
    """Erstellt eine Höhen-Animation für einen CALayer-Balken."""
    anim = CABasicAnimation.animationWithKeyPath_("bounds.size.height")
    anim.setFromValue_(WAVE_BAR_MIN_HEIGHT)
    anim.setToValue_(to_height)
    anim.setDuration_(duration)
    anim.setAutoreverses_(True)
    anim.setRepeatCount_(repeat)
    if delay > 0:
        anim.setBeginTime_(
            bar.convertTime_fromLayer_(
                CABasicAnimation.alloc().init().beginTime(), None
            )
            + delay
        )
    if ease:
        anim.setTimingFunction_(
            CAMediaTimingFunction.functionWithName_(kCAMediaTimingFunctionEaseInEaseOut)
        )
    return anim


class SoundWaveView(NSView):
    """Animierte Schallwellen-Visualisierung."""

    def initWithFrame_(self, frame):
        self = super().initWithFrame_(frame)
        if self:
            self.setWantsLayer_(True)
            self.bars = []
            self.animations_running = False
            self.current_animation = None
            self._setup_bars()
        return self

    def _setup_bars(self):
        """Erstellt die Balken für die Schallwellen."""
        from Quartz import CALayer

        frame = self.frame()
        center_y = frame.size.height / 2

        for i in range(WAVE_BAR_COUNT):
            x = i * (WAVE_BAR_WIDTH + WAVE_BAR_GAP)

            bar = CALayer.alloc().init()
            bar.setBackgroundColor_(COLOR_IDLE.CGColor())
            bar.setCornerRadius_(WAVE_BAR_WIDTH / 2)
            bar.setFrame_(
                (
                    (x, center_y - WAVE_BAR_MIN_HEIGHT / 2),
                    (WAVE_BAR_WIDTH, WAVE_BAR_MIN_HEIGHT),
                )
            )

            self.layer().addSublayer_(bar)
            self.bars.append(bar)

    def setBarColor_(self, ns_color):
        """Setzt die Farbe der Balken."""
        cg_color = ns_color.CGColor()
        for bar in self.bars:
            bar.setBackgroundColor_(cg_color)

    def startRecordingAnimation(self):
        """Startet die organische Schallwellen-Animation (Sprechen)."""
        if self.current_animation == "recording":
            return

        self.stopAnimating()
        self.current_animation = "recording"
        self.animations_running = True

        # Organischere Animation mit leicht unterschiedlichen Timings
        durations = [0.42, 0.38, 0.45, 0.39, 0.41]
        delays = [0.0, 0.15, 0.3, 0.1, 0.25]

        for i, bar in enumerate(self.bars):
            anim = create_height_animation(
                bar, WAVE_BAR_MAX_HEIGHT, durations[i], delays[i]
            )
            bar.addAnimation_forKey_(anim, f"heightAnim{i}")

    def startTranscribingAnimation(self):
        """Startet eine 'Loading'-Welle (Transkribieren)."""
        if self.current_animation == "transcribing":
            return

        self.stopAnimating()
        self.current_animation = "transcribing"
        self.animations_running = True

        for i, bar in enumerate(self.bars):
            # Sequentielles Delay für Welleneffekt
            delay = (i / len(self.bars)) * 0.5
            anim = create_height_animation(
                bar, WAVE_BAR_MAX_HEIGHT * 0.7, duration=1.0, delay=delay
            )
            bar.addAnimation_forKey_(anim, f"transcribeAnim{i}")

    def startSuccessAnimation(self):
        """Einmaliges 'Hüpfen' in Grün für Erfolg."""
        self.stopAnimating()
        self.current_animation = "success"
        self.setBarColor_(COLOR_SUCCESS)

        for i, bar in enumerate(self.bars):
            anim = create_height_animation(
                bar,
                WAVE_BAR_MAX_HEIGHT * 0.8,
                duration=0.3,
                delay=i * 0.05,
                repeat=1,
                ease=False,
            )
            bar.addAnimation_forKey_(anim, f"successAnim{i}")

    def startErrorAnimation(self):
        """Kurzes rotes Aufblinken."""
        self.stopAnimating()
        self.current_animation = "error"
        self.setBarColor_(COLOR_ERROR)

        for i, bar in enumerate(self.bars):
            anim = create_height_animation(
                bar, WAVE_BAR_MAX_HEIGHT, duration=0.15, repeat=2, ease=False
            )
            bar.addAnimation_forKey_(anim, f"errorAnim{i}")

    def stopAnimating(self):
        """Stoppt alle Animationen."""
        if not self.animations_running:
            return

        self.animations_running = False
        self.current_animation = None
        for bar in self.bars:
            bar.removeAllAnimations()
            # Reset auf Ruheposition
            # Hinweis: Ohne explizites Setzen bleiben Layer manchmal im letzten State
            bar.setBounds_(NSMakeRect(0, 0, WAVE_BAR_WIDTH, WAVE_BAR_MIN_HEIGHT))

    def drawRect_(self, rect):
        """Zeichnet die Balken manuell (Fallback ohne Animation)."""
        if self.animations_running:
            return

        frame = self.frame()
        center_y = frame.size.height / 2

        COLOR_IDLE.setFill()

        for i in range(WAVE_BAR_COUNT):
            x = i * (WAVE_BAR_WIDTH + WAVE_BAR_GAP)
            height = WAVE_BAR_MIN_HEIGHT
            y = center_y - height / 2

            path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                NSMakeRect(x, y, WAVE_BAR_WIDTH, height),
                WAVE_BAR_WIDTH / 2,
                WAVE_BAR_WIDTH / 2,
            )
            path.fill()


class WhisperOverlay(NSObject):
    """Hauptklasse für das Untertitel-Overlay."""

    def init(self):
        self = super().init()
        if self:
            self.window = None
            self.text_field = None
            self.visual_effect_view = None
            self.wave_view = None
            self.last_text = None
            self.last_interim = None
            self.target_alpha = 0.0

            # State Tracking für Feedback
            self.current_state_file_value = None
            self.state_timestamp = 0.0
            self.breathing_active = False
            self.current_bar_color = None  # Cache für setBarColor_

            # mtime-Caching für File-I/O Optimierung
            self._state_mtime = 0.0
            self._state_cache = "idle"
            self._interim_mtime = 0.0
            self._interim_cache = None

            self._setup_window()
            self._setup_timer()
        return self

    def _setup_window(self):
        """Erstellt das Overlay-Fenster."""
        screen = NSScreen.mainScreen()
        if not screen:
            return

        screen_frame = screen.frame()

        width = OVERLAY_MIN_WIDTH
        height = OVERLAY_HEIGHT
        x = (screen_frame.size.width - width) / 2
        y = OVERLAY_MARGIN_BOTTOM

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
        self.window.setAlphaValue_(0.0)  # Start unsichtbar für Fade-In
        self.window.setHasShadow_(True)

        self.visual_effect_view = NSVisualEffectView.alloc().initWithFrame_(
            NSMakeRect(0, 0, width, height)
        )
        self.visual_effect_view.setMaterial_(NS_VISUAL_EFFECT_MATERIAL_HUD_WINDOW)
        self.visual_effect_view.setBlendingMode_(
            NS_VISUAL_EFFECT_BLENDING_MODE_BEHIND_WINDOW
        )
        self.visual_effect_view.setState_(NS_VISUAL_EFFECT_STATE_ACTIVE)
        self.visual_effect_view.setWantsLayer_(True)
        self.visual_effect_view.layer().setCornerRadius_(OVERLAY_CORNER_RADIUS)
        self.visual_effect_view.layer().setMasksToBounds_(True)

        # Premium "Glass" Border
        self.visual_effect_view.layer().setBorderWidth_(1.0)
        self.visual_effect_view.layer().setBorderColor_(
            NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.15).CGColor()
        )

        self.window.setContentView_(self.visual_effect_view)
        # VERTIKALES LAYOUT (REFINED)

        # 1. Schallwelle (Oben)
        # Position: Vertikal etwas nach oben verschoben für gute Balance
        wave_y = height - (WAVE_BAR_MAX_HEIGHT + 20)
        wave_x = (width - WAVE_AREA_WIDTH) / 2

        self.wave_view = SoundWaveView.alloc().initWithFrame_(
            NSMakeRect(wave_x, wave_y, WAVE_AREA_WIDTH, WAVE_BAR_MAX_HEIGHT)
        )
        self.visual_effect_view.addSubview_(self.wave_view)

        # 2. Text (Unten)
        # Position: Unter der Wave, aber nicht am Boden klebend
        text_y = 16  # Abstand von unten

        self.text_field = NSTextField.alloc().initWithFrame_(
            NSMakeRect(
                OVERLAY_PADDING_H,
                text_y,
                width - 2 * OVERLAY_PADDING_H,
                TEXT_FIELD_HEIGHT,
            )
        )
        self.text_field.setStringValue_("")
        self.text_field.setBezeled_(False)
        self.text_field.setDrawsBackground_(False)
        self.text_field.setEditable_(False)
        self.text_field.setSelectable_(False)
        self.text_field.setAlignment_(NSTextAlignmentCenter)
        # Native Truncation: NSLineBreakByTruncatingTail = 4
        self.text_field.cell().setLineBreakMode_(4)

        # Text Shadow (Subtil für Lesbarkeit)
        from AppKit import NSShadow

        text_shadow = NSShadow.alloc().init()
        text_shadow.setShadowOffset_((0, -1))
        text_shadow.setShadowBlurRadius_(2.0)
        text_shadow.setShadowColor_(NSColor.colorWithCalibratedWhite_alpha_(0.0, 0.3))
        self.text_field.setShadow_(text_shadow)

        # Helles Weiß für starken Kontrast
        self.text_field.setTextColor_(
            NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.95)
        )
        # Semibold Font
        self.text_field.setFont_(
            NSFont.systemFontOfSize_weight_(FONT_SIZE, NSFontWeightSemibold)
        )

        self.visual_effect_view.addSubview_(self.text_field)

    def _setup_timer(self):
        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            POLL_INTERVAL,
            self,
            "pollState:",
            None,
            True,
        )

    def _update_text_style(self, is_status: bool):
        """Passt Schriftart und Farbe basierend auf Text-Typ an."""
        if is_status:
            # Status: Sehr dezent (Regular, transparent)
            self.text_field.setFont_(
                NSFont.systemFontOfSize_weight_(FONT_SIZE, NSFontWeightRegular)
            )
            self.text_field.setTextColor_(
                NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.6)
            )
        else:
            # Inhalt: Gut lesbar, aber nicht zu fett (Medium)
            self.text_field.setFont_(
                NSFont.systemFontOfSize_weight_(FONT_SIZE, NSFontWeightMedium)
            )
            self.text_field.setTextColor_(
                NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.9)
            )

    def _resize_window_for_text(self, text: str):
        """Passt Fenstergröße dynamisch an."""
        screen = NSScreen.mainScreen()
        if not screen:
            return

        screen_frame = screen.frame()

        # Aktuellen Font des Textfelds für Messung nutzen
        font = self.text_field.font()
        attributes = {NSFontAttributeName: font}
        ns_text = NSString.stringWithString_(text)

        text_size = ns_text.sizeWithAttributes_(attributes)
        text_width = text_size.width

        # Extra Padding addieren
        content_width = text_width + 2 * OVERLAY_PADDING_H
        min_for_wave = WAVE_AREA_WIDTH + 2 * OVERLAY_PADDING_H + 60

        width = max(
            OVERLAY_MIN_WIDTH,
            min_for_wave,
            min(content_width, screen_frame.size.width * OVERLAY_MAX_WIDTH_RATIO),
        )
        height = OVERLAY_HEIGHT

        x = (screen_frame.size.width - width) / 2
        y = OVERLAY_MARGIN_BOTTOM

        # Fenster Update (Animiert für flüssige Größenänderung)
        self.window.animator().setFrame_display_(NSMakeRect(x, y, width, height), True)
        self.visual_effect_view.setFrame_(NSMakeRect(0, 0, width, height))

        # Wave zentrieren
        wave_x = (width - WAVE_AREA_WIDTH) / 2
        wave_y = height - (WAVE_BAR_MAX_HEIGHT + 20)
        self.wave_view.setFrame_(
            NSMakeRect(wave_x, wave_y, WAVE_AREA_WIDTH, WAVE_BAR_MAX_HEIGHT)
        )

        # Text zentrieren/breiter machen
        self.text_field.setFrame_(
            NSMakeRect(
                OVERLAY_PADDING_H,
                16,  # Fester Abstand von unten
                width - 2 * OVERLAY_PADDING_H,
                TEXT_FIELD_HEIGHT,
            )
        )

    def _startBreathingEffect(self):
        """Subtiler Puls-Effekt auf dem Shadow während Recording."""
        if self.breathing_active:
            return
        self.breathing_active = True

        layer = self.visual_effect_view.layer()
        # Shadow-Opacity pulsieren
        anim = CABasicAnimation.animationWithKeyPath_("shadowOpacity")
        anim.setFromValue_(0.2)
        anim.setToValue_(0.5)
        anim.setDuration_(1.2)
        anim.setAutoreverses_(True)
        anim.setRepeatCount_(float("inf"))
        anim.setTimingFunction_(
            CAMediaTimingFunction.functionWithName_(kCAMediaTimingFunctionEaseInEaseOut)
        )
        layer.setShadowOpacity_(0.3)
        layer.setShadowRadius_(12.0)
        layer.setShadowOffset_((0, -2))
        layer.setShadowColor_(COLOR_RECORDING.CGColor())
        layer.addAnimation_forKey_(anim, "breathingShadow")

    def _stopBreathingEffect(self):
        """Stoppt den Breathing-Effekt."""
        if not self.breathing_active:
            return
        self.breathing_active = False

        layer = self.visual_effect_view.layer()
        layer.removeAnimationForKey_("breathingShadow")
        layer.setShadowOpacity_(0.0)

    def _popIn(self):
        """Subtiler Scale-Effekt beim Erscheinen."""
        # Scale Animation für den Content View
        anim = CABasicAnimation.animationWithKeyPath_("transform.scale")
        anim.setFromValue_(0.95)
        anim.setToValue_(1.0)
        anim.setDuration_(0.25)
        anim.setTimingFunction_(
            CAMediaTimingFunction.functionWithName_(kCAMediaTimingFunctionEaseInEaseOut)
        )

        self.visual_effect_view.layer().addAnimation_forKey_(anim, "popIn")

    def _fadeIn(self):
        """Sanftes Einblenden."""
        if self.target_alpha != OVERLAY_ALPHA:
            was_hidden = self.target_alpha == 0.0
            self.target_alpha = OVERLAY_ALPHA

            self.window.orderFront_(None)
            self.window.animator().setAlphaValue_(OVERLAY_ALPHA)

            if was_hidden:
                self._popIn()

    def _fadeOut(self):
        """Sanftes Ausblenden."""
        if self.target_alpha != 0.0:
            self.target_alpha = 0.0
            self.window.animator().setAlphaValue_(0.0)
            # Hinweis: Wir rufen orderOut_ nicht sofort auf, damit der Fade sichtbar ist.
            # Bei Alpha 0 ist es effektiv unsichtbar und Klicks gehen durch (ignoresMouseEvents=True)

    def pollState_(self, timer):
        state = self._read_state()
        current_time = time.time()

        # State Change Detection für Feedback-Timer
        if state != self.current_state_file_value:
            self.current_state_file_value = state
            self.state_timestamp = current_time

        # 1. Feedback-Zustände (Done/Error)
        if state in ["done", "error"]:
            self._stopBreathingEffect()  # Recording-Effekt beenden
            elapsed = current_time - self.state_timestamp

            # Gradueller Fade-Out ab FEEDBACK_FADE_START
            if elapsed > FEEDBACK_DISPLAY_DURATION:
                self._fadeOut()
                return
            elif elapsed > FEEDBACK_FADE_START:
                # Sanfter Übergang: Linear von OVERLAY_ALPHA zu 0
                fade_progress = (elapsed - FEEDBACK_FADE_START) / (
                    FEEDBACK_DISPLAY_DURATION - FEEDBACK_FADE_START
                )
                new_alpha = OVERLAY_ALPHA * (1.0 - fade_progress)
                self.window.setAlphaValue_(new_alpha)
                return

            # Visualisierung anzeigen
            if state == "done" and self.wave_view.current_animation != "success":
                self.wave_view.startSuccessAnimation()
                self.text_field.setStringValue_("Done")
                self.text_field.setTextColor_(COLOR_SUCCESS)
                self.text_field.setFont_(
                    NSFont.systemFontOfSize_weight_(FONT_SIZE, NSFontWeightSemibold)
                )
                self._resize_window_for_text("Done")
                self._fadeIn()
            elif state == "error" and self.wave_view.current_animation != "error":
                self.wave_view.startErrorAnimation()
                self.text_field.setStringValue_("Error")
                self.text_field.setTextColor_(COLOR_ERROR)
                self.text_field.setFont_(
                    NSFont.systemFontOfSize_weight_(FONT_SIZE, NSFontWeightSemibold)
                )
                self._resize_window_for_text("Error")
                self._fadeIn()

            return  # Wichtig: Hier abbrechen, damit unten nichts überschrieben wird

        # 2. Aktive Zustände (Recording/Transcribing)
        is_status_msg = False
        new_text = None
        animation_mode = None
        target_bar_color = COLOR_IDLE

        if state == "recording":
            animation_mode = "recording"
            target_bar_color = COLOR_RECORDING

            # Lazy read: Interim nur bei Recording laden
            interim_text = self._read_interim()
            if interim_text:
                self.last_interim = interim_text
                new_text = f"{interim_text} ..."
                is_status_msg = False
            elif self.last_interim:
                new_text = "Loading ..."
                is_status_msg = True
            else:
                new_text = "Listening ..."
                is_status_msg = True

        elif state == "transcribing":
            animation_mode = "transcribing"
            target_bar_color = COLOR_TRANSCRIBING
            new_text = "Transcribing ..."
            self.last_interim = None
            is_status_msg = True

        else:  # idle
            new_text = None
            self.last_interim = None
            is_status_msg = True
            animation_mode = None

        # Bar Color nur bei Änderung setzen
        if target_bar_color != self.current_bar_color:
            self.current_bar_color = target_bar_color
            self.wave_view.setBarColor_(target_bar_color)

        # Animation Update
        if animation_mode == "recording":
            self.wave_view.startRecordingAnimation()
            self._startBreathingEffect()
        elif animation_mode == "transcribing":
            self.wave_view.startTranscribingAnimation()
            self._stopBreathingEffect()
        else:
            self.wave_view.stopAnimating()
            self._stopBreathingEffect()

        # Text & Visibility Update
        if (
            new_text != self.last_text or self.target_alpha == 0.0
        ):  # Auch updaten wenn wir gerade eingeblendet werden
            self.last_text = new_text
            if new_text is not None:
                self._update_text_style(is_status_msg)
                self._resize_window_for_text(new_text)
                self.text_field.setStringValue_(new_text)
                self._fadeIn()
            else:
                self._fadeOut()

    def _read_state(self) -> str:
        try:
            mtime = STATE_FILE.stat().st_mtime
            if mtime == self._state_mtime:
                return self._state_cache
            self._state_mtime = mtime
            self._state_cache = STATE_FILE.read_text().strip() or "idle"
            return self._state_cache
        except FileNotFoundError:
            self._state_mtime = 0.0
            self._state_cache = "idle"
            return "idle"
        except OSError:
            return self._state_cache

    def _read_interim(self) -> str | None:
        try:
            mtime = INTERIM_FILE.stat().st_mtime
            if mtime == self._interim_mtime:
                return self._interim_cache
            self._interim_mtime = mtime
            text = INTERIM_FILE.read_text().strip()
            self._interim_cache = text or None
            return self._interim_cache
        except FileNotFoundError:
            self._interim_mtime = 0.0
            self._interim_cache = None
            return None
        except OSError:
            return self._interim_cache


def main():
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(1)
    overlay = WhisperOverlay.alloc().init()  # noqa: F841
    app.run()


if __name__ == "__main__":
    main()
