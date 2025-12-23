"""PySide6 Overlay für PulseScribe (Windows).

GPU-beschleunigtes Overlay mit:
- QWidget + QPainter für Hardware-Rendering
- QTimer mit PreciseTimer für echte 60 FPS
- Signals/Slots für Thread-Safety
- Optionaler Windows Acrylic Blur (Win10+)
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
    QPoint,
    QPropertyAnimation,
    QRectF,
    QTimer,
    Qt,
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

# Mathematische Konstante für Frequenzberechnungen
TAU = math.tau  # 2π

logger = logging.getLogger("pulsescribe.overlay")

# =============================================================================
# Window-Konstanten
# =============================================================================

WINDOW_WIDTH = 280
WINDOW_HEIGHT = 90
WINDOW_CORNER_RADIUS = 16
WINDOW_MARGIN_BOTTOM = 60

# =============================================================================
# Bar-Konstanten
# =============================================================================

BAR_COUNT = 10
BAR_WIDTH = 4
BAR_GAP = 5
BAR_MIN_HEIGHT = 6
BAR_MAX_HEIGHT = 42

# =============================================================================
# Animation-Konstanten
# =============================================================================

FPS = 60
FRAME_MS = 1000 // FPS  # ~16ms

# Smoothing: Schneller Anstieg, langsames Abklingen
SMOOTHING_ALPHA_RISE = 0.55
SMOOTHING_ALPHA_FALL = 0.12

# Zusätzliches Level-Smoothing (wie macOS)
LEVEL_SMOOTHING_RISE = 0.30
LEVEL_SMOOTHING_FALL = 0.10

# Audio-Visual Mapping
VISUAL_GAIN = 2.0
VISUAL_NOISE_GATE = 0.002
VISUAL_EXPONENT = 1.3

# Adaptive Gain Control (AGC)
AGC_DECAY = 0.9923
AGC_MIN_PEAK = 0.01
AGC_HEADROOM = 2.0

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
# Farben
# =============================================================================

BG_COLOR = QColor(26, 26, 26, 242)  # #1A1A1A mit 95% Alpha
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
# Helper-Funktionen
# =============================================================================


def _gaussian(distance: float, sigma: float) -> float:
    """Gaussian-Funktion für Envelope."""
    if sigma <= 0:
        return 0.0
    x = distance / sigma
    return math.exp(-0.5 * x * x)


def _build_height_factors() -> list[float]:
    """Symmetrische Höhenfaktoren (Mitte höher als Ränder)."""
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
# Windows Blur Helper
# =============================================================================


def _enable_windows_blur(hwnd: int) -> bool:
    """Aktiviert Windows Acrylic Blur (Windows 10 1803+)."""
    if sys.platform != "win32":
        return False

    try:
        from ctypes import wintypes

        class ACCENT_POLICY(ctypes.Structure):
            _fields_ = [
                ("AccentState", ctypes.c_int),
                ("AccentFlags", ctypes.c_int),
                ("GradientColor", ctypes.c_uint),
                ("AnimationId", ctypes.c_int),
            ]

        class WINDOWCOMPOSITIONATTRIBDATA(ctypes.Structure):
            _fields_ = [
                ("Attribute", ctypes.c_int),
                ("Data", ctypes.POINTER(ACCENT_POLICY)),
                ("SizeOfData", ctypes.c_size_t),
            ]

        # ACCENT_ENABLE_ACRYLICBLURBEHIND = 4
        accent = ACCENT_POLICY()
        accent.AccentState = 4
        accent.AccentFlags = 2  # ACCENT_FLAG_DRAW_ALL
        accent.GradientColor = 0xCC1A1A1A  # ABGR: Dark with alpha

        data = WINDOWCOMPOSITIONATTRIBDATA()
        data.Attribute = 19  # WCA_ACCENT_POLICY
        data.Data = ctypes.pointer(accent)
        data.SizeOfData = ctypes.sizeof(accent)

        set_window_composition_attribute = ctypes.windll.user32.SetWindowCompositionAttribute
        set_window_composition_attribute.argtypes = [wintypes.HWND, ctypes.POINTER(WINDOWCOMPOSITIONATTRIBDATA)]
        set_window_composition_attribute.restype = ctypes.c_bool

        result = set_window_composition_attribute(hwnd, ctypes.byref(data))
        if result:
            logger.debug("Windows Acrylic Blur aktiviert")
        return result
    except Exception as e:
        logger.debug(f"Blur nicht verfügbar: {e}")
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
        self._smoothed_level = 0.0
        self._level_smoothed = 0.0  # Zweite Smoothing-Ebene
        self._agc_peak = AGC_MIN_PEAK
        self._normalized_level = 0.0
        self._bar_heights = [float(BAR_MIN_HEIGHT)] * BAR_COUNT
        self._animation_start = time.perf_counter()
        self._blur_enabled = False
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

    def showEvent(self, event):
        """Aktiviert Blur beim ersten Anzeigen."""
        super().showEvent(event)
        if not self._blur_enabled:
            hwnd = int(self.winId())
            self._blur_enabled = _enable_windows_blur(hwnd)

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
        """Thread-safe Level-Update."""
        self.level_changed.emit(level)

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
        self._smoothed_level = 0.0
        self._level_smoothed = 0.0
        self._agc_peak = AGC_MIN_PEAK
        self._normalized_level = 0.0
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

        # Level-Smoothing (erste Ebene)
        target = self._audio_level
        alpha = SMOOTHING_ALPHA_RISE if target > self._smoothed_level else SMOOTHING_ALPHA_FALL
        self._smoothed_level += alpha * (target - self._smoothed_level)

        # Level-Smoothing (zweite Ebene, wie macOS)
        alpha2 = LEVEL_SMOOTHING_RISE if self._smoothed_level > self._level_smoothed else LEVEL_SMOOTHING_FALL
        self._level_smoothed += alpha2 * (self._smoothed_level - self._level_smoothed)

        # AGC für Recording
        if self._state == "RECORDING":
            self._update_agc()

        # Bar-Höhen berechnen
        for i in range(BAR_COUNT):
            target_height = self._calculate_bar_height(i, t)

            # Per-Bar Smoothing
            if target_height > self._bar_heights[i]:
                bar_alpha = 0.4
            else:
                bar_alpha = 0.15
            self._bar_heights[i] += bar_alpha * (target_height - self._bar_heights[i])

        # Repaint anfordern
        self.update()

    def _update_agc(self):
        """Berechnet AGC-normalisierten Level."""
        gated = max(self._level_smoothed - VISUAL_NOISE_GATE, 0.0)

        if gated > self._agc_peak:
            self._agc_peak = gated
        else:
            self._agc_peak = max(self._agc_peak * AGC_DECAY, AGC_MIN_PEAK)

        reference_peak = max(self._agc_peak * AGC_HEADROOM, AGC_MIN_PEAK)
        normalized = gated / reference_peak if reference_peak > 0 else 0.0

        shaped = (min(1.0, normalized) ** VISUAL_EXPONENT) * VISUAL_GAIN
        self._normalized_level = min(1.0, shaped)

    def _calculate_bar_height(self, bar_index: int, t: float) -> float:
        """Berechnet Zielhöhe für einen Bar."""
        i = bar_index
        center = (BAR_COUNT - 1) / 2

        if self._state == "RECORDING":
            return self._calc_recording_height(i, t, center)
        elif self._state == "LISTENING":
            return self._calc_listening_height(i, t)
        elif self._state in ("TRANSCRIBING", "REFINING"):
            return self._calc_processing_height(i, t)
        elif self._state == "LOADING":
            return self._calc_loading_height(i, t)
        elif self._state == "DONE":
            return self._calc_done_height(i, t)
        elif self._state == "ERROR":
            return self._calc_error_height(t)

        return BAR_MIN_HEIGHT

    def _calc_recording_height(self, i: int, t: float, center: float) -> float:
        """Recording: Audio-responsive mit Traveling Wave und Envelope."""
        level = self._normalized_level

        # Traveling Wave
        phase1 = TAU * WAVE_WANDER_HZ_PRIMARY * t + i * WAVE_WANDER_PHASE_STEP_PRIMARY
        phase2 = TAU * WAVE_WANDER_HZ_SECONDARY * t + i * WAVE_WANDER_PHASE_STEP_SECONDARY
        wave1 = (math.sin(phase1) + 1) / 2
        wave2 = (math.sin(phase2) + 1) / 2
        wave_mod = WAVE_WANDER_BLEND * wave1 + (1 - WAVE_WANDER_BLEND) * wave2
        wave_factor = 1.0 - WAVE_WANDER_AMOUNT + WAVE_WANDER_AMOUNT * wave_mod

        # Gaussian Envelope
        env_phase1 = TAU * ENVELOPE_HZ_PRIMARY * t
        env_phase2 = TAU * ENVELOPE_HZ_SECONDARY * t
        env_offset1 = math.sin(env_phase1) * center * 0.8
        env_offset2 = math.sin(env_phase2) * center * 0.6
        env_center = center + ENVELOPE_BLEND * env_offset1 + (1 - ENVELOPE_BLEND) * env_offset2

        distance = abs(i - env_center)
        env_factor = ENVELOPE_BASE + (1 - ENVELOPE_BASE) * _gaussian(distance, ENVELOPE_SIGMA)
        env_factor = ENVELOPE_STRENGTH * env_factor + (1 - ENVELOPE_STRENGTH) * 1.0

        base_factor = _HEIGHT_FACTORS[i]
        combined = level * base_factor * wave_factor * env_factor

        return BAR_MIN_HEIGHT + (BAR_MAX_HEIGHT - BAR_MIN_HEIGHT) * combined

    def _calc_listening_height(self, i: int, t: float) -> float:
        """Listening: Langsames Atmen."""
        phase = t * 0.4 + i * 0.25
        breath = (math.sin(phase) + 1) / 2
        return BAR_MIN_HEIGHT + 12 * breath * _HEIGHT_FACTORS[i]

    def _calc_processing_height(self, i: int, t: float) -> float:
        """Transcribing/Refining: Wandernder Pulse."""
        pulse_pos = (t * 1.5) % (BAR_COUNT + 2) - 1
        distance = abs(i - pulse_pos)
        intensity = max(0, 1 - distance / 2)
        return BAR_MIN_HEIGHT + (BAR_MAX_HEIGHT * 0.6) * intensity

    def _calc_loading_height(self, i: int, t: float) -> float:
        """Loading: Langsames synchrones Pulsieren."""
        phase = t * 0.8  # Langsamer als Processing
        pulse = (math.sin(phase * math.pi) + 1) / 2
        return BAR_MIN_HEIGHT + (BAR_MAX_HEIGHT * 0.5) * pulse * _HEIGHT_FACTORS[i]

    def _calc_done_height(self, i: int, t: float) -> float:
        """Done: Bounce-Animation."""
        if t < 0.3:
            progress = t / 0.3
            return BAR_MIN_HEIGHT + (BAR_MAX_HEIGHT - BAR_MIN_HEIGHT) * progress * _HEIGHT_FACTORS[i]
        elif t < 0.5:
            progress = (t - 0.3) / 0.2
            bounce = 1 - abs(math.sin(progress * math.pi * 2)) * 0.3
            return BAR_MAX_HEIGHT * bounce * _HEIGHT_FACTORS[i]
        else:
            return BAR_MAX_HEIGHT * 0.7 * _HEIGHT_FACTORS[i]

    def _calc_error_height(self, t: float) -> float:
        """Error: Flash-Animation."""
        flash = (math.sin(t * 8) + 1) / 2
        return BAR_MIN_HEIGHT + (BAR_MAX_HEIGHT - BAR_MIN_HEIGHT) * flash * 0.5

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
        """Zeichnet abgerundeten Hintergrund."""
        path = QPainterPath()
        rect = QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
        path.addRoundedRect(rect, WINDOW_CORNER_RADIUS, WINDOW_CORNER_RADIUS)

        # Hintergrund (nur wenn kein Blur aktiv)
        if not self._blur_enabled:
            painter.fillPath(path, QBrush(BG_COLOR))

        # Border
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
        self._app: QApplication | None = None
        self._widget: PySide6OverlayWidget | None = None
        self._interim_file = interim_file
        self._running = False
        self._last_interim_text = ""
        self._interim_timer: QTimer | None = None

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

        # Interim-File Polling (wenn aktiviert)
        if self._interim_file:
            self._interim_timer = QTimer()
            self._interim_timer.timeout.connect(self._poll_interim_file)
            self._interim_timer.start(200)  # 200ms wie macOS

        logger.info("PySide6 Overlay gestartet (High-DPI: aktiv)")
        self._app.exec()

    def stop(self):
        """Beendet das Overlay."""
        self._running = False
        if self._interim_timer:
            self._interim_timer.stop()
        if self._app:
            self._app.quit()

    def update_state(self, state: str, text: str | None = None):
        """Thread-safe State-Update."""
        if self._widget:
            self._widget.update_state(state, text)

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
