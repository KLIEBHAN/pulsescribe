#!/usr/bin/env python3
"""
whisper_daemon.py – Unified Daemon für whisper_go.

Konsolidiert in einem Prozess:
- Hotkey-Listener (QuickMacHotKey, keine Accessibility nötig)
- Mikrofon-Aufnahme + Deepgram Streaming (wie run_daemon_mode_streaming)
- Menübar-Status (NSStatusBar)
- Overlay mit Animationen (NSWindow)
- LLM-Nachbearbeitung (optional)
- Auto-Paste (pynput/Quartz)

Architektur:
- Main Thread: NSApplication Event-Loop (QuickMacHotKey, Menübar, Overlay)
- Worker Thread: _deepgram_stream_core() mit external_stop_event

Usage:
    python whisper_daemon.py              # Mit Defaults aus .env
    python whisper_daemon.py --hotkey f19 # Hotkey überschreiben
"""

import logging
import os
import queue
import random
import sys
import threading
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

# IPC-Dateien für Interim-Text (Kompatibilität mit transcribe.py)
INTERIM_FILE = Path("/tmp/whisper_go.interim")

# =============================================================================
# Konfiguration
# =============================================================================

SCRIPT_DIR = Path(__file__).resolve().parent
LOG_DIR = SCRIPT_DIR / "logs"
LOG_FILE = LOG_DIR / "whisper_daemon.log"

# Timeouts
DEBOUNCE_INTERVAL = 0.3  # Ignoriere Hotkey-Events innerhalb 300ms

# =============================================================================
# Logging
# =============================================================================

logger = logging.getLogger("whisper_daemon")


def setup_logging(debug: bool = False) -> None:
    """Konfiguriert Logging mit Datei-Output."""
    # Verhindere doppelte Handler bei mehrfachem Aufruf
    if logger.handlers:
        return
    
    LOG_DIR.mkdir(exist_ok=True)

    logger.setLevel(logging.DEBUG if debug else logging.INFO)

    file_handler = RotatingFileHandler(
        LOG_FILE, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%H:%M:%S")
    )
    logger.addHandler(file_handler)

    if debug:
        stderr_handler = logging.StreamHandler(sys.stderr)
        stderr_handler.setLevel(logging.DEBUG)
        stderr_handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
        logger.addHandler(stderr_handler)


# =============================================================================
# Lazy Imports (DRY: Import statt Duplizieren)
# =============================================================================

_transcribe_module = None
_hotkey_module = None


def _get_transcribe():
    """Lazy Import für transcribe.py."""
    global _transcribe_module
    if _transcribe_module is None:
        import transcribe

        _transcribe_module = transcribe
    return _transcribe_module


def _get_hotkey():
    """Lazy Import für hotkey_daemon.py."""
    global _hotkey_module
    if _hotkey_module is None:
        import hotkey_daemon

        _hotkey_module = hotkey_daemon
    return _hotkey_module


# =============================================================================
# Menübar-Controller (Phase 2)
# =============================================================================

# Status-Icons für Menübar
MENUBAR_ICONS = {
    "idle": "🎤",
    "recording": "🔴",
    "transcribing": "⏳",
    "done": "✅",
    "error": "❌",
}


class MenuBarController:
    """
    Menübar-Status-Anzeige via NSStatusBar.

    Zeigt aktuellen State als Icon + optional Interim-Text.
    Kein Polling - wird direkt via Callback aktualisiert.
    """

    def __init__(self):
        from AppKit import NSStatusBar, NSVariableStatusItemLength  # type: ignore[import-not-found]

        self._status_bar = NSStatusBar.systemStatusBar()
        self._status_item = self._status_bar.statusItemWithLength_(
            NSVariableStatusItemLength
        )
        self._status_item.setTitle_(MENUBAR_ICONS["idle"])
        self._current_state = "idle"

    def update_state(self, state: str, interim_text: str | None = None) -> None:
        """Aktualisiert Menübar-Icon und optional Text."""
        self._current_state = state
        icon = MENUBAR_ICONS.get(state, MENUBAR_ICONS["idle"])

        if state == "recording" and interim_text:
            # Kürzen für Menübar
            preview = (
                interim_text[:20] + "…" if len(interim_text) > 20 else interim_text
            )
            self._status_item.setTitle_(f"{icon} {preview}")
        else:
            self._status_item.setTitle_(icon)


