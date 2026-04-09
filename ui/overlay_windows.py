"""Windows Overlay für PulseScribe.

Tkinter-basiertes Overlay mit animierten Waveform-Bars.
Zeigt Status und Interim-Text während der Aufnahme.

Inspiriert vom macOS-Overlay mit:
- Traveling Wave Animation
- Gaussian Envelope (wanderndes Energie-Paket)
- Pill-förmige Bars mit abgerundeten Enden
- Unterschiedliches Smoothing für Rise/Fall
"""

import logging
import queue
import sys
import time
import tkinter as tk
from pathlib import Path

from ui.animation import (
    AnimationLogic,
    BAR_COUNT,
    BAR_WIDTH,
    BAR_GAP,
    BAR_MIN_HEIGHT,
    FPS,
)
from utils.log_tail import read_file_tail_text
from utils.log_tail import get_file_signature

logger = logging.getLogger("pulsescribe.overlay")

# =============================================================================
# Window-Konstanten
# =============================================================================

WINDOW_WIDTH = 280
WINDOW_HEIGHT = 90
WINDOW_CORNER_RADIUS = 16
WINDOW_MARGIN_BOTTOM = 60  # Abstand vom unteren Bildschirmrand

# =============================================================================
# Animation-Konstanten
# =============================================================================

FRAME_MS = 1000 // FPS  # ~16ms
FRAME_MS_ACTIVE = 1000 // 30  # 30 FPS für nicht-kritische Animationen
FRAME_MS_FEEDBACK = 1000 // 20  # 20 FPS für kurze DONE/ERROR-Phase
BAR_HEIGHT_UPDATE_EPSILON = 0.25  # Spare Canvas-Updates für subpixel-kleine Änderungen
QUEUE_POLL_ACTIVE_MS = 16  # 60Hz während Overlay sichtbar/aktiv
QUEUE_POLL_ACTIVE_IDLE_MS = 33  # Weniger Wakeups wenn die UI aktiv, die Queue aber leer ist
QUEUE_POLL_IDLE_MS = 50  # Weniger CPU-Last im Idle
QUEUE_MAX_MESSAGES_PER_TICK = 200
INTERIM_QUEUE_BACKPRESSURE_LIMIT = 120
INTERIM_POLL_MAX_CHARS = 512
INTERIM_POLL_INTERVAL_MS = 200
INTERIM_POLL_STABLE_INTERVAL_MS = 500
INTERIM_POLL_STABLE_THRESHOLD = 3
INTERIM_POLL_DIRECT_INTERVAL_MS = 1000
INTERIM_DIRECT_UPDATE_GRACE_S = 1.5

# =============================================================================
# Farben
# =============================================================================

BG_COLOR = "#1A1A1A"  # Etwas dunkler als vorher

