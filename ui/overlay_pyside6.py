"""PySide6 Overlay für PulseScribe (Windows).

GPU-beschleunigtes Overlay mit:
- QWidget + QPainter für Hardware-Rendering
- QTimer mit PreciseTimer für echte 60 FPS
- Signals/Slots für Thread-Safety
- Windows 11 Mica-Effekt mit nativen runden Ecken (22H2+)
- Graceful Fallback auf Solid-Background (Win10/ältere Builds)
- Traveling Wave + Gaussian Envelope Animation
- High-DPI Awareness (scharfe Darstellung auf 4K)
- Multi-Monitor Support (Overlay auf aktivem Monitor)
"""

import ctypes
import logging
import math
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import (
    QMetaObject,
    QPoint,
    QPropertyAnimation,
    QRectF,
    QTimer,
    Qt,
    Q_ARG,
    Signal,
    Slot,
)
from PySide6.QtGui import (
    QBrush,
    QColor,
    QCursor,
    QFont,
    QPainter,
    QPainterPath,
    QPen,
)
from PySide6.QtWidgets import (
    QApplication,
    QLabel,
    QWidget,
)

if TYPE_CHECKING:
    from PySide6.QtGui import QScreen

from ui.animation import (
    AnimationLogic,
    BAR_COUNT,
    BAR_WIDTH,
    BAR_GAP,
    BAR_MIN_HEIGHT,
    BAR_MAX_HEIGHT,
)

logger = logging.getLogger("pulsescribe.overlay")

# =============================================================================
# Window-Konstanten
# =============================================================================

WINDOW_WIDTH = 280
WINDOW_HEIGHT = 90
WINDOW_CORNER_RADIUS = 16
WINDOW_MARGIN_BOTTOM = 60

# =============================================================================
# Animation-Konstanten
# =============================================================================

FPS = 60
FRAME_MS = 1000 // FPS  # ~16ms

# =============================================================================
# Farben
# =============================================================================

BG_COLOR = QColor(26, 26, 26, 200)  # #1A1A1A mit ~78% Alpha (transparenter)
BORDER_COLOR = QColor(255, 255, 255, 38)  # Subtle border

STATE_COLORS = {
    "IDLE": QColor(255, 255, 255, 230),
    "LISTENING": QColor(255, 182, 193),  # Pink
    "RECORDING": QColor(255, 82, 82),  # Rot
    "TRANSCRIBING": QColor(255, 177, 66),  # Orange
    "REFINING": QColor(156, 39, 176),  # Lila
    "LOADING": QColor(66, 165, 245),  # Material Blue 400
    "DONE": QColor(76, 175, 80),  # Grün
    "ERROR": QColor(255, 71, 87),  # Rot
}

STATE_TEXTS = {
    "LISTENING": "Listening...",
    "RECORDING": "Recording...",
    "TRANSCRIBING": "Transcribing...",
    "REFINING": "Refining...",
    "LOADING": "Loading model...",
    "DONE": "Done!",
    "ERROR": "Error",
}

# Auto-Hide Timer für DONE/ERROR
FEEDBACK_DISPLAY_MS = 800  # Millisekunden

# =============================================================================
# Windows 11 DWM-Konstanten (Mica Effect)
# =============================================================================

# DwmSetWindowAttribute Attribute IDs
DWMWA_USE_IMMERSIVE_DARK_MODE = 20
DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMWA_SYSTEMBACKDROP_TYPE = 38

# DWM_SYSTEMBACKDROP_TYPE Values (Windows 11 22H2+)
DWMSBT_AUTO = 0
DWMSBT_DISABLE = 1
DWMSBT_MAINWINDOW = 2       # Mica
DWMSBT_TRANSIENTWINDOW = 3  # Acrylic
DWMSBT_TABBEDWINDOW = 4     # Tabbed (Mica Alt)

# DWM_WINDOW_CORNER_PREFERENCE Values
DWMWCP_DEFAULT = 0
DWMWCP_DONOTROUND = 1
DWMWCP_ROUND = 2
DWMWCP_ROUNDSMALL = 3


# =============================================================================
# Multi-Monitor Helper
# =============================================================================