# =============================================================================
# Overlay-Controller (Phase 3)
# =============================================================================

# Overlay-Konfiguration
OVERLAY_MIN_WIDTH = 260
OVERLAY_MAX_WIDTH_RATIO = 0.75
OVERLAY_HEIGHT = 120
OVERLAY_MARGIN_BOTTOM = 110
OVERLAY_CORNER_RADIUS = 22
OVERLAY_PADDING_H = 24
OVERLAY_ALPHA = 0.95
OVERLAY_FONT_SIZE = 19
OVERLAY_TEXT_FIELD_HEIGHT = 30
OVERLAY_WINDOW_LEVEL = 25

# Schallwellen-Konfiguration
WAVE_BAR_COUNT = 19
WAVE_BAR_WIDTH = 3
WAVE_BAR_GAP = 3
WAVE_BAR_MIN_HEIGHT = 4
WAVE_BAR_MAX_HEIGHT = 42
WAVE_AREA_WIDTH = WAVE_BAR_COUNT * WAVE_BAR_WIDTH + (WAVE_BAR_COUNT - 1) * WAVE_BAR_GAP

# Animations-Parameter
ANIM_RECORDING_MIN_SCALE = 0.3    # Min Skalierung am Rand (vs Mitte)
ANIM_RECORDING_RANDOM_BASE = 0.4  # Zufalls-Basis für Höhe
ANIM_RECORDING_RANDOM_VAR = 0.6   # Zufalls-Variation für Höhe
ANIM_OPACITY_MIN_BASE = 0.6       # Minimale Opazität (Mitte)
ANIM_OPACITY_DROP_EDGE = 0.3      # Opazitäts-Abfall zum Rand
ANIM_OPACITY_MAX_BASE = 0.9       # Maximale Opazität (Basis)
ANIM_OPACITY_MAX_VAR = 0.1        # Maximale Opazität (Variation)