STATE_COLORS = {
    "LISTENING": "#FFB6C1",  # Pink
    "RECORDING": "#FF5252",  # Rot
    "TRANSCRIBING": "#FFB142",  # Orange
    "REFINING": "#9C27B0",  # Lila
    "LOADING": "#42A5F5",  # Blau
    "DONE": "#4CAF50",  # Grün (satter)
    "ERROR": "#FF4757",  # Rot
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

# =============================================================================
# Overlay Controller
# =============================================================================


def _format_recording_interim_text(text: str, max_chars: int = 45) -> str:
    """Normalize and tail-truncate recording interim text for compact overlays."""
    if not text:
        return ""

    normalized_tail_reversed: list[str] = []
    normalized_length = 0
    pending_space = False
    truncated = False

    for char in reversed(text):
        if char.isspace():
            if normalized_length > 0:
                pending_space = True
            continue

        if pending_space:
            normalized_tail_reversed.append(" ")
            normalized_length += 1
            pending_space = False

        normalized_tail_reversed.append(char)
        normalized_length += 1
        if max_chars > 0 and normalized_length > max_chars:
            truncated = True
            break

    if not normalized_tail_reversed:
        return ""

    cleaned_tail = "".join(reversed(normalized_tail_reversed))
    if not truncated:
        return cleaned_tail
    if max_chars <= 0:
        return cleaned_tail

    tail_chars = max_chars - 3
    if tail_chars <= 0:
        return "..."
    return "..." + cleaned_tail[-tail_chars:]


class WindowsOverlayController:
    """Tkinter-basiertes Overlay für Windows.

    Features:
    - Traveling Wave Animation
    - Gaussian Envelope
    - Pill-förmige Bars
    - Abgerundete Fensterecken
    - Thread-safe via Queue
    """

    def __init__(self, interim_file: Path | None = None):
        self._root: tk.Tk | None = None
        self._canvas: tk.Canvas | None = None
        self._label: tk.Label | None = None

        self._state = "IDLE"
        self._audio_level = 0.0
        self._anim = AnimationLogic()
        self._bar_heights: list[float] = [float(BAR_MIN_HEIGHT)] * BAR_COUNT
        self._drawn_bar_heights: list[float] = [float(BAR_MIN_HEIGHT)] * BAR_COUNT

        # Animation timing
        self._animation_start = time.perf_counter()
        self._animation_running = False

        self._queue: queue.Queue = queue.Queue()
        self._running = False
        self._bar_item_ids: list[tuple[int, int, int]] = []
        self._bar_color: str | None = None
        self._last_label_config: tuple[str, tuple[object, ...], str] | None = None
        self._interim_file = interim_file
        self._last_interim_text = ""
        self._last_interim_signature: tuple[int, int] | None = None
        self._stable_interim_polls = 0
        self._interim_polling_active = False
        self._interim_poll_after_id: str | None = None
        self._direct_interim_until = 0.0
        self._last_state_payload: tuple[str, str] | None = None
        self._queued_state_payload: tuple[str, str] | None = None

    # =========================================================================
    # Public API (thread-safe)
    # =========================================================================

    def update_state(self, state: str, text: str | None = None) -> None:
        normalized_text = text or ""
        payload = (state, normalized_text)
        if (
            payload == getattr(self, "_queued_state_payload", None)
            or payload == getattr(self, "_last_state_payload", None)
        ):
            return
        self._queued_state_payload = payload
        self._queue.put(("state", state, normalized_text))

    def update_audio_level(self, level: float) -> None:
        """Aktualisiert Audio-Level ohne Queue-Druck.

        Audio-Level wird sehr häufig aktualisiert (pro Audio-Callback). Das
        direkte Setzen vermeidet Queue-Wachstum im Tkinter-Fallback und hält
        die Animation bei längerer Laufzeit stabil.
        """
        self._audio_level = level

    def update_interim_text(self, text: str) -> None:
        normalized_text = text or ""
        self._direct_interim_until = (
            time.monotonic() + INTERIM_DIRECT_UPDATE_GRACE_S
        )
        if normalized_text == self._last_interim_text:
            return
        # Bei hoher Last ältere Interim-Updates verwerfen, damit State-Updates
        # (DONE/ERROR) nicht durch veraltete Text-Events ausgebremst werden.
        try:
            if self._queue.qsize() >= INTERIM_QUEUE_BACKPRESSURE_LIMIT:
                return
        except NotImplementedError:
            # qsize() ist auf manchen Plattformen optional; dann normal enqueuen.
            pass
        self._last_interim_text = normalized_text
        self._last_interim_signature = None
        self._queue.put(("interim", normalized_text, None))

    def run(self) -> None:
        """Start tkinter mainloop (call from dedicated thread)."""
        self._root = tk.Tk()
        self._setup_window()
        self._running = True
        self._animation_start = time.perf_counter()
        self._poll_queue()
        self._start_animation_loop()
        self._root.mainloop()

    def stop(self) -> None:
        self._running = False
        self._set_interim_polling_active(False)
        if self._root:
            try:
                self._root.after(0, self._root.destroy)
            except Exception:
                pass

    # =========================================================================
    # Window Setup
    # =========================================================================

    def _setup_window(self) -> None:
        if not self._root:
            return

        self._root.title("PulseScribe")
        self._root.overrideredirect(True)
        self._root.attributes("-topmost", True)
        self._root.attributes("-alpha", 0.95)
        self._root.configure(bg=BG_COLOR)

        # Position: bottom-center (primär)
        self._position_window(use_active_monitor=False)

        # Main Canvas (für abgerundeten Hintergrund + Bars)
        self._canvas = tk.Canvas(
            self._root,
            width=WINDOW_WIDTH,
            height=WINDOW_HEIGHT,
            bg=BG_COLOR,
            highlightthickness=0,
        )
        self._canvas.pack(fill="both", expand=True)

        # Abgerundeter Hintergrund
        self._draw_rounded_background()

        # Label für Text (über Canvas platziert)
        self._label = tk.Label(
            self._root,
            text="",
            fg="white",
            bg=BG_COLOR,
            font=("Segoe UI", 11),
        )
        # Platzieren am unteren Rand des Canvas
        self._label.place(relx=0.5, rely=0.85, anchor="center")

        self._root.withdraw()
        logger.debug("Overlay window initialized")

    def _draw_rounded_rect(
        self,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        radius: float,
        fill: str,
        outline: str = "",
    ) -> None:
        """Zeichnet ein Rechteck mit abgerundeten Ecken."""
        if not self._canvas:
            return

        r = min(radius, (x2 - x1) / 2, (y2 - y1) / 2)

        # Vier Ecken als Arcs
        # Oben-links
        self._canvas.create_arc(
            x1,
            y1,
            x1 + 2 * r,
            y1 + 2 * r,
            start=90,
            extent=90,
            fill=fill,
            outline=outline,
            tags="bg",
        )
        # Oben-rechts
        self._canvas.create_arc(
            x2 - 2 * r,
            y1,
            x2,
            y1 + 2 * r,
            start=0,
            extent=90,
            fill=fill,
            outline=outline,
            tags="bg",
        )
        # Unten-rechts
        self._canvas.create_arc(
            x2 - 2 * r,
            y2 - 2 * r,
            x2,
            y2,
            start=270,
            extent=90,
            fill=fill,
            outline=outline,
            tags="bg",
        )
        # Unten-links
        self._canvas.create_arc(
            x1,
            y2 - 2 * r,
            x1 + 2 * r,
            y2,
            start=180,
            extent=90,
            fill=fill,
            outline=outline,
            tags="bg",
        )

        # Verbindende Rechtecke
        # Oben
        self._canvas.create_rectangle(
            x1 + r, y1, x2 - r, y1 + r, fill=fill, outline="", tags="bg"
        )
        # Mitte
        self._canvas.create_rectangle(
            x1, y1 + r, x2, y2 - r, fill=fill, outline="", tags="bg"
        )
        # Unten
        self._canvas.create_rectangle(
            x1 + r, y2 - r, x2 - r, y2, fill=fill, outline="", tags="bg"
        )

    def _draw_rounded_background(self) -> None:
        """Zeichnet abgerundeten Hintergrund."""
        if not self._canvas:
            return

        self._canvas.delete("bg")
        self._draw_rounded_rect(
            0, 0, WINDOW_WIDTH, WINDOW_HEIGHT, WINDOW_CORNER_RADIUS, BG_COLOR
        )

    # =========================================================================
    # Queue Processing
    # =========================================================================

    def _poll_queue(self) -> None:
        processed_message = False
        latest_interim_text: str | None = None
        processed_count = 0
        try:
            while processed_count < QUEUE_MAX_MESSAGES_PER_TICK:
                msg_type, value, text = self._queue.get_nowait()
                processed_message = True
                processed_count += 1
                if msg_type == "state":
                    self._handle_state_change(value, text)
                elif msg_type == "level":
                    self._audio_level = value
                elif msg_type == "interim":
                    latest_interim_text = value
        except queue.Empty:
            pass

        if latest_interim_text is not None:
            self._handle_interim_text(latest_interim_text)

        if self._running and self._root:
            has_backlog = processed_count >= QUEUE_MAX_MESSAGES_PER_TICK
            poll_ms = (
                QUEUE_POLL_ACTIVE_MS
                if processed_message or has_backlog
                else (
                    QUEUE_POLL_IDLE_MS
                    if self._state == "IDLE"
                    else QUEUE_POLL_ACTIVE_IDLE_MS
                )
            )
            self._root.after(poll_ms, self._poll_queue)

    def _poll_interim_file(self) -> None:
        self._interim_poll_after_id = None
        if not self._running or not self._root or not self._interim_file:
            return
        if not self._interim_polling_active:
            return
        if self._state != "RECORDING":
            self._last_interim_text = ""
            self._last_interim_signature = None
            self._direct_interim_until = 0.0
            return

        poll_interval_ms = self._current_interim_poll_interval_ms()
        if time.monotonic() < getattr(self, "_direct_interim_until", 0.0):
            poll_interval_ms = INTERIM_POLL_DIRECT_INTERVAL_MS
            if self._running and self._interim_polling_active:
                self._interim_poll_after_id = self._root.after(
                    poll_interval_ms, self._poll_interim_file
                )
            return

        signature = (
            get_file_signature(self._interim_file)
            if self._state == "RECORDING"
            else None
        )
        if self._state == "RECORDING" and signature is None:
            if self._last_interim_text:
                self._last_interim_text = ""
                self._last_interim_signature = None
                self._stable_interim_polls = 0
                self._handle_interim_text("")
            else:
                self._stable_interim_polls += 1
        elif self._state == "RECORDING":
            try:
                if signature == self._last_interim_signature:
                    self._stable_interim_polls += 1
                    if self._running and self._interim_polling_active:
                        self._interim_poll_after_id = self._root.after(
                            self._current_interim_poll_interval_ms(),
                            self._poll_interim_file,
                        )
                    return

                text = read_file_tail_text(
                    self._interim_file,
                    max_chars=INTERIM_POLL_MAX_CHARS,
                    errors="replace",
                ).strip()
                self._last_interim_signature = signature
                if text != self._last_interim_text:
                    self._last_interim_text = text
                    self._stable_interim_polls = 0
                    self._handle_interim_text(text)
                else:
                    self._stable_interim_polls += 1
            except Exception:
                pass

        if self._running and self._interim_polling_active:
            self._interim_poll_after_id = self._root.after(
                self._current_interim_poll_interval_ms(),
                self._poll_interim_file,
            )

    def _set_interim_polling_active(self, active: bool) -> None:
        if not self._root or not self._interim_file:
            return

        if active:
            if self._interim_polling_active:
                return
            self._interim_polling_active = True
            self._interim_poll_after_id = self._root.after(0, self._poll_interim_file)
            return

        self._interim_polling_active = False
        if self._interim_poll_after_id is not None:
            try:
                self._root.after_cancel(self._interim_poll_after_id)
            except Exception:
                pass
            self._interim_poll_after_id = None
        self._stable_interim_polls = 0
        self._last_interim_text = ""
        self._last_interim_signature = None
        self._direct_interim_until = 0.0

    def _current_interim_poll_interval_ms(self) -> int:
        if self._stable_interim_polls >= INTERIM_POLL_STABLE_THRESHOLD:
            return INTERIM_POLL_STABLE_INTERVAL_MS
        return INTERIM_POLL_INTERVAL_MS

    # =========================================================================
    # State Handling
    # =========================================================================

    def _handle_state_change(self, state: str, text: str | None) -> None:
        normalized_text = text or ""
        payload = (state, normalized_text)
        self._queued_state_payload = None
        if payload == getattr(self, "_last_state_payload", None):
            return
        self._last_state_payload = payload
        prev_state = self._state
        self._state = state
        state_changed = state != prev_state

        if not self._root or not self._label:
            return

        if state == "IDLE":
            if state_changed:
                self._root.withdraw()
                self._last_interim_text = ""
                self._last_interim_signature = None
                self._stable_interim_polls = 0
                self._direct_interim_until = 0.0
                self._last_label_config = None
                self._bar_color = None
                self._anim = AnimationLogic()
                self._drawn_bar_heights = [float(BAR_MIN_HEIGHT)] * BAR_COUNT
        else:
            if prev_state == "IDLE":
                # Bei Start auf Monitor des aktiven Fensters zentrieren.
                self._position_window(use_active_monitor=True)
            if state_changed:
                self._start_animation_loop()
                self._root.deiconify()
            display_text = normalized_text or STATE_TEXTS.get(state, "")
            label_color = (
                STATE_COLORS.get(state, "white")
                if state in ("DONE", "ERROR")
                else "white"
            )
            self._set_label_config(
                text=display_text,
                font=("Segoe UI", 11),
                fg=label_color,
            )
            if state != "RECORDING":
                self._direct_interim_until = 0.0

        if state_changed:
            self._set_interim_polling_active(state == "RECORDING")

        if state_changed:
            self._animation_start = time.perf_counter()

    def _handle_interim_text(self, text: str) -> None:
        if self._state != "RECORDING" or not self._label:
            return

        formatted = _format_recording_interim_text(text)
        if not formatted:
            self._set_label_config(
                text=STATE_TEXTS["RECORDING"],
                font=("Segoe UI", 11),
                fg="white",
            )
            return

        self._set_label_config(
            text=formatted,
            font=("Segoe UI", 10, "italic"),
            fg="#909090",
        )

    def _set_label_config(
        self, *, text: str, font: tuple[object, ...], fg: str
    ) -> None:
        if not self._label:
            return

        config = (text, font, fg)
        if getattr(self, "_last_label_config", None) == config:
            return

        self._last_label_config = config
        self._label.config(text=text, font=font, fg=fg)

    def _position_window(self, use_active_monitor: bool) -> None:
        if not self._root:
            return

        work_area = (
            self._get_active_monitor_work_area()
            if use_active_monitor
            else None
        )
        if work_area is None:
            work_area = self._get_primary_work_area()

        left, top, width, height = work_area
        x = left + (width - WINDOW_WIDTH) // 2
        y = top + height - WINDOW_HEIGHT - WINDOW_MARGIN_BOTTOM
        self._root.geometry(f"{WINDOW_WIDTH}x{WINDOW_HEIGHT}+{x}+{y}")

    def _get_primary_work_area(self) -> tuple[int, int, int, int]:
        if sys.platform == "win32":
            try:
                from ctypes import wintypes
                import ctypes

                SPI_GETWORKAREA = 0x0030
                rect = wintypes.RECT()
                user32 = ctypes.windll.user32
                if user32.SystemParametersInfoW(
                    SPI_GETWORKAREA, 0, ctypes.byref(rect), 0
                ):
                    return (
                        int(rect.left),
                        int(rect.top),
                        int(rect.right - rect.left),
                        int(rect.bottom - rect.top),
                    )
            except Exception:
                pass

        if not self._root:
            return 0, 0, WINDOW_WIDTH, WINDOW_HEIGHT
        return (
            0,
            0,
            int(self._root.winfo_screenwidth()),
            int(self._root.winfo_screenheight()),
        )

    def _get_active_monitor_work_area(self) -> tuple[int, int, int, int] | None:
        if sys.platform != "win32":
            return None

        try:
            from ctypes import wintypes
            import ctypes

            user32 = ctypes.windll.user32

            class RECT(ctypes.Structure):
                _fields_ = [
                    ("left", wintypes.LONG),
                    ("top", wintypes.LONG),
                    ("right", wintypes.LONG),
                    ("bottom", wintypes.LONG),
                ]

            class MONITORINFO(ctypes.Structure):
                _fields_ = [
                    ("cbSize", wintypes.DWORD),
                    ("rcMonitor", RECT),
                    ("rcWork", RECT),
                    ("dwFlags", wintypes.DWORD),
                ]

            MONITOR_DEFAULTTONEAREST = 2
            hwnd = user32.GetForegroundWindow()
            if not hwnd:
                return None

            monitor = user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
            if not monitor:
                return None

            monitor_info = MONITORINFO()
            monitor_info.cbSize = ctypes.sizeof(MONITORINFO)
            if not user32.GetMonitorInfoW(monitor, ctypes.byref(monitor_info)):
                return None

            work = monitor_info.rcWork
            return (
                int(work.left),
                int(work.top),
                int(work.right - work.left),
                int(work.bottom - work.top),
            )
        except Exception:
            return None

    # =========================================================================
    # Animation
    # =========================================================================

    def _start_animation_loop(self) -> None:
        if self._animation_running:
            return
        self._animation_running = True
        self._animate()

    def _animate(self) -> None:
        if not self._running or not self._root:
            self._animation_running = False
            return

        if self._state == "IDLE":
            self._animation_running = False
            return

        # Zeit seit Animation-Start
        t = time.perf_counter() - self._animation_start

        # Audio-Level an Animation-Logik übergeben
        self._anim.update_level(self._audio_level)

        # AGC: Einmal pro Frame berechnen (nicht pro Bar)
        if self._state == "RECORDING":
            self._anim.update_agc()

        self._render_bars(t)
        self._root.after(self._frame_interval_ms(), self._animate)

    def _frame_interval_ms(self) -> int:
        """Gibt ein state-abhängiges Frame-Intervall zurück.

        RECORDING bleibt bei 60 FPS für maximale Responsiveness.
        Andere States laufen mit reduzierter Framerate, um CPU-Last zu senken.
        """
        if self._state == "RECORDING":
            return FRAME_MS
        if self._state in ("DONE", "ERROR"):
            return FRAME_MS_FEEDBACK
        return FRAME_MS_ACTIVE

    def _render_bars(self, t: float) -> None:
        if not self._canvas:
            return

        color = STATE_COLORS.get(self._state, "#FFFFFF")

        # Bar-Positionen berechnen
        total_width = BAR_COUNT * BAR_WIDTH + (BAR_COUNT - 1) * BAR_GAP
        start_x = (WINDOW_WIDTH - total_width) // 2
        center_y = 35  # Etwas höher für Text darunter
        self._ensure_pill_bar_items(start_x, center_y)
        self._set_bar_color(color)

        for i in range(BAR_COUNT):
            target = self._anim.calculate_bar_height(i, t, self._state)

            # Smoothing pro Bar
            if target > self._bar_heights[i]:
                alpha = 0.4
            else:
                alpha = 0.15
            self._bar_heights[i] += alpha * (target - self._bar_heights[i])
            height = max(BAR_MIN_HEIGHT, self._bar_heights[i])

            # Pill-förmige Bar zeichnen
            x = start_x + i * (BAR_WIDTH + BAR_GAP)
            self._draw_pill_bar(i, x, center_y, BAR_WIDTH, height)

    def _ensure_pill_bar_items(self, start_x: float, center_y: float) -> None:
        """Erstellt Canvas-Items einmalig und reused sie pro Frame."""
        if not self._canvas:
            return
        if not hasattr(self, "_bar_item_ids"):
            self._bar_item_ids = []
        if not hasattr(self, "_drawn_bar_heights") or len(self._drawn_bar_heights) != BAR_COUNT:
            self._drawn_bar_heights = [float(BAR_MIN_HEIGHT)] * BAR_COUNT
        if len(self._bar_item_ids) == BAR_COUNT:
            return

        self._bar_item_ids = []
        self._drawn_bar_heights = [float(BAR_MIN_HEIGHT)] * BAR_COUNT
        for i in range(BAR_COUNT):
            x = start_x + i * (BAR_WIDTH + BAR_GAP)
            y1 = center_y - BAR_MIN_HEIGHT / 2
            y2 = center_y + BAR_MIN_HEIGHT / 2
            top_arc = self._canvas.create_arc(
                x,
                y1,
                x + BAR_WIDTH,
                y1 + BAR_WIDTH,
                start=0,
                extent=180,
                fill="#FFFFFF",
                outline="",
                tags="bars",
            )
            middle_rect = self._canvas.create_rectangle(
                x,
                y1 + BAR_WIDTH / 2,
                x + BAR_WIDTH,
                y2 - BAR_WIDTH / 2,
                fill="#FFFFFF",
                outline="",
                tags="bars",
            )
            bottom_arc = self._canvas.create_arc(
                x,
                y2 - BAR_WIDTH,
                x + BAR_WIDTH,
                y2,
                start=180,
                extent=180,
                fill="#FFFFFF",
                outline="",
                tags="bars",
            )
            self._bar_item_ids.append((top_arc, middle_rect, bottom_arc))

    def _set_bar_color(self, color: str) -> None:
        if not self._canvas:
            return
        if getattr(self, "_bar_color", None) == color and self._bar_item_ids:
            return

        self._bar_color = color
        for top_arc, middle_rect, bottom_arc in self._bar_item_ids:
            self._canvas.itemconfig(top_arc, fill=color, outline="")
            self._canvas.itemconfig(middle_rect, fill=color, outline="")
            self._canvas.itemconfig(bottom_arc, fill=color, outline="")

    def _draw_pill_bar(
        self,
        bar_index: int,
        x: float,
        center_y: float,
        width: float,
        height: float,
    ) -> None:
        """Zeichnet eine Pill-förmige Bar (abgerundete Enden)."""
        if not self._canvas:
            return
        if not hasattr(self, "_bar_item_ids") or len(self._bar_item_ids) <= bar_index:
            return
        if not hasattr(self, "_drawn_bar_heights") or len(self._drawn_bar_heights) != BAR_COUNT:
            self._drawn_bar_heights = [float(BAR_MIN_HEIGHT)] * BAR_COUNT

        top_arc, middle_rect, bottom_arc = self._bar_item_ids[bar_index]
        height = max(height, width)
        if (
            bar_index < len(self._drawn_bar_heights)
            and abs(self._drawn_bar_heights[bar_index] - height)
            < BAR_HEIGHT_UPDATE_EPSILON
        ):
            return

        y1 = center_y - height / 2
        y2 = center_y + height / 2
        rect_top = y1 + width / 2
        rect_bottom = y2 - width / 2
        if rect_bottom < rect_top:
            rect_bottom = rect_top

        self._canvas.coords(top_arc, x, y1, x + width, y1 + width)
        self._canvas.coords(middle_rect, x, rect_top, x + width, rect_bottom)
        self._canvas.coords(bottom_arc, x, y2 - width, x + width, y2)
        if bar_index < len(self._drawn_bar_heights):
            self._drawn_bar_heights[bar_index] = height


__all__ = ["WindowsOverlayController"]