def _get_active_screen() -> "QScreen | None":
    """Ermittelt den Monitor des aktiven Fensters (Windows)."""
    app = QApplication.instance()
    if not app:
        return None

    # Methode 1: Versuche das aktive Fenster über Windows API zu finden
    if sys.platform == "win32":
        try:
            from ctypes import wintypes

            user32 = ctypes.windll.user32

            # Aktives Fenster holen
            hwnd = user32.GetForegroundWindow()
            if hwnd:
                # Fenster-Rechteck holen
                rect = wintypes.RECT()
                if user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                    # Mittelpunkt des aktiven Fensters
                    center_x = (rect.left + rect.right) // 2
                    center_y = (rect.top + rect.bottom) // 2

                    # Screen an diesem Punkt finden
                    screen = app.screenAt(QPoint(center_x, center_y))
                    if screen:
                        return screen
        except Exception as e:
            logger.debug(f"Aktiver Monitor nicht ermittelbar: {e}")

    # Methode 2: Fallback auf Cursor-Position
    try:
        cursor_pos = QCursor.pos()
        screen = app.screenAt(cursor_pos)
        if screen:
            return screen
    except Exception:
        pass

    # Methode 3: Primary Screen
    return app.primaryScreen()


# =============================================================================
# Windows 11 Mica Effect Helper
# =============================================================================


def _get_windows_build() -> int:
    """Ermittelt die Windows Build-Nummer."""
    if sys.platform != "win32":
        return 0

    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion") as key:
            build = winreg.QueryValueEx(key, "CurrentBuildNumber")[0]
            return int(build)
    except Exception:
        return 0