# Feedback Timing
FEEDBACK_DISPLAY_DURATION = 0.8


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
        self._color_recording = _get_overlay_color(255, 59, 48) # SF System Red
        self._color_transcribing = _get_overlay_color(255, 177, 66)
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

    def _create_basic_animation(
        self, key_path, from_val, to_val, duration, repeat=float("inf")
    ):
        """Generische Helper-Methode für Core Animation."""
        from Quartz import (  # type: ignore[import-not-found]
            CABasicAnimation,
            CAMediaTimingFunction,
            kCAMediaTimingFunctionEaseInEaseOut,
        )

        anim = CABasicAnimation.animationWithKeyPath_(key_path)
        if from_val is not None:
            anim.setFromValue_(from_val)
        anim.setToValue_(to_val)
        anim.setDuration_(duration)
        anim.setAutoreverses_(True)
        anim.setRepeatCount_(repeat)
        anim.setTimingFunction_(
            CAMediaTimingFunction.functionWithName_(kCAMediaTimingFunctionEaseInEaseOut)
        )
        return anim

    def start_recording_animation(self) -> None:
        """Startet organische Schallwellen-Animation mit Glow-Effekt."""
        if self.current_animation == "recording":
            return
        self.stop_animating()
        self.current_animation = "recording"
        self.animations_running = True
        self.set_bar_color(self._color_recording)

        center_idx = len(self.bars) // 2
        for i, bar in enumerate(self.bars):
            # Distanz zur Mitte (0.0 bis 1.0)
            dist = abs(i - center_idx) / (center_idx + 1)
            
            # Basis-Berechnungen (mit Konstanten)
            base_scale = 1.0 - (dist * (1.0 - ANIM_RECORDING_MIN_SCALE))
            random_scale = ANIM_RECORDING_RANDOM_BASE + (random.random() * ANIM_RECORDING_RANDOM_VAR)
            
            target_height = WAVE_BAR_MAX_HEIGHT * base_scale * random_scale
            target_height = max(WAVE_BAR_MIN_HEIGHT * 2, target_height)
            
            duration = 0.5 + (random.random() * 0.5)
            
            # 1. Höhen-Animation
            anim_h = self._create_basic_animation(
                "bounds.size.height", WAVE_BAR_MIN_HEIGHT, target_height, duration
            )
            time_offset = random.random()
            anim_h.setTimeOffset_(time_offset)
            bar.addAnimation_forKey_(anim_h, f"heightAnim{i}")

            # 2. Opazitäts-Animation ("Breathing")
            # Ränder sind transparenter, Mitte deckender
            min_opacity = ANIM_OPACITY_MIN_BASE - (dist * ANIM_OPACITY_DROP_EDGE)
            # Wenn der Balken hoch geht, wird er fast voll deckend
            max_opacity = ANIM_OPACITY_MAX_BASE + (random.random() * ANIM_OPACITY_MAX_VAR)
            
            anim_o = self._create_basic_animation(
                "opacity", min_opacity, max_opacity, duration
            )
            anim_o.setTimeOffset_(time_offset) # Synchron zur Höhe
            bar.addAnimation_forKey_(anim_o, f"opacityAnim{i}")

    def start_transcribing_animation(self) -> None:
        """Startet Loading-Wellen-Animation (Flow-Effekt)."""
        if self.current_animation == "transcribing":
            return
        self.stop_animating()
        self.current_animation = "transcribing"
        self.animations_running = True
        self.set_bar_color(self._color_transcribing)

        for i, bar in enumerate(self.bars):
            # Welle von links nach rechts
            anim = self._create_basic_animation(
                "bounds.size.height", WAVE_BAR_MIN_HEIGHT, WAVE_BAR_MAX_HEIGHT * 0.6, 0.6
            )
            
            # Zeitlicher Versatz erzeugt die Wellenbewegung
            offset = i * 0.08
            anim.setTimeOffset_(offset)
            
            bar.addAnimation_forKey_(anim, f"transcribeAnim{i}")

    def start_success_animation(self) -> None:
        """Einmaliges Hüpfen in Grün."""
        self.stop_animating()
        self.current_animation = "success"
        self.set_bar_color(self._color_success)

        for i, bar in enumerate(self.bars):
            anim = self._create_basic_animation(
                "bounds.size.height", 
                WAVE_BAR_MIN_HEIGHT, 
                WAVE_BAR_MAX_HEIGHT * 0.8, 
                0.3, 
                repeat=1
            )
            bar.addAnimation_forKey_(anim, f"successAnim{i}")

    def start_error_animation(self) -> None:
        """Kurzes rotes Aufblinken."""
        self.stop_animating()
        self.current_animation = "error"
        self.set_bar_color(self._color_error)

        for i, bar in enumerate(self.bars):
            anim = self._create_basic_animation(
                "bounds.size.height",
                WAVE_BAR_MIN_HEIGHT,
                WAVE_BAR_MAX_HEIGHT,
                0.15,
                repeat=2
            )
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
        # Welle noch etwas tiefer (Margin Top 20 -> 25)
        wave_y = height - (WAVE_BAR_MAX_HEIGHT + 25)
        wave_x = (width - WAVE_AREA_WIDTH) / 2
        self._wave_view = SoundWaveView(
            NSMakeRect(wave_x, wave_y, WAVE_AREA_WIDTH, WAVE_BAR_MAX_HEIGHT)
        )
        self._visual_effect_view.addSubview_(self._wave_view.view)

        # Text-Feld
        # Text noch etwas höher (y 18 -> 20)
        self._text_field = NSTextField.alloc().initWithFrame_(
            NSMakeRect(
                OVERLAY_PADDING_H,
                20,
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
            self._get_font(OVERLAY_FONT_SIZE, NSFontWeightSemibold)
        )
        self._visual_effect_view.addSubview_(self._text_field)

        # State
        self._target_alpha = 0.0
        self._current_state = "idle"
        self._state_timestamp = 0.0
        self._feedback_timer = None

    def _get_font(self, size: float, weight):
        """Erstellt Font mit 'Rounded' Design falls verfügbar."""
        from AppKit import NSFont, NSFontDescriptor  # type: ignore[import-not-found]
        
        # Basis-Font
        font = NSFont.systemFontOfSize_weight_(size, weight)
        
        try:
            # Versuche "Rounded" Design (moderne macOS Optik)
            # NSFontDescriptorSystemDesignRounded ist ein String "rounded"
            desc = font.fontDescriptor().fontDescriptorWithDesign_("rounded")
            if desc:
                return NSFont.fontWithDescriptor_size_(desc, size)
        except Exception:
            pass
            
        return font

    def update_state(self, state: str, interim_text: str | None = None) -> None:
        """Aktualisiert Overlay basierend auf State."""
        if not self.window:
            return

        from AppKit import NSColor, NSFont, NSFontWeightMedium, NSFontWeightSemibold  # type: ignore[import-not-found]

        self._current_state = state

        if state == "recording":
            self._wave_view.start_recording_animation()
            if interim_text:
                text = f"{interim_text} ..."
                self._text_field.setFont_(
                    self._get_font(OVERLAY_FONT_SIZE, NSFontWeightMedium)
                )
                self._text_field.setTextColor_(
                    NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.95)
                )
            else:
                text = "Listening..."
                self._text_field.setFont_(
                    self._get_font(OVERLAY_FONT_SIZE, NSFontWeightMedium)
                )
                self._text_field.setTextColor_(
                    NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.7)
                )
            self._text_field.setStringValue_(text)
            self._fade_in()

        elif state == "transcribing":
            self._wave_view.start_transcribing_animation()
            self._text_field.setStringValue_("Thinking...")
            self._text_field.setTextColor_(
                NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.7)
            )
            self._fade_in()

        elif state == "done":
            self._wave_view.start_success_animation()
            self._text_field.setStringValue_("Done")
            self._text_field.setTextColor_(_get_overlay_color(51, 217, 178))
            self._text_field.setFont_(
                self._get_font(OVERLAY_FONT_SIZE, NSFontWeightSemibold)
            )
            self._fade_in()
            self._start_fade_out_timer()

        elif state == "error":
            self._wave_view.start_error_animation()
            self._text_field.setStringValue_("Error")
            self._text_field.setTextColor_(_get_overlay_color(255, 71, 87))
            self._text_field.setFont_(
                self._get_font(OVERLAY_FONT_SIZE, NSFontWeightSemibold)
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


# =============================================================================
# WhisperDaemon: Hauptklasse
# =============================================================================


class WhisperDaemon:
    """
    Unified Daemon für whisper_go.
    
    Architektur:
        Main-Thread: Hotkey-Listener (QuickMacHotKey) + UI-Updates
        Worker-Thread: Deepgram-Streaming (async)
    
    State-Flow:
        idle → [Hotkey] → recording → [Hotkey] → transcribing → done/error → idle
    """

    def __init__(
        self,
        hotkey: str = "f19",
        language: str | None = None,
        model: str | None = None,
        refine: bool = False,
        refine_model: str | None = None,
        refine_provider: str | None = None,
        context: str | None = None,
    ):
        self.hotkey = hotkey
        self.language = language
        self.model = model
        self.refine = refine
        self.refine_model = refine_model
        self.refine_provider = refine_provider
        self.context = context

        # State
        self._recording = False
        self._toggle_lock = threading.Lock()
        self._last_hotkey_time = 0.0
        self._current_state = "idle"

        # Stop-Event für _deepgram_stream_core
        self._stop_event: threading.Event | None = None

        # Worker-Thread für Streaming
        self._worker_thread: threading.Thread | None = None

        # Result-Queue für Transkripte
        self._result_queue: queue.Queue[str | Exception | None] = queue.Queue()

        # NSTimer für Result-Polling und Interim-Polling
        self._result_timer = None
        self._interim_timer = None
        self._last_interim_mtime = 0.0

        # UI-Controller (werden in run() initialisiert)
        self._menubar: MenuBarController | None = None
        self._overlay: OverlayController | None = None

    def _update_state(self, state: str, interim_text: str | None = None) -> None:
        """Aktualisiert State und benachrichtigt UI-Controller."""
        self._current_state = state
        logger.debug(
            f"State: {state}"
            + (f" interim='{interim_text[:20]}...'" if interim_text else "")
        )

        # UI-Controller aktualisieren
        if self._menubar:
            self._menubar.update_state(state, interim_text)
        if self._overlay:
            self._overlay.update_state(state, interim_text)

    def _on_hotkey(self) -> None:
        """Callback bei Hotkey-Aktivierung."""
        # Keyboard-Auto-Repeat und schnelle Doppelklicks ignorieren
        now = time.time()
        if now - self._last_hotkey_time < DEBOUNCE_INTERVAL:
            logger.debug("Debounce: Event ignoriert")
            return
        self._last_hotkey_time = now

        # Parallele Ausführung verhindern (non-blocking Lock)
        if not self._toggle_lock.acquire(blocking=False):
            logger.warning("Hotkey ignoriert - Toggle bereits aktiv")
            return

        try:
            logger.debug(f"Hotkey gedrückt! Recording={self._recording}")
            self._toggle_recording()
        finally:
            self._toggle_lock.release()

    def _toggle_recording(self) -> None:
        """Toggle-Mode: Start/Stop bei jedem Tastendruck."""
        if self._recording:
            self._stop_recording()
        else:
            self._start_recording()

    def _start_recording(self) -> None:
        """Startet Streaming-Aufnahme im Worker-Thread."""
        # Sicherstellen, dass kein alter Worker noch läuft
        if self._worker_thread is not None and self._worker_thread.is_alive():
            logger.warning("Alter Worker-Thread läuft noch, warte auf Beendigung...")
            if self._stop_event is not None:
                self._stop_event.set()
            self._worker_thread.join(timeout=2.0)
            if self._worker_thread.is_alive():
                logger.error("Worker-Thread konnte nicht beendet werden!")
            self._worker_thread = None
            self._stop_event = None

        self._recording = True
        self._update_state("recording")

        # Interim-Datei löschen, um veralteten Text zu vermeiden
        INTERIM_FILE.unlink(missing_ok=True)

        # Neues Stop-Event für diese Aufnahme
        self._stop_event = threading.Event()

        # Worker-Thread starten
        self._worker_thread = threading.Thread(
            target=self._streaming_worker,
            daemon=True,
            name="StreamingWorker",
        )
        self._worker_thread.start()

        # Interim-Polling starten (für Live-Preview)
        self._start_interim_polling()

        logger.info("Streaming gestartet (Worker-Thread)")

    def _start_interim_polling(self) -> None:
        """Startet NSTimer für Interim-Text-Polling."""
        from Foundation import NSTimer  # type: ignore[import-not-found]

        self._last_interim_mtime = 0.0

        def poll_interim() -> None:
            if self._current_state != "recording":
                return
            try:
                mtime = INTERIM_FILE.stat().st_mtime
                if mtime > self._last_interim_mtime:
                    self._last_interim_mtime = mtime
                    interim_text = INTERIM_FILE.read_text().strip()
                    if interim_text:
                        self._update_state("recording", interim_text)
            except FileNotFoundError:
                pass
            except OSError:
                pass

        self._interim_timer = NSTimer.scheduledTimerWithTimeInterval_repeats_block_(
            0.2, True, lambda _: poll_interim()
        )

    def _stop_interim_polling(self) -> None:
        """Stoppt Interim-Polling."""
        if self._interim_timer:
            self._interim_timer.invalidate()
            self._interim_timer = None

    def _streaming_worker(self) -> None:
        """
        Hintergrund-Thread für Deepgram-Streaming.
        
        Läuft in eigenem Thread, weil Deepgram async ist,
        aber der Main-Thread für QuickMacHotKey und UI frei bleiben muss.
        
        Lifecycle: Start → Mikrofon → Stream → Stop-Event → Finalize → Result
        """
        import asyncio

        transcribe = _get_transcribe()

        try:
            model = self.model or transcribe.DEFAULT_DEEPGRAM_MODEL

            transcribe.setup_logging(debug=logger.level == logging.DEBUG)

            # Eigener Event-Loop, da wir nicht im Main-Thread sind
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            try:
                transcript = loop.run_until_complete(
                    transcribe._deepgram_stream_core(
                        model=model,
                        language=self.language,
                        play_ready=True,
                        external_stop_event=self._stop_event,
                    )
                )

                # LLM-Nachbearbeitung (optional)
                if self.refine and transcript:
                    transcript = transcribe.refine_transcript(
                        transcript,
                        model=self.refine_model,
                        provider=self.refine_provider,
                        context=self.context,
                    )

                self._result_queue.put(transcript)

            finally:
                loop.close()

        except Exception as e:
            logger.exception(f"Streaming-Worker Fehler: {e}")
            self._result_queue.put(e)

    def _stop_recording(self) -> None:
        """Stoppt Aufnahme und wartet auf Worker-Beendigung."""
        if not self._recording:
            return

        logger.info("Stop-Event setzen...")

        self._stop_interim_polling()

        # Signal an Worker: Beende Deepgram-Stream sauber
        if self._stop_event:
            self._stop_event.set()

        # Worker-Thread muss beendet sein, bevor neuer starten kann
        # Verhindert parallele Mikrofon-Zugriffe
        if self._worker_thread is not None and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=2.0)
            if self._worker_thread.is_alive():
                logger.warning("Worker-Thread noch aktiv nach Timeout")

        self._recording = False
        self._update_state("transcribing")

        # Polling statt Blocking: Main-Thread bleibt reaktiv für UI
        self._start_result_polling()

    def _start_result_polling(self) -> None:
        """Startet NSTimer für Result-Polling."""
        from Foundation import NSTimer  # type: ignore[import-not-found]

        def check_result() -> None:
            try:
                result = self._result_queue.get_nowait()
                self._stop_result_polling()

                if isinstance(result, Exception):
                    logger.error(f"Fehler: {result}")
                    transcribe = _get_transcribe()
                    transcribe.play_sound("error")
                    self._update_state("error")
                elif result:
                    self._paste_result(result)
                    self._update_state("done")
                else:
                    logger.warning("Leeres Transkript")
                    self._update_state("idle")

            except queue.Empty:
                pass  # Noch kein Result

        # NSTimer für regelmäßiges Polling (50ms)
        self._result_timer = NSTimer.scheduledTimerWithTimeInterval_repeats_block_(
            0.05, True, lambda _: check_result()
        )

    def _stop_result_polling(self) -> None:
        """Stoppt NSTimer."""
        if self._result_timer:
            self._result_timer.invalidate()
            self._result_timer = None

    def _paste_result(self, transcript: str) -> None:
        """Fügt Transkript via Auto-Paste ein."""
        hotkey = _get_hotkey()
        success = hotkey.paste_transcript(transcript)
        if success:
            logger.info(f"✓ Text eingefügt: '{transcript[:50]}...'")
        else:
            logger.error("Auto-Paste fehlgeschlagen")

    def run(self) -> None:
        """Startet Daemon (blockiert)."""
        from quickmachotkey import quickHotKey
        from AppKit import NSApplication  # type: ignore[import-not-found]
        from Foundation import NSTimer  # type: ignore[import-not-found]
        import signal

        hotkey = _get_hotkey()

        # UI-Controller initialisieren
        logger.info("Initialisiere UI-Controller...")
        self._menubar = MenuBarController()
        self._overlay = OverlayController()
        logger.info("UI-Controller bereit")

        # Hotkey parsen
        virtual_key, modifier_mask = hotkey.parse_hotkey(self.hotkey)

        logger.info(
            f"Daemon gestartet: hotkey={self.hotkey}, "
            f"virtualKey={virtual_key}, modifierMask={modifier_mask}"
        )
        print("🎤 whisper_daemon läuft", file=sys.stderr)
        print(f"   Hotkey: {self.hotkey}", file=sys.stderr)
        print("   Beenden mit Ctrl+C", file=sys.stderr)

        # Hotkey registrieren
        @quickHotKey(virtualKey=virtual_key, modifierMask=modifier_mask)  # type: ignore[arg-type]
        def hotkey_handler() -> None:
            self._on_hotkey()

        # NSApplication Event-Loop (blockiert)
        app = NSApplication.sharedApplication()

        # FIX: Ctrl+C Support
        # 1. Dummy-Timer, damit der Python-Interpreter regelmäßig läuft und Signale prüft
        NSTimer.scheduledTimerWithTimeInterval_repeats_block_(
            0.1, True, lambda _: None
        )

        # 2. Signal-Handler, der die App sauber beendet
        def signal_handler(sig, frame):
            app.terminate_(None)

        signal.signal(signal.SIGINT, signal_handler)

        app.run()


