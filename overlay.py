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

from AppKit import (
    NSApplication,
    NSBackingStoreBuffered,
    NSBezierPath,
    NSColor,
    NSFont,
    NSFontWeightMedium,
    NSMakeRect,
    NSScreen,
    NSTextField,
    NSTextAlignmentCenter,
    NSView,
    NSVisualEffectView,
    NSWindow,
    NSWindowStyleMaskBorderless,
)
from Foundation import NSObject, NSTimer
from objc import super  # noqa: A004 - PyObjC braucht das
from Quartz import (
    CABasicAnimation,
    CAMediaTimingFunction,
    kCAMediaTimingFunctionEaseInEaseOut,
)

# NSVisualEffectMaterial Konstanten (PyObjC exportiert diese nicht direkt)
NS_VISUAL_EFFECT_MATERIAL_HUD_WINDOW = 13
NS_VISUAL_EFFECT_BLENDING_MODE_BEHIND_WINDOW = 0
NS_VISUAL_EFFECT_STATE_ACTIVE = 1

# IPC-Dateien (synchron mit transcribe.py)
STATE_FILE = Path("/tmp/whisper_go.state")
INTERIM_FILE = Path("/tmp/whisper_go.interim")

# Konfiguration
POLL_INTERVAL = 0.2  # Sekunden
OVERLAY_MIN_WIDTH = 280
OVERLAY_MAX_WIDTH_RATIO = 0.7  # Max 70% der Bildschirmbreite
OVERLAY_HEIGHT = 82  # Höher für vertikales Layout
OVERLAY_MARGIN_BOTTOM = 100
OVERLAY_CORNER_RADIUS = 14
OVERLAY_PADDING_H = 20
OVERLAY_PADDING_V = 18  # Mehr Padding oben/unten
OVERLAY_ALPHA = 0.9
FONT_SIZE = 13
MAX_TEXT_LENGTH = 120
TEXT_FIELD_HEIGHT = 20  # Höhe des Textfelds

# Schallwellen-Konfiguration (größer)
WAVE_BAR_COUNT = 5
WAVE_BAR_WIDTH = 4
WAVE_BAR_GAP = 4
WAVE_BAR_MIN_HEIGHT = 6
WAVE_BAR_MAX_HEIGHT = 28
WAVE_AREA_WIDTH = WAVE_BAR_COUNT * WAVE_BAR_WIDTH + (WAVE_BAR_COUNT - 1) * WAVE_BAR_GAP

# Window-Level für Always-on-Top
OVERLAY_WINDOW_LEVEL = 25


def truncate_text(text: str, max_length: int = MAX_TEXT_LENGTH) -> str:
    """Kürzt Text für Overlay-Anzeige."""
    text = text.strip()
    if len(text) <= max_length:
        return text
    return text[:max_length].rstrip() + "…"