def _enable_mica_effect(hwnd: int) -> bool:
    """Aktiviert Windows 11 Mica-Effekt mit nativen runden Ecken.

    Erfordert Windows 11 22H2 (Build 22621+) für DWMWA_SYSTEMBACKDROP_TYPE.
    Auf älteren Systemen wird False zurückgegeben und der Solid-Background verwendet.

    Hinweis: DwmExtendFrameIntoClientArea wird NICHT aufgerufen, da es laut
    Avalonia Issue #7403 den Mica-Effekt auf Qt-ähnlichen Frameworks bricht.
    Qt's FramelessWindowHint + WA_TranslucentBackground reicht aus.
    """
    if sys.platform != "win32":
        return False

    # Mindestens Windows 11 22H2 (Build 22621) erforderlich
    build = _get_windows_build()
    if build < 22621:
        logger.debug(f"Mica nicht verfügbar (Build {build} < 22621)")
        return False

    try:
        from ctypes import wintypes

        dwmapi = ctypes.windll.dwmapi

        # Funktions-Signaturen definieren für korrektes 64-bit Handling
        # HRESULT DwmSetWindowAttribute(HWND, DWORD, LPCVOID, DWORD)
        dwmapi.DwmSetWindowAttribute.argtypes = [
            wintypes.HWND,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        dwmapi.DwmSetWindowAttribute.restype = wintypes.LONG  # HRESULT

        # 1. Dark Mode aktivieren (für dunkles Mica)
        # Nicht kritisch - bei Fehler wird Light-Theme verwendet
        dark_mode = ctypes.c_int(1)
        result = dwmapi.DwmSetWindowAttribute(
            hwnd,
            DWMWA_USE_IMMERSIVE_DARK_MODE,
            ctypes.byref(dark_mode),
            ctypes.sizeof(dark_mode),
        )
        if result != 0:
            logger.debug(f"Dark Mode fehlgeschlagen (HRESULT {result}), fahre fort...")

        # 2. Runde Ecken via DWM (native, nicht QPainter)
        # Nicht kritisch - bei Fehler werden Default-Ecken verwendet
        corners = ctypes.c_int(DWMWCP_ROUND)
        result = dwmapi.DwmSetWindowAttribute(
            hwnd,
            DWMWA_WINDOW_CORNER_PREFERENCE,
            ctypes.byref(corners),
            ctypes.sizeof(corners),
        )
        if result != 0:
            logger.debug(f"Rounded Corners fehlgeschlagen (HRESULT {result}), fahre fort...")

        # 3. Mica Backdrop aktivieren - KRITISCH
        # Bei Fehler hier → Fallback auf Solid-Background
        backdrop = ctypes.c_int(DWMSBT_MAINWINDOW)
        result = dwmapi.DwmSetWindowAttribute(
            hwnd,
            DWMWA_SYSTEMBACKDROP_TYPE,
            ctypes.byref(backdrop),
            ctypes.sizeof(backdrop),
        )

        if result == 0:  # S_OK
            logger.debug(f"Windows 11 Mica-Effekt aktiviert (Build {build})")
            return True
        else:
            logger.debug(f"Mica Backdrop fehlgeschlagen (HRESULT {result}) → Fallback")
            return False

    except Exception as e:
        logger.debug(f"Mica nicht verfügbar: {e}")
        return False


# =============================================================================
# PySide6 Overlay Widget
# =============================================================================


class PySide6OverlayWidget(QWidget):
    """GPU-beschleunigtes Overlay Widget."""

    # Signals für Thread-Safety
    state_changed = Signal(str, str)
    level_changed = Signal(float)
    interim_changed = Signal(str)

    def __init__(self):
        super().__init__()

        # State
        self._state = "IDLE"
        self._text = ""
        self._audio_level = 0.0
        self._anim = AnimationLogic()
        self._bar_heights = [float(BAR_MIN_HEIGHT)] * BAR_COUNT
        self._animation_start = time.perf_counter()
        self._mica_enabled = False
        self._fade_out_timer: QTimer | None = None

        # Setup
        self._setup_window()
        self._setup_label()
        self._setup_animation()
        self._setup_fade_animation()

        # Connect signals to slots (thread-safe)
        self.state_changed.connect(self._on_state_changed)
        self.level_changed.connect(self._on_level_changed)
        self.interim_changed.connect(self._on_interim_changed)

        # Precompute bar positions
        total_width = BAR_COUNT * BAR_WIDTH + (BAR_COUNT - 1) * BAR_GAP
        start_x = (WINDOW_WIDTH - total_width) // 2
        self._bar_x_positions = [
            start_x + i * (BAR_WIDTH + BAR_GAP) for i in range(BAR_COUNT)
        ]
        self._bar_center_y = 35

    def _setup_window(self):
        """Konfiguriert das Fenster."""
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFixedSize(WINDOW_WIDTH, WINDOW_HEIGHT)
        self._center_on_screen()

    def _center_on_screen(self, use_active_screen: bool = False):
        """Positioniert das Fenster unten-mittig.

        Args:
            use_active_screen: Wenn True, wird der Monitor des aktiven Fensters verwendet.
        """
        if use_active_screen:
            screen = _get_active_screen()
        else:
            screen = QApplication.primaryScreen()

        if screen:
            geometry = screen.availableGeometry()
            # Position relativ zum Screen berechnen
            x = geometry.x() + (geometry.width() - WINDOW_WIDTH) // 2
            y = geometry.y() + geometry.height() - WINDOW_HEIGHT - WINDOW_MARGIN_BOTTOM
            self.move(x, y)

    def _setup_label(self):
        """Erstellt das Text-Label."""
        self._label = QLabel(self)
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._label.setGeometry(10, 55, WINDOW_WIDTH - 20, 30)
        self._label.setStyleSheet(
            "QLabel { color: white; background: transparent; }"
        )
        font = QFont("Segoe UI", 11)
        self._label.setFont(font)

    def _setup_animation(self):
        """Konfiguriert den Animation-Timer."""
        self._animation_timer = QTimer(self)
        self._animation_timer.timeout.connect(self._animate_frame)
        self._animation_timer.setTimerType(Qt.TimerType.PreciseTimer)

    def _setup_fade_animation(self):
        """Konfiguriert Fade-In/Out Animation."""
        self._fade_animation = QPropertyAnimation(self, b"windowOpacity")
        self._fade_animation.setDuration(150)  # 150ms
        # Einmalig verbinden (nicht in _fade_out, um multiple connections zu vermeiden)
        self._fade_animation.finished.connect(self._on_fade_out_finished)

    def _fade_in(self):
        """Blendet Overlay ein."""
        self._cancel_fade_out_timer()
        self._fade_animation.stop()
        self._fade_animation.setStartValue(self.windowOpacity())
        self._fade_animation.setEndValue(1.0)
        self.show()
        self._fade_animation.start()

    def _fade_out(self):
        """Blendet Overlay aus."""
        self._fade_animation.stop()
        self._fade_animation.setStartValue(self.windowOpacity())
        self._fade_animation.setEndValue(0.0)
        self._fade_animation.start()

    def _on_fade_out_finished(self):
        """Versteckt das Fenster nach Fade-Out (nur wenn wirklich ausgeblendet)."""
        # Nur reagieren wenn wir wirklich am Ausblenden sind (nicht bei Fade-In)
        if self._fade_animation.endValue() == 0.0 and self.windowOpacity() < 0.1:
            self.hide()
            self._stop_animation()
            self._reset_levels()

    def _start_fade_out_timer(self):
        """Startet Timer für automatisches Ausblenden nach DONE/ERROR."""
        self._cancel_fade_out_timer()
        self._fade_out_timer = QTimer(self)
        self._fade_out_timer.setSingleShot(True)
        self._fade_out_timer.timeout.connect(self._fade_out)
        self._fade_out_timer.start(FEEDBACK_DISPLAY_MS)

    def _cancel_fade_out_timer(self):
        """Bricht den Fade-Out Timer ab."""
        if self._fade_out_timer:
            self._fade_out_timer.stop()
            self._fade_out_timer = None

    @Slot()
    def cleanup(self):
        """Stoppt alle Timer sauber (muss im Qt-Thread aufgerufen werden)."""
        self._cancel_fade_out_timer()
        if hasattr(self, "_animation_timer") and self._animation_timer:
            self._animation_timer.stop()
        if hasattr(self, "_fade_animation") and self._fade_animation:
            self._fade_animation.stop()

    def showEvent(self, event):
        """Aktiviert Mica-Effekt beim ersten Anzeigen (Windows 11 22H2+)."""
        super().showEvent(event)
        if not self._mica_enabled:
            hwnd = int(self.winId())
            self._mica_enabled = _enable_mica_effect(hwnd)

    # =========================================================================
    # Public API (Thread-Safe via Signals)
    # =========================================================================

    @property
    def current_state(self) -> str:
        """Gibt den aktuellen State zurück (read-only)."""
        return self._state

    def update_state(self, state: str, text: str | None = None):
        """Thread-safe State-Update."""
        self.state_changed.emit(state, text or "")

    def update_audio_level(self, level: float):
        """Thread-safe Level-Update (direkt, ohne Signal-Queue).

        Level-Updates sind unkritisch - verlorene Updates sind ok.
        Direktes Setzen vermeidet Signal-Queue-Verzögerung.
        """
        self._audio_level = level  # Atomare Zuweisung (Python GIL)

    def update_interim_text(self, text: str):
        """Thread-safe Interim-Text-Update."""
        self.interim_changed.emit(text)

    # =========================================================================
    # Slots (Main Thread)
    # =========================================================================

    @Slot(str, str)
    def _on_state_changed(self, state: str, text: str):
        prev_state = self._state
        self._state = state
        self._text = text
        self._animation_start = time.perf_counter()

        if state == "IDLE":
            self._fade_out()
        else:
            # Bei Übergang von IDLE: Auf aktivem Monitor positionieren
            if prev_state == "IDLE":
                self._center_on_screen(use_active_screen=True)

            # Label aktualisieren
            display_text = text or STATE_TEXTS.get(state, "")
            self._update_label(state, display_text)
            self._fade_in()
            self._start_animation()

            # Auto-Hide für DONE/ERROR
            if state in ("DONE", "ERROR"):
                self._start_fade_out_timer()

    @Slot(float)
    def _on_level_changed(self, level: float):
        self._audio_level = level

    @Slot(str)
    def _on_interim_changed(self, text: str):
        if self._state == "RECORDING":
            if len(text) > 45:
                text = "..." + text[-42:]
            self._update_label("RECORDING", text, italic=True)

    def _update_label(self, state: str, text: str, italic: bool = False):
        """Aktualisiert das Label mit State-spezifischem Styling."""
        font = QFont("Segoe UI", 11 if not italic else 10)
        font.setItalic(italic)
        self._label.setFont(font)

        if italic:
            self._label.setStyleSheet(
                "QLabel { color: rgba(255, 255, 255, 0.6); background: transparent; }"
            )
        elif state in ("DONE", "ERROR"):
            color = STATE_COLORS.get(state, QColor(255, 255, 255))
            self._label.setStyleSheet(
                f"QLabel {{ color: rgb({color.red()}, {color.green()}, {color.blue()}); background: transparent; }}"
            )
        else:
            self._label.setStyleSheet(
                "QLabel { color: white; background: transparent; }"
            )

        self._label.setText(text)

    def _reset_levels(self):
        """Setzt Audio-Level zurück."""
        self._audio_level = 0.0
        self._anim = AnimationLogic()
        self._bar_heights = [float(BAR_MIN_HEIGHT)] * BAR_COUNT

    # =========================================================================
    # Animation
    # =========================================================================

    def _start_animation(self):
        if not self._animation_timer.isActive():
            self._animation_start = time.perf_counter()
            self._animation_timer.start(FRAME_MS)

    def _stop_animation(self):
        self._animation_timer.stop()

    @Slot()
    def _animate_frame(self):
        if self._state == "IDLE":
            return

        t = time.perf_counter() - self._animation_start

        # Audio-Level an Animation-Logik übergeben
        self._anim.update_level(self._audio_level)

        # AGC für Recording
        if self._state == "RECORDING":
            self._anim.update_agc()

        # Bar-Höhen berechnen
        for i in range(BAR_COUNT):
            target_height = self._anim.calculate_bar_height(i, t, self._state)

            # Per-Bar Smoothing
            if target_height > self._bar_heights[i]:
                bar_alpha = 0.4
            else:
                bar_alpha = 0.15
            self._bar_heights[i] += bar_alpha * (target_height - self._bar_heights[i])

        # Repaint anfordern
        self.update()

    # =========================================================================
    # Painting
    # =========================================================================

    def paintEvent(self, event):
        """Zeichnet Background und Bars."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        self._draw_background(painter)
        self._draw_bars(painter)

        painter.end()

    def _draw_background(self, painter: QPainter):
        """Zeichnet abgerundeten Hintergrund.

        Bei aktivem Mica-Effekt wird nichts gezeichnet, da DWM das
        komplette Fenster mit Blur und nativen runden Ecken rendert.
        """
        # Bei Mica: DWM übernimmt Hintergrund UND Ecken - nichts zeichnen
        if self._mica_enabled:
            return

        # Fallback: Solid-Background mit QPainter (Win10/ältere Builds)
        path = QPainterPath()
        rect = QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
        path.addRoundedRect(rect, WINDOW_CORNER_RADIUS, WINDOW_CORNER_RADIUS)

        painter.fillPath(path, QBrush(BG_COLOR))
        painter.setPen(QPen(BORDER_COLOR, 1))
        painter.drawPath(path)

    def _draw_bars(self, painter: QPainter):
        """Zeichnet alle Bars."""
        color = STATE_COLORS.get(self._state, QColor(255, 255, 255))
        painter.setBrush(QBrush(color))
        painter.setPen(Qt.PenStyle.NoPen)

        for i in range(BAR_COUNT):
            height = max(BAR_MIN_HEIGHT, self._bar_heights[i])
            x = self._bar_x_positions[i]
            self._draw_pill(painter, x, self._bar_center_y, BAR_WIDTH, height)

    def _draw_pill(self, painter: QPainter, x: float, center_y: float, width: float, height: float):
        """Zeichnet eine Pill-förmige Bar."""
        y1 = center_y - height / 2
        y2 = center_y + height / 2

        path = QPainterPath()

        if height <= width:
            # Sehr klein: Oval
            path.addEllipse(QRectF(x, y1, width, height))
        else:
            # Pill: Rechteck mit abgerundeten Enden
            radius = width / 2
            path.addRoundedRect(QRectF(x, y1, width, y2 - y1), radius, radius)

        painter.drawPath(path)


# =============================================================================
# Controller Wrapper (API-kompatibel mit WindowsOverlayController)
# =============================================================================


class PySide6OverlayController:
    """Drop-in Replacement für WindowsOverlayController.

    Threading Model:
        The run() method starts the Qt event loop and blocks until stop() is called.
        It should be called from a dedicated thread that will become the Qt GUI thread.
        All Qt widgets and timers are created and managed within this thread.

        Cross-thread communication is handled via Qt Signals, which are thread-safe.
        The public methods (update_state, update_audio_level, update_interim_text)
        can be safely called from any thread - they emit signals that are processed
        in the Qt event loop thread.

    Usage:
        controller = PySide6OverlayController()
        threading.Thread(target=controller.run, daemon=True).start()
        # Now safe to call from main thread:
        controller.update_state("RECORDING")
    """

    def __init__(self, interim_file: Path | None = None):
        import threading

        self._app: QApplication | None = None
        self._widget: PySide6OverlayWidget | None = None
        self._interim_file = interim_file
        self._running = False
        self._last_interim_text = ""
        self._interim_timer: QTimer | None = None
        # Ready-Event: Wird gesetzt sobald Widget bereit ist
        self._ready_event = threading.Event()
        # Pending State: Falls update_state vor ready aufgerufen wird
        self._pending_state: tuple[str, str] | None = None

    def run(self):
        """Start Qt Event Loop.

        This method blocks and runs the Qt event loop. Call from a dedicated thread.
        The calling thread becomes the Qt GUI thread where all widgets are processed.
        """
        # High-DPI Awareness konfigurieren (muss vor QApplication passieren)
        if not QApplication.instance():
            # Automatische High-DPI Skalierung aktivieren
            QApplication.setHighDpiScaleFactorRoundingPolicy(
                Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
            )

        # QApplication erstellen (oder existierende verwenden)
        self._app = QApplication.instance()
        if self._app is None:
            self._app = QApplication([])

        self._widget = PySide6OverlayWidget()
        self._running = True

        # Pending State anwenden (falls update_state vor ready aufgerufen wurde)
        if self._pending_state:
            state, text = self._pending_state
            self._pending_state = None
            self._widget.update_state(state, text)
            logger.debug(f"Pending State angewendet: {state}")

        # Ready-Event setzen (andere Threads können jetzt update_state aufrufen)
        self._ready_event.set()

        # Interim-File Polling (wenn aktiviert)
        if self._interim_file:
            self._interim_timer = QTimer()
            self._interim_timer.timeout.connect(self._poll_interim_file)
            self._interim_timer.start(200)  # 200ms wie macOS

        # Cleanup vor App-Exit (stoppt alle Timer im Qt-Thread)
        self._app.aboutToQuit.connect(self._cleanup_timers)

        logger.info("PySide6 Overlay gestartet (High-DPI: aktiv)")
        self._app.exec()

    def _cleanup_timers(self):
        """Stoppt alle Timer vor App-Exit (wird im Qt-Thread aufgerufen)."""
        if self._interim_timer:
            self._interim_timer.stop()
            self._interim_timer = None
        if self._widget:
            self._widget.cleanup()

    def stop(self):
        """Beendet das Overlay (thread-safe)."""
        self._running = False
        if self._app:
            # Thread-safe: quit() im Qt-Event-Loop ausführen
            QMetaObject.invokeMethod(self._app, "quit", Qt.QueuedConnection)

    def update_state(self, state: str, text: str | None = None):
        """Thread-safe State-Update."""
        if self._widget:
            self._widget.update_state(state, text)
        else:
            # Widget noch nicht bereit - State für später speichern
            self._pending_state = (state, text or "")

    def update_audio_level(self, level: float):
        """Thread-safe Level-Update."""
        if self._widget:
            self._widget.update_audio_level(level)

    def update_interim_text(self, text: str):
        """Thread-safe Interim-Text-Update."""
        if self._widget:
            self._widget.update_interim_text(text)

    def _poll_interim_file(self):
        """Pollt Interim-File für Text-Updates."""
        if not self._running or not self._interim_file or not self._widget:
            return

        if self._widget.current_state != "RECORDING":
            return

        if not self._interim_file.exists():
            return

        try:
            text = self._interim_file.read_text(encoding="utf-8").strip()
            if text and text != self._last_interim_text:
                self._last_interim_text = text
                self._widget.update_interim_text(text)
        except Exception as e:
            logger.debug(f"Interim-File lesen fehlgeschlagen: {e}")


__all__ = ["PySide6OverlayController", "PySide6OverlayWidget"]