# =============================================================================
# Environment Loading
# =============================================================================


def load_environment() -> None:
    """Lädt .env-Datei falls vorhanden."""
    try:
        from dotenv import load_dotenv

        env_file = SCRIPT_DIR / ".env"
        load_dotenv(env_file if env_file.exists() else None)
    except ImportError:
        pass


# =============================================================================
# Main
# =============================================================================


def main() -> int:
    """CLI-Einstiegspunkt."""
    import argparse

    parser = argparse.ArgumentParser(
        description="whisper_daemon – Unified Daemon für whisper_go",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Beispiele:
  %(prog)s                          # Mit Defaults aus .env
  %(prog)s --hotkey f19             # F19 als Hotkey
  %(prog)s --hotkey cmd+shift+r     # Tastenkombination
  %(prog)s --refine                 # Mit LLM-Nachbearbeitung
        """,
    )

    parser.add_argument(
        "--hotkey",
        default=None,
        help="Hotkey (default: WHISPER_GO_HOTKEY oder 'f19')",
    )
    parser.add_argument(
        "--language",
        default=None,
        help="Sprachcode z.B. 'de', 'en'",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Deepgram-Modell (default: nova-3)",
    )
    parser.add_argument(
        "--refine",
        action="store_true",
        default=os.getenv("WHISPER_GO_REFINE", "").lower() == "true",
        help="LLM-Nachbearbeitung aktivieren",
    )
    parser.add_argument(
        "--refine-model",
        default=None,
        help="Modell für LLM-Nachbearbeitung",
    )
    parser.add_argument(
        "--refine-provider",
        choices=["openai", "openrouter", "groq"],
        default=None,
        help="LLM-Provider für Nachbearbeitung",
    )
    parser.add_argument(
        "--context",
        choices=["email", "chat", "code", "default"],
        default=None,
        help="Kontext für LLM-Nachbearbeitung",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Debug-Logging aktivieren",
    )

    args = parser.parse_args()

    # Environment laden
    load_environment()
    setup_logging(debug=args.debug)

    # Konfiguration: CLI > ENV > Default
    hotkey = args.hotkey or os.getenv("WHISPER_GO_HOTKEY", "f19")
    language = args.language or os.getenv("WHISPER_GO_LANGUAGE")
    model = args.model or os.getenv("WHISPER_GO_MODEL")

    # Daemon starten
    try:
        daemon = WhisperDaemon(
            hotkey=hotkey,
            language=language,
            model=model,
            refine=args.refine,
            refine_model=args.refine_model,
            refine_provider=args.refine_provider,
            context=args.context,
        )
        daemon.run()
    except ValueError as e:
        print(f"Konfigurationsfehler: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\n👋 Daemon beendet", file=sys.stderr)
        return 0
    except Exception as e:
        logger.exception(f"Unerwarteter Fehler: {e}")
        print(f"Fehler: {e}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