class SoundWaveView(NSView):
    """Animierte Schallwellen-Visualisierung."""

    def initWithFrame_(self, frame):
        self = super().initWithFrame_(frame)
        if self:
            self.setWantsLayer_(True)
            self.bars = []
            self.animations_running = False
            self._setup_bars()
        return self

    def _setup_bars(self):
        """Erstellt die Balken für die Schallwellen."""
        frame = self.frame()
        center_y = frame.size.height / 2

        for i in range(WAVE_BAR_COUNT):
            x = i * (WAVE_BAR_WIDTH + WAVE_BAR_GAP)

            # Balken als CALayer
            bar = (
                self.layer().sublayers()[i]
                if self.layer().sublayers() and i < len(self.layer().sublayers())
                else None
            )

            if not bar:
                from Quartz import CALayer

                bar = CALayer.alloc().init()
                self.layer().addSublayer_(bar)

            bar.setBackgroundColor_(
                NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.9).CGColor()
            )
            bar.setCornerRadius_(WAVE_BAR_WIDTH / 2)

            # Initiale Position (zentriert)
            initial_height = WAVE_BAR_MIN_HEIGHT
            bar.setFrame_(
                ((x, center_y - initial_height / 2), (WAVE_BAR_WIDTH, initial_height))
            )

            self.bars.append(bar)

    def startAnimating(self):
        """Startet die Schallwellen-Animation."""
        if self.animations_running:
            return

        self.animations_running = True
        frame = self.frame()
        center_y = frame.size.height / 2

        # Verschiedene Animationszeiten für jeden Balken
        durations = [0.3, 0.4, 0.35, 0.45]
        delays = [0.0, 0.1, 0.05, 0.15]

        for i, bar in enumerate(self.bars):
            duration = durations[i % len(durations)]
            delay = delays[i % len(delays)]

            # Höhen-Animation
            height_anim = CABasicAnimation.animationWithKeyPath_("bounds.size.height")
            height_anim.setFromValue_(WAVE_BAR_MIN_HEIGHT)
            height_anim.setToValue_(WAVE_BAR_MAX_HEIGHT)
            height_anim.setDuration_(duration)
            height_anim.setBeginTime_(
                bar.convertTime_fromLayer_(
                    CABasicAnimation.alloc().init().beginTime(), None
                )
                + delay
            )
            height_anim.setRepeatCount_(float("inf"))
            height_anim.setAutoreverses_(True)
            height_anim.setTimingFunction_(
                CAMediaTimingFunction.functionWithName_(
                    kCAMediaTimingFunctionEaseInEaseOut
                )
            )

            # Y-Position Animation (um zentriert zu bleiben)
            y_anim = CABasicAnimation.animationWithKeyPath_("position.y")
            y_anim.setFromValue_(center_y)
            y_anim.setToValue_(center_y)
            y_anim.setDuration_(duration)
            y_anim.setBeginTime_(
                bar.convertTime_fromLayer_(
                    CABasicAnimation.alloc().init().beginTime(), None
                )
                + delay
            )
            y_anim.setRepeatCount_(float("inf"))
            y_anim.setAutoreverses_(True)

            bar.addAnimation_forKey_(height_anim, f"heightAnim{i}")
            bar.addAnimation_forKey_(y_anim, f"yAnim{i}")

    def stopAnimating(self):
        """Stoppt die Animation."""
        if not self.animations_running:
            return

        self.animations_running = False
        for bar in self.bars:
            bar.removeAllAnimations()

    def drawRect_(self, rect):
        """Zeichnet die Balken manuell (Fallback ohne Animation)."""
        if self.animations_running:
            return  # Animation läuft via CALayer

        frame = self.frame()
        center_y = frame.size.height / 2

        NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.9).setFill()

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
            self.last_interim = None  # Letzter Interim-Text (für Pausen)
            self.is_visible = False
            self._setup_window()
            self._setup_timer()
        return self

    def _setup_window(self):
        """Erstellt das Overlay-Fenster mit Vibrancy-Effekt."""
        screen = NSScreen.mainScreen()
        if not screen:
            return

        screen_frame = screen.frame()

        # Initiale Größe
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

        # Overlay-Eigenschaften
        self.window.setLevel_(OVERLAY_WINDOW_LEVEL)
        self.window.setIgnoresMouseEvents_(True)
        self.window.setOpaque_(False)
        self.window.setBackgroundColor_(NSColor.clearColor())
        self.window.setAlphaValue_(OVERLAY_ALPHA)
        self.window.setHasShadow_(True)

        # NSVisualEffectView für Blur-Effekt
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

        self.window.setContentView_(self.visual_effect_view)

        # Layout: Schallwelle oben zentriert, Text darunter
        wave_area_y = OVERLAY_PADDING_V + TEXT_FIELD_HEIGHT + 6  # Über dem Text

        # Schallwellen-View (oben zentriert)
        wave_x = (width - WAVE_AREA_WIDTH) / 2
        self.wave_view = SoundWaveView.alloc().initWithFrame_(
            NSMakeRect(wave_x, wave_area_y, WAVE_AREA_WIDTH, WAVE_BAR_MAX_HEIGHT)
        )
        self.visual_effect_view.addSubview_(self.wave_view)

        # Textfeld (unten zentriert)
        self.text_field = NSTextField.alloc().initWithFrame_(
            NSMakeRect(
                OVERLAY_PADDING_H,
                OVERLAY_PADDING_V,
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
        self.text_field.setTextColor_(
            NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.85)
        )
        self.text_field.setFont_(
            NSFont.systemFontOfSize_weight_(FONT_SIZE, NSFontWeightMedium)
        )

        self.visual_effect_view.addSubview_(self.text_field)

    def _setup_timer(self):
        """Startet den Polling-Timer."""
        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            POLL_INTERVAL,
            self,
            "pollState:",
            None,
            True,
        )

    def _resize_window_for_text(self, text: str):
        """Passt Fenstergröße dynamisch an Textlänge an."""
        screen = NSScreen.mainScreen()
        if not screen:
            return

        screen_frame = screen.frame()

        # Breite berechnen: Text + Padding (vertikales Layout)
        text_width = len(text) * 7.5  # ~7.5px pro Zeichen bei Font 13
        content_width = text_width + 2 * OVERLAY_PADDING_H

        # Mindestens so breit wie Schallwellen + Padding
        min_for_wave = WAVE_AREA_WIDTH + 2 * OVERLAY_PADDING_H + 40

        # Begrenzen auf min/max
        max_width = screen_frame.size.width * OVERLAY_MAX_WIDTH_RATIO
        width = max(OVERLAY_MIN_WIDTH, min_for_wave, min(content_width, max_width))
        height = OVERLAY_HEIGHT

        # Zentriert positionieren
        x = (screen_frame.size.width - width) / 2
        y = OVERLAY_MARGIN_BOTTOM

        # Fenster und Views anpassen
        self.window.setFrame_display_(NSMakeRect(x, y, width, height), True)
        self.visual_effect_view.setFrame_(NSMakeRect(0, 0, width, height))

        # Schallwellen zentrieren
        wave_x = (width - WAVE_AREA_WIDTH) / 2
        wave_area_y = OVERLAY_PADDING_V + TEXT_FIELD_HEIGHT + 6
        self.wave_view.setFrame_(
            NSMakeRect(wave_x, wave_area_y, WAVE_AREA_WIDTH, WAVE_BAR_MAX_HEIGHT)
        )

        # Textfeld anpassen
        self.text_field.setFrame_(
            NSMakeRect(
                OVERLAY_PADDING_H,
                OVERLAY_PADDING_V,
                width - 2 * OVERLAY_PADDING_H,
                TEXT_FIELD_HEIGHT,
            )
        )

    def _show(self):
        """Zeigt das Overlay und startet Animation."""
        if not self.is_visible:
            self.is_visible = True
            self.wave_view.startAnimating()
            self.window.orderFront_(None)

    def _hide(self):
        """Versteckt das Overlay und stoppt Animation."""
        if self.is_visible:
            self.is_visible = False
            self.wave_view.stopAnimating()
            self.window.orderOut_(None)

    def pollState_(self, timer):
        """Liest State und Interim-Text, aktualisiert Overlay."""
        state = self._read_state()
        interim_text = self._read_interim()

        # Neuen Text bestimmen
        if state == "recording":
            if interim_text:
                # Neuer Interim-Text → speichern und anzeigen
                self.last_interim = interim_text
                new_text = f"{truncate_text(interim_text)} ..."
            elif self.last_interim:
                # Sprechpause → letzten Text behalten
                new_text = f"{truncate_text(self.last_interim)} ..."
            else:
                # Noch kein Text → Listening anzeigen
                new_text = "Listening ..."
        elif state == "transcribing":
            new_text = "Transcribing ..."
            self.last_interim = None  # Reset für nächste Aufnahme
        else:
            new_text = None
            self.last_interim = None  # Reset

        # Nur aktualisieren wenn sich Text geändert hat
        if new_text != self.last_text:
            self.last_text = new_text
            if new_text is not None:  # Auch leerer String zeigt Overlay
                self._resize_window_for_text(new_text if new_text else "Recording")
                self.text_field.setStringValue_(new_text)
                self._show()
            else:
                self._hide()

    def _read_state(self) -> str:
        """Liest aktuellen State aus IPC-Datei."""
        try:
            state = STATE_FILE.read_text().strip()
            return state if state else "idle"
        except FileNotFoundError:
            return "idle"
        except OSError:
            return "idle"

    def _read_interim(self) -> str | None:
        """Liest aktuellen Interim-Text."""
        try:
            text = INTERIM_FILE.read_text().strip()
            return text or None
        except FileNotFoundError:
            return None
        except OSError:
            return None


def main():
    """Startet die Overlay-App."""
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(1)  # NSApplicationActivationPolicyAccessory

    overlay = WhisperOverlay.alloc().init()  # noqa: F841

    app.run()


if __name__ == "__main__":
    main()
