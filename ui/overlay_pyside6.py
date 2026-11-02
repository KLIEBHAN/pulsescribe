"""PySide6 Overlay für PulseScribe (Windows).

Monochromes Overlay im Stil von ui/overlay_style.py:
- QWidget + QPainter, Inhalt aus ``describe_overlay_content`` (wie macOS)
- Nur RECORDING animiert (audiogetrieben); alle anderen States sind statisch
- Klick-durchlässig, außer während ein Fehler sichtbar ist (Klick blendet ihn aus)
- Signals/Slots für Thread-Safety
- High-DPI Awareness, Multi-Monitor Support (Overlay auf aktivem Monitor)
"""

import ctypes
import logging
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
    Signal,
    Slot,
)
from PySide6.QtGui import (
    QColor,
    QCursor,
    QFont,
    QFontDatabase,
    QFontMetricsF,
    QLinearGradient,
    QPainter,
    QPen,
)
from PySide6.QtWidgets import (
    QApplication,
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
    LISTENING_BAR_HEIGHTS,
)
from ui.overlay_feedback import (
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
    TEXT_COLOR,
    TEXT_FADE_WIDTH,
    TEXT_FONT_SIZE,
    OverlayLayout,
    compute_overlay_layout,
)
from ui.overlay_text import (
    FADED_RECORDING_INTERIM_MAX_CHARS,
    format_recording_duration,
    format_recording_interim_text as _format_recording_interim_text,  # noqa: F401
    split_recording_interim_text,
)
from utils.log_tail import read_file_tail_text

logger = logging.getLogger("pulsescribe.overlay")

# =============================================================================
# Window-/Animation-Konstanten (Maße/Farben: ui/overlay_style.py)
# =============================================================================

WINDOW_MARGIN_BOTTOM = 60
FPS = 60
FRAME_MS = 1000 // FPS  # ~16ms, nur während RECORDING
BAR_HEIGHT_UPDATE_EPSILON = 0.25  # Spare Repaints für subpixel-kleine Änderungen
BAR_CENTER_Y = OVERLAY_HEIGHT / 2
PX_TO_PT = 0.75  # Qt-Logik: 96 DPI → 1 px = 0.75 pt

# Auto-Hide Timer für kurze Ergebnis- / Fehler-Feedbacks
# Auf den DONE-Hold des Daemons (_DONE_DISPLAY_HOLD_SEC) abgestimmt, damit der
# Übergang DONE -> ausgeblendet snappy wirkt statt nachzuhängen.
FEEDBACK_DISPLAY_MS = 600  # Millisekunden
CLOCK_INTERVAL_MS = 1000  # Aufnahmedauer m:ss, nur LISTENING/RECORDING
INTERIM_POLL_MAX_CHARS = 512  # Begrenztes Tail-Read für niedrige Polling-Last
INTERIM_POLL_INTERVAL_MS = 200  # Polling nur während RECORDING
INTERIM_POLL_STABLE_INTERVAL_MS = 500
INTERIM_POLL_STABLE_THRESHOLD = 3
INTERIM_POLL_DIRECT_INTERVAL_MS = 1000  # Weniger Wakeups bei direkten Interim-Updates
INTERIM_DIRECT_UPDATE_GRACE_S = 1.5

_AUTO_HIDE_STATES = ("DONE", "NO_SPEECH")
_TEXT_COLORS = {
    "text": TEXT_COLOR,
    "muted": MUTED_TEXT_COLOR,
    "interim": INTERIM_TEXT_COLOR,
}


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


def _system_font(size_px: float, weight: QFont.Weight) -> QFont:
    """System UI font (Segoe UI on Windows) at a pixel-equivalent size."""
    font = QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont)
    font.setPointSizeF(size_px * PX_TO_PT)
    font.setWeight(weight)
    return font


def _tabular_figures(font: QFont) -> QFont:
    """Request tabular digits; PySide6 without ``QFont.Tag(str)`` keeps the default."""
    tag = getattr(QFont, "Tag", None)
    if tag is None or not hasattr(font, "setFeature"):
        return font
    try:
        font.setFeature(tag("tnum"), 1)
    except TypeError:  # PySide6 6.7.0-6.8.2: no string constructor
        pass
    return font


# =============================================================================
# PySide6 Overlay Widget
# =============================================================================


class PySide6OverlayWidget(QWidget):
    """Overlay Widget: ``[Balken] [Badge] [Text]``, wächst mit dem Text."""

    # Signals für Thread-Safety
    state_changed = Signal(str, str)
    recording_state_changed = Signal(bool)
    level_changed = Signal(float)
    interim_changed = Signal(str)

    def __init__(self):
        super().__init__()

        # State
        self._state = "IDLE"
        self._content = OverlayContent()
        self._interim = ("", "")
        self._previous_interim = ""
        self._audio_level = 0.0
        self._anim = AnimationLogic()
        self._bar_heights = [float(BAR_MIN_HEIGHT)] * BAR_COUNT
        self._painted_bar_heights = [float(BAR_MIN_HEIGHT)] * BAR_COUNT
        self._animation_start = time.perf_counter()
        self._active_screen_center_pending = False
        self._fade_out_timer: QTimer | None = None
        self._screen_geometry = None
        self._text_font = _system_font(TEXT_FONT_SIZE, QFont.Weight.Normal)
        self._badge_font = _system_font(BADGE_FONT_SIZE, QFont.Weight.DemiBold)
        self._dismiss_font = _system_font(DISMISS_FONT_SIZE, QFont.Weight.Normal)
        self._meta_font = _tabular_figures(
            _system_font(META_FONT_SIZE, QFont.Weight.Normal)
        )
        self._meta_color = QColor(META_TEXT_COLOR)
        self._meta_text = ""
        self._meta_shown = ""
        self._clock_start = 0.0
        self._persistent = False
        self._text_runs: list[tuple[float, str, QColor]] = []
        self._text_clipped = False
        self._badge_rect: QRectF | None = None
        self._layout = compute_overlay_layout(text_width=0)
        self._colors = {key: QColor(value) for key, value in _TEXT_COLORS.items()}
        self._bar_colors = {key: QColor(value) for key, value in BAR_COLORS.items()}
        self._step_colors = {
            mode: tuple(QColor(color) for color in colors)
            for mode, colors in PHASE_STEP_COLORS.items()
        }
        self._badge_colors = {
            tone: (QColor(background), QColor(foreground))
            for tone, (background, foreground) in BADGE_TONES.items()
        }

        # Setup
        self._setup_window()
        self._setup_animation()
        self._setup_fade_animation()
        self._setup_clock()

        # Connect signals to slots (thread-safe)
        self.state_changed.connect(self._on_state_changed)
        self.level_changed.connect(self._on_level_changed)
        self.interim_changed.connect(self._on_interim_changed)

    def _setup_window(self):
        """Konfiguriert das Fenster."""
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
            | Qt.WindowType.WindowTransparentForInput
        )
        # Transparent nur außerhalb der 3-px-Ecken; der Inhalt ist solide.
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFixedSize(OVERLAY_WIDTH, OVERLAY_HEIGHT)
        self.setAccessibleName("PulseScribe overlay")
        self.setAccessibleDescription(
            "Shows current recording and transcription feedback."
        )
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
            self._screen_geometry = screen.availableGeometry()
            self._move_to_bottom_center()

    def _move_to_bottom_center(self) -> None:
        geometry = self._screen_geometry
        if geometry is None:
            return
        x = geometry.x() + (geometry.width() - self.width()) // 2
        y = geometry.y() + geometry.height() - OVERLAY_HEIGHT - WINDOW_MARGIN_BOTTOM
        self.move(x, y)

    def _max_width(self) -> float:
        geometry = self._screen_geometry
        if geometry is None:
            return OVERLAY_WIDTH
        return geometry.width() * OVERLAY_MAX_WIDTH_RATIO

    def _schedule_center_on_active_screen(self):
        """Defer active-monitor lookup so first overlay feedback paints fast."""
        if getattr(self, "_active_screen_center_pending", False):
            return
        if QApplication.instance() is None:
            self._center_on_screen(use_active_screen=True)
            return
        self._active_screen_center_pending = True
        QTimer.singleShot(1, self._center_on_active_screen_if_visible)

    @Slot()
    def _center_on_active_screen_if_visible(self):
        self._active_screen_center_pending = False
        if self._state != "IDLE":
            self._center_on_screen(use_active_screen=True)
            self._relayout()

    def _setup_animation(self):
        """Konfiguriert den Animation-Timer (läuft nur während RECORDING)."""
        self._animation_timer = QTimer(self)
        self._animation_timer.timeout.connect(self._animate_frame)
        self._animation_timer.setTimerType(Qt.TimerType.PreciseTimer)

    def _setup_fade_animation(self):
        """Konfiguriert Fade-In/Out Animation."""
        self._fade_animation = QPropertyAnimation(self, b"windowOpacity")
        # Keep feedback crisp: 150ms felt sluggish on Windows hotkey/VAD transitions.
        self._fade_animation.setDuration(90)
        # Einmalig verbinden (nicht in _fade_out, um multiple connections zu vermeiden)
        self._fade_animation.finished.connect(self._on_fade_out_finished)
        # Reuse a single timer to avoid accumulating QObject children over long sessions.
        self._fade_out_timer = QTimer(self)
        self._fade_out_timer.setSingleShot(True)
        self._fade_out_timer.timeout.connect(self._fade_out)

    def _setup_clock(self):
        """One coarse 1 Hz timer for the recording duration (LISTENING/RECORDING)."""
        self._clock_timer = QTimer(self)
        self._clock_timer.setTimerType(Qt.TimerType.CoarseTimer)
        self._clock_timer.setInterval(CLOCK_INTERVAL_MS)
        self._clock_timer.timeout.connect(self._tick_clock)

    def _start_clock(self) -> None:
        if self._clock_timer.isActive():
            return
        self._clock_start = time.monotonic()
        self._meta_text = format_recording_duration(0)
        self._clock_timer.start()

    def _stop_clock(self) -> None:
        self._clock_timer.stop()
        self._meta_text = ""

    @Slot()
    def _tick_clock(self) -> None:
        # round(): coarse timers may fire a little early; still show the full second.
        text = format_recording_duration(round(time.monotonic() - self._clock_start))
        if text != self._meta_text:
            self._meta_text = text
            self._relayout()

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
        """Startet Timer für automatisches Ausblenden nach DONE/NO_SPEECH."""
        self._cancel_fade_out_timer()
        if self._fade_out_timer:
            self._fade_out_timer.start(FEEDBACK_DISPLAY_MS)

    def _cancel_fade_out_timer(self):
        """Bricht den Fade-Out Timer ab."""
        if self._fade_out_timer:
            self._fade_out_timer.stop()

    @Slot()
    def cleanup(self):
        """Stoppt alle Timer sauber (muss im Qt-Thread aufgerufen werden)."""
        self._cancel_fade_out_timer()
        if hasattr(self, "_animation_timer") and self._animation_timer:
            self._animation_timer.stop()
        if hasattr(self, "_clock_timer") and self._clock_timer:
            self._clock_timer.stop()
        if hasattr(self, "_fade_animation") and self._fade_animation:
            self._fade_animation.stop()

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
        if getattr(self, "_last_state_payload", None) == (state, text):
            return
        self._state = state
        state_changed = state != prev_state
        self._last_state_payload = (state, text)
        if state_changed and (prev_state == "RECORDING") != (state == "RECORDING"):
            self.recording_state_changed.emit(state == "RECORDING")

        if state == "IDLE":
            self._stop_clock()
            # Daemon returns to IDLE so hotkeys work; a persistent message stays.
            if state_changed and not self._persistent:
                self._fade_out()
            return

        # Bei Übergang von IDLE nicht vor dem ersten Paint per WinAPI blockieren.
        # Erst schnell anzeigen, dann auf den aktiven Monitor korrigieren.
        if prev_state == "IDLE":
            self._schedule_center_on_active_screen()

        if state_changed:
            self._interim = ("", "")
            self._previous_interim = ""
            if state == "RECORDING":
                self._start_animation()
            else:
                self._stop_animation()
            if state in RECORDING_CLOCK_STATES:
                self._start_clock()
            else:
                self._stop_clock()
        content = describe_overlay_content(state, text)
        # A new error after a dismissed one must show even within ERROR.
        reshow = content.dismissible and not self._persistent
        self._set_persistent(content.dismissible)
        self._set_content(content)
        if state_changed or reshow:
            self._fade_in()
        if state_changed:
            # Auto-Hide für kurze Ergebnisse; Fehler bleiben bis zum Klick
            if state in _AUTO_HIDE_STATES:
                self._start_fade_out_timer()

    def _set_persistent(self, persistent: bool) -> None:
        """Accept clicks only while a dismissible message is shown.

        Toggles ``WindowTransparentForInput`` on the native window so the
        overlay is not re-created or hidden (QWidget.setWindowFlags would).
        """
        if self._persistent == persistent:
            return
        self._persistent = persistent
        handle = self.windowHandle()
        if handle is None:
            self.setWindowFlag(Qt.WindowType.WindowTransparentForInput, not persistent)
        else:
            handle.setFlag(Qt.WindowType.WindowTransparentForInput, not persistent)

    def _dismiss(self) -> None:
        """Hide a persistent message after a click on the overlay."""
        if not self._persistent:
            return
        self._set_persistent(False)
        self._cancel_fade_out_timer()
        self._fade_out()

    def mousePressEvent(self, event):
        if self._persistent:
            self._dismiss()
            event.accept()
            return
        super().mousePressEvent(event)

    @Slot(float)
    def _on_level_changed(self, level: float):
        self._audio_level = level

    @Slot(str)
    def _on_interim_changed(self, text: str):
        if self._state != "RECORDING":
            return
        interim = split_recording_interim_text(
            text,
            self._previous_interim,
            FADED_RECORDING_INTERIM_MAX_CHARS,
            ellipsis=False,
        )
        self._previous_interim = text
        if interim == self._interim:
            return
        self._interim = interim
        self._relayout()

    def _set_content(self, content: OverlayContent) -> None:
        if content == self._content:
            # The clock may have been reset (new recording, same content).
            self._relayout()
            return
        self._content = content
        try:
            self.setAccessibleDescription(
                " ".join(part for part in (content.badge, content.text) if part)
            )
        except Exception:
            pass
        self._relayout()

    # =========================================================================
    # Layout (einmal pro Inhaltsänderung, nicht pro Frame)
    # =========================================================================

    def _relayout(self) -> None:
        """Measure badge and text, resize the window and cache paint runs."""
        content = self._content
        text_metrics = QFontMetricsF(self._text_font)
        earlier, newest = self._interim
        interim = bool(earlier or newest)
        full_text = earlier + newest if interim else content.text
        badge_width = 0.0
        if content.badge:
            badge_metrics = QFontMetricsF(self._badge_font)
            badge_width = badge_metrics.horizontalAdvance(content.badge) + 2 * BADGE_PADDING_H
        dismiss_width = (
            QFontMetricsF(self._dismiss_font).horizontalAdvance(DISMISS_GLYPH)
            if content.dismissible
            else 0
        )
        # The recording clock wins while it runs; otherwise the content's meta.
        meta = self._meta_text or content.meta
        self._meta_shown = meta
        layout = compute_overlay_layout(
            text_width=text_metrics.horizontalAdvance(full_text) if full_text else 0,
            meta_width=(
                QFontMetricsF(self._meta_font).horizontalAdvance(meta) if meta else 0
            ),
            dismiss_width=dismiss_width,
            status_width=badge_width,
            grow=content.dismissible,
            max_width=self._max_width(),
        )
        self._layout = layout
        self._badge_rect = None
        if content.badge:
            badge_height = QFontMetricsF(self._badge_font).height() + 2 * BADGE_PADDING_V
            self._badge_rect = QRectF(
                layout.status_x,
                round((OVERLAY_HEIGHT - badge_height) / 2),
                badge_width,
                badge_height,
            )
        self._text_runs = self._build_text_runs(
            layout, text_metrics, full_text, newest if interim else None
        )

        width = round(layout.width)
        if width != self.width():
            self.setFixedSize(width, OVERLAY_HEIGHT)
            self._move_to_bottom_center()
        self.update()

    def _build_text_runs(
        self,
        layout: OverlayLayout,
        metrics: QFontMetricsF,
        full_text: str,
        newest: str | None,
    ) -> list[tuple[float, str, QColor]]:
        """Place text runs in the text zone.

        Status text is elided at the end. Live text keeps its newest words:
        when it overflows, its tail ends at the zone edge and the start is cut
        off (painted under the left fade, see ``_text_clipped``).
        """
        self._text_clipped = False
        if not full_text:
            return []
        if newest is None:
            color_key = "text" if self._content.emphasized else "muted"
            shown = metrics.elidedText(
                full_text, Qt.TextElideMode.ElideRight, layout.text_width
            )
            return [(layout.text_x, shown, self._colors[color_key])]

        x = layout.text_x
        full_width = metrics.horizontalAdvance(full_text)
        if full_width > layout.text_width:
            x += layout.text_width - full_width
            self._text_clipped = True
        earlier = full_text[: len(full_text) - len(newest)]
        runs = []
        if earlier:
            runs.append((x, earlier, self._colors["interim"]))
        if newest:
            offset = metrics.horizontalAdvance(earlier)
            runs.append((x + offset, newest, self._colors["text"]))
        return runs

    def _reset_levels(self):
        """Setzt Audio-Level zurück."""
        self._audio_level = 0.0
        self._anim = AnimationLogic()
        self._bar_heights = [float(BAR_MIN_HEIGHT)] * BAR_COUNT
        self._painted_bar_heights = [float(BAR_MIN_HEIGHT)] * BAR_COUNT

    # =========================================================================
    # Animation (nur RECORDING)
    # =========================================================================

    def _start_animation(self):
        if not self._animation_timer.isActive():
            self._animation_start = time.perf_counter()
            self._animation_timer.start(FRAME_MS)

    def _stop_animation(self):
        self._animation_timer.stop()

    def _target_bar_heights(self, t: float) -> tuple[float, ...]:
        return tuple(self._anim.calculate_frame_heights(t, "RECORDING"))

    @Slot()
    def _animate_frame(self):
        if self._state != "RECORDING":
            self._stop_animation()
            return

        t = time.perf_counter() - self._animation_start
        painted_bar_heights = self._painted_bar_heights

        self._anim.update_level(self._audio_level)
        self._anim.update_agc()

        needs_repaint = False
        for i, target_height in enumerate(self._target_bar_heights(t)):
            # Per-Bar Smoothing
            bar_alpha = 0.4 if target_height > self._bar_heights[i] else 0.15
            self._bar_heights[i] += bar_alpha * (target_height - self._bar_heights[i])
            if (
                not needs_repaint
                and abs(painted_bar_heights[i] - self._bar_heights[i])
                >= BAR_HEIGHT_UPDATE_EPSILON
            ):
                needs_repaint = True

        if not needs_repaint:
            return

        self._painted_bar_heights = list(self._bar_heights)
        self.update()

    # =========================================================================
    # Painting
    # =========================================================================

    def paintEvent(self, event):
        """Zeichnet Hintergrund, Balken, Badge und Text."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
        painter.setPen(QPen(QColor(BORDER_COLOR), 1))
        painter.setBrush(QColor(BACKGROUND_COLOR))
        painter.drawRoundedRect(rect, OVERLAY_CORNER_RADIUS, OVERLAY_CORNER_RADIUS)

        self._draw_bars(painter)
        self._draw_badge(painter)
        self._draw_text(painter)
        if self._layout.meta_x is not None and self._meta_shown:
            painter.setPen(self._meta_color)
            painter.setFont(self._meta_font)
            painter.drawText(
                QRectF(self._layout.meta_x, 0, self.width(), OVERLAY_HEIGHT),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                self._meta_shown,
            )
        if self._layout.dismiss_x is not None:
            painter.setPen(self._colors["muted"])
            painter.setFont(self._dismiss_font)
            painter.drawText(
                QRectF(self._layout.dismiss_x, 0, self.width(), OVERLAY_HEIGHT),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                DISMISS_GLYPH,
            )
        painter.end()

    def _draw_text(self, painter: QPainter):
        if not self._text_runs:
            return
        layout = self._layout
        zone = QRectF(layout.text_x, 0, layout.text_width, OVERLAY_HEIGHT)
        painter.save()
        painter.setClipRect(zone)
        painter.setFont(self._text_font)
        for x, text, color in self._text_runs:
            painter.setPen(color)
            painter.drawText(
                QRectF(x, 0, layout.text_x + layout.text_width - x, OVERLAY_HEIGHT),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                text,
            )
        if self._text_clipped:
            # Static fade over the cut; the background is solid, so a gradient
            # to transparent looks like an opacity mask without an offscreen pass.
            # Inset by the 1 px border so the frame stays intact.
            fade = QRectF(layout.text_x, 1, TEXT_FADE_WIDTH, OVERLAY_HEIGHT - 2)
            gradient = QLinearGradient(fade.left(), 0, fade.right(), 0)
            background = QColor(BACKGROUND_COLOR)
            gradient.setColorAt(0.0, background)
            background.setAlpha(0)
            gradient.setColorAt(1.0, background)
            painter.fillRect(fade, gradient)
        painter.restore()

    def _draw_bars(self, painter: QPainter):
        mode = self._content.bars
        bars_x = self._layout.status_x
        steps = self._step_colors.get(mode)
        if steps is not None:
            painter.setPen(Qt.PenStyle.NoPen)
            for index, color in enumerate(steps):
                painter.setBrush(color)
                x = bars_x + index * (PHASE_STEP_WIDTH + PHASE_STEP_GAP)
                painter.drawRoundedRect(
                    QRectF(x, BAR_CENTER_Y - LINE_HEIGHT / 2, PHASE_STEP_WIDTH, LINE_HEIGHT),
                    LINE_HEIGHT / 2,
                    LINE_HEIGHT / 2,
                )
            return
        if mode not in self._bar_colors:
            return
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self._bar_colors[mode])
        if mode == "line":
            painter.drawRoundedRect(
                QRectF(bars_x, BAR_CENTER_Y - LINE_HEIGHT / 2, BAR_AREA_WIDTH, LINE_HEIGHT),
                LINE_HEIGHT / 2,
                LINE_HEIGHT / 2,
            )
            return
        heights = self._bar_heights if mode == "level" else LISTENING_BAR_HEIGHTS
        radius = BAR_WIDTH / 2
        for i, height in enumerate(heights):
            height = max(BAR_MIN_HEIGHT, height)
            x = bars_x + i * (BAR_WIDTH + BAR_GAP)
            painter.drawRoundedRect(
                QRectF(x, BAR_CENTER_Y - height / 2, BAR_WIDTH, height), radius, radius
            )

    def _draw_badge(self, painter: QPainter):
        rect = self._badge_rect
        content = self._content
        if rect is None or not content.badge:
            return
        background, foreground = self._badge_colors[content.tone]
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(background)
        painter.drawRoundedRect(rect, BADGE_CORNER_RADIUS, BADGE_CORNER_RADIUS)
        painter.setPen(foreground)
        painter.setFont(self._badge_font)
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, content.badge)


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
        self._last_interim_mtime_ns: int | None = None
        self._stable_interim_polls = 0
        self._interim_timer: QTimer | None = None
        self._direct_interim_until = 0.0
        self._last_requested_state_payload: tuple[str, str] | None = None
        self._last_requested_interim_text = ""
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
            self._widget.recording_state_changed.connect(
                self._set_interim_polling_active
            )
            self._set_interim_polling_active(
                self._widget.current_state == "RECORDING"
            )

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

    def wait_until_ready(self, timeout: float | None = None) -> bool:
        """Wait briefly until the Qt widget is ready for first state updates."""
        return self._ready_event.wait(timeout=timeout)

    def update_state(self, state: str, text: str | None = None):
        """Thread-safe State-Update."""
        normalized_text = text or ""
        payload = (state, normalized_text)
        if (
            payload == getattr(self, "_pending_state", None)
            or payload == getattr(self, "_last_requested_state_payload", None)
        ):
            return
        self._last_requested_state_payload = payload
        if self._widget:
            self._widget.update_state(state, normalized_text)
        else:
            # Widget noch nicht bereit - State für später speichern
            self._pending_state = payload

    def update_audio_level(self, level: float):
        """Thread-safe Level-Update."""
        if self._widget:
            self._widget.update_audio_level(level)

    def update_interim_text(self, text: str):
        """Thread-safe Interim-Text-Update."""
        normalized_text = text or ""
        self._direct_interim_until = time.monotonic() + INTERIM_DIRECT_UPDATE_GRACE_S
        self._stable_interim_polls = 0
        if normalized_text == getattr(self, "_last_requested_interim_text", ""):
            return
        self._last_requested_interim_text = normalized_text
        if self._widget:
            self._widget.update_interim_text(normalized_text)

    def _poll_interim_file(self):
        """Pollt Interim-File für Text-Updates."""
        if not self._running or not self._interim_file or not self._widget:
            return

        if self._widget.current_state != "RECORDING":
            self._last_interim_mtime_ns = None
            self._last_interim_text = ""
            self._stable_interim_polls = 0
            self._direct_interim_until = 0.0
            self._last_requested_interim_text = ""
            return

        if time.monotonic() < getattr(self, "_direct_interim_until", 0.0):
            self._apply_interim_poll_interval()
            return

        if not self._interim_file.exists():
            if self._last_interim_text:
                self._widget.update_interim_text("")
                self._stable_interim_polls = 0
            else:
                self._stable_interim_polls += 1
            self._last_interim_mtime_ns = None
            self._last_interim_text = ""
            self._last_requested_interim_text = ""
            self._apply_interim_poll_interval()
            return

        try:
            current_mtime_ns = self._interim_file.stat().st_mtime_ns
            if current_mtime_ns == self._last_interim_mtime_ns:
                self._stable_interim_polls += 1
                self._apply_interim_poll_interval()
                return

            text = read_file_tail_text(
                self._interim_file,
                max_chars=INTERIM_POLL_MAX_CHARS,
                errors="replace",
            ).strip()
            self._last_interim_mtime_ns = current_mtime_ns
            if text != self._last_interim_text:
                self._last_interim_text = text
                self._stable_interim_polls = 0
                self._widget.update_interim_text(text)
            else:
                self._stable_interim_polls += 1
            self._apply_interim_poll_interval()
        except Exception as e:
            logger.debug(f"Interim-File lesen fehlgeschlagen: {e}")

    @Slot(bool)
    def _set_interim_polling_active(self, active: bool) -> None:
        """Steuert Interim-Polling state-basiert, um Idle-CPU-Last zu senken."""
        if not self._interim_timer:
            return

        if active:
            self._stable_interim_polls = 0
            if not self._interim_timer.isActive():
                self._interim_timer.start(INTERIM_POLL_INTERVAL_MS)
            elif self._interim_timer.interval() != INTERIM_POLL_INTERVAL_MS:
                self._interim_timer.setInterval(INTERIM_POLL_INTERVAL_MS)
            return

        if self._interim_timer.isActive():
            self._interim_timer.stop()
        self._stable_interim_polls = 0
        self._last_interim_mtime_ns = None
        self._last_interim_text = ""
        self._direct_interim_until = 0.0
        self._last_requested_interim_text = ""

    def _apply_interim_poll_interval(self) -> None:
        """Apply polling backoff from the timer's owning Qt thread."""
        interim_timer = getattr(self, "_interim_timer", None)
        if not interim_timer or not interim_timer.isActive():
            return
        if time.monotonic() < getattr(self, "_direct_interim_until", 0.0):
            target_interval = INTERIM_POLL_DIRECT_INTERVAL_MS
        elif self._stable_interim_polls >= INTERIM_POLL_STABLE_THRESHOLD:
            target_interval = INTERIM_POLL_STABLE_INTERVAL_MS
        else:
            target_interval = INTERIM_POLL_INTERVAL_MS
        if interim_timer.interval() != target_interval:
            interim_timer.setInterval(target_interval)


__all__ = ["PySide6OverlayController", "PySide6OverlayWidget"]
