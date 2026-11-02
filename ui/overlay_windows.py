"""Windows Overlay für PulseScribe (Tkinter-Fallback ohne PySide6).

Nutzt dieselben Farben und Inhalte wie macOS/PySide6 (ui/overlay_style.py,
``describe_overlay_content``), aber vereinfacht: feste Breite (auch bleibende
Meldungen wachsen nicht), Interim-Text per Tail-Kürzung statt Fade und
einfarbig gedämpft. Nur RECORDING animiert; alle anderen States sind statisch.
Fehler bleiben bis zum Klick sichtbar; das Tk-Fenster ist (wie bisher) nie
klick-durchlässig, weil Tk das nicht pro State umschalten kann.
"""

from __future__ import annotations

import logging
import queue
import sys
import time
from pathlib import Path

try:
    import tkinter as tk
except ImportError as exc:  # pragma: no cover - depends on host Python build
    tk = None
    _TK_IMPORT_ERROR: ImportError | None = exc
else:
    _TK_IMPORT_ERROR = None

from ui.animation import (
    AnimationLogic,
    BAR_COUNT,
    BAR_WIDTH,
    BAR_GAP,
    BAR_MIN_HEIGHT,
    FPS,
    LISTENING_BAR_HEIGHTS,
)
from ui.overlay_feedback import (
    RECORDING_CLOCK_STATES,
    OverlayContent,
    describe_overlay_content,
)
from ui.overlay_style import (
    BACKGROUND_COLOR,
    BADGE_PADDING_H,
    BADGE_TONES,
    BAR_AREA_WIDTH,
    BAR_COLORS,
    DISMISS_GLYPH,
    INTERIM_TEXT_COLOR,
    LINE_HEIGHT,
    META_TEXT_COLOR,
    MUTED_TEXT_COLOR,
    OVERLAY_CORNER_RADIUS,
    OVERLAY_GAP,
    OVERLAY_HEIGHT,
    OVERLAY_PADDING_H,
    OVERLAY_WIDTH,
    STATUS_SLOT_WIDTH,
    TEXT_COLOR,
)
from ui.overlay_text import (
    format_recording_duration,
    format_recording_interim_text as _format_recording_interim_text,
)
from utils.log_tail import read_file_tail_text
from utils.log_tail import get_file_signature

logger = logging.getLogger("pulsescribe.overlay")

TK_AVAILABLE = tk is not None

# =============================================================================
# Window-Konstanten
# =============================================================================

WINDOW_WIDTH = OVERLAY_WIDTH  # Fest: passt für die längste Meldung inkl. Badge
WINDOW_HEIGHT = OVERLAY_HEIGHT
WINDOW_CORNER_RADIUS = OVERLAY_CORNER_RADIUS
WINDOW_MARGIN_BOTTOM = 60  # Abstand vom unteren Bildschirmrand

# =============================================================================
# Animation-Konstanten
# =============================================================================

FRAME_MS = 1000 // FPS  # ~16ms, nur während RECORDING
BAR_HEIGHT_UPDATE_EPSILON = 0.25  # Spare Canvas-Updates für subpixel-kleine Änderungen
QUEUE_POLL_ACTIVE_MS = 16  # 60Hz während Overlay sichtbar/aktiv
QUEUE_POLL_ACTIVE_IDLE_MS = 33  # Weniger Wakeups wenn die UI aktiv, die Queue aber leer ist
QUEUE_POLL_IDLE_MS = 20  # Snappier Hotkey→Overlay im Tk-Fallback bei moderater Idle-CPU
QUEUE_MAX_MESSAGES_PER_TICK = 200
INTERIM_QUEUE_BACKPRESSURE_LIMIT = 120
INTERIM_POLL_MAX_CHARS = 512
INTERIM_POLL_INTERVAL_MS = 200
INTERIM_POLL_STABLE_INTERVAL_MS = 500
INTERIM_POLL_STABLE_THRESHOLD = 3
INTERIM_POLL_DIRECT_INTERVAL_MS = 1000
INTERIM_DIRECT_UPDATE_GRACE_S = 1.5
CLOCK_INTERVAL_MS = 1000  # Aufnahmedauer m:ss, nur LISTENING/RECORDING

# =============================================================================
# Farben / Schrift (Werte aus ui/overlay_style.py)
# =============================================================================

BG_COLOR = BACKGROUND_COLOR
TEXT_FONT = ("Segoe UI", 10)
BADGE_FONT = ("Segoe UI", 9, "bold")
META_FONT = ("Segoe UI", 9)  # Segoe UI digits are tabular by default
TEXT_X = OVERLAY_PADDING_H + STATUS_SLOT_WIDTH + OVERLAY_GAP

# =============================================================================
# Overlay Controller
# =============================================================================


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
        self._bar_x_positions = [
            OVERLAY_PADDING_H + i * (BAR_WIDTH + BAR_GAP) for i in range(BAR_COUNT)
        ]
        self._bar_center_y = WINDOW_HEIGHT / 2
        self._badge_label: tk.Label | None = None
        self._dismiss_label: tk.Label | None = None
        self._meta_label: tk.Label | None = None
        self._meta_text = ""
        self._clock_start = 0.0
        self._clock_after_id: str | None = None
        self._text_x = float(TEXT_X)
        self._text_width = 0.0
        self._dismiss_shown = False
        self._persistent = False
        self._bar_mode = "none"
        self._line_item_id: int | None = None
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
        if not TK_AVAILABLE:
            logger.warning("Tkinter overlay unavailable: %s", _TK_IMPORT_ERROR)
            return

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
        self._root.attributes("-alpha", 1.0)
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

        # Badge (Ergebnis) und Text, links neben/nach den Balken platziert
        self._badge_label = tk.Label(
            self._root, text="", font=BADGE_FONT, padx=BADGE_PADDING_H, pady=1
        )
        self._label = tk.Label(
            self._root,
            text="",
            fg=MUTED_TEXT_COLOR,
            bg=BG_COLOR,
            font=TEXT_FONT,
            anchor="w",
        )
        self._label.place(x=TEXT_X, rely=0.5, anchor="w")
        self._dismiss_label = tk.Label(
            self._root, text=DISMISS_GLYPH, fg=MUTED_TEXT_COLOR, bg=BG_COLOR, font=TEXT_FONT
        )
        self._meta_label = tk.Label(
            self._root, text="", fg=META_TEXT_COLOR, bg=BG_COLOR, font=META_FONT
        )
        # Toplevel-Bindings gelten auch für alle Kind-Widgets (bindtags).
        self._root.bind("<Button-1>", lambda _event: self._dismiss())

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
        if not self._can_poll_interim_file():
            return
        if self._state != "RECORDING":
            self._last_interim_text = ""
            self._last_interim_signature = None
            self._direct_interim_until = 0.0
            return

        if self._direct_interim_update_active():
            self._schedule_interim_poll(INTERIM_POLL_DIRECT_INTERVAL_MS)
            return

        self._poll_recording_interim_file()
        self._schedule_interim_poll()

    def _can_poll_interim_file(self) -> bool:
        return bool(
            self._running
            and self._root
            and self._interim_file
            and self._interim_polling_active
        )

    def _direct_interim_update_active(self) -> bool:
        return time.monotonic() < getattr(self, "_direct_interim_until", 0.0)

    def _schedule_interim_poll(self, interval_ms: int | None = None) -> None:
        if not self._running or not self._interim_polling_active or not self._root:
            return
        poll_interval_ms = interval_ms or self._current_interim_poll_interval_ms()
        self._interim_poll_after_id = self._root.after(
            poll_interval_ms,
            self._poll_interim_file,
        )

    def _poll_recording_interim_file(self) -> None:
        signature = get_file_signature(self._interim_file)
        if signature is None:
            self._handle_missing_interim_file()
            return
        if signature == self._last_interim_signature:
            self._stable_interim_polls += 1
            return
        self._read_changed_interim_file(signature)

    def _handle_missing_interim_file(self) -> None:
        if not self._last_interim_text:
            self._stable_interim_polls += 1
            return
        self._last_interim_text = ""
        self._last_interim_signature = None
        self._stable_interim_polls = 0
        self._handle_interim_text("")

    def _read_changed_interim_file(self, signature: tuple[int, int]) -> None:
        try:
            text = read_file_tail_text(
                self._interim_file,
                max_chars=INTERIM_POLL_MAX_CHARS,
                errors="replace",
            ).strip()
        except Exception:
            return

        self._last_interim_signature = signature
        if text == self._last_interim_text:
            self._stable_interim_polls += 1
            return

        self._last_interim_text = text
        self._stable_interim_polls = 0
        self._handle_interim_text(text)

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

        if state_changed:
            if state in RECORDING_CLOCK_STATES:
                self._start_clock()
            else:
                self._stop_clock()

        if state == "IDLE":
            # Daemon returns to IDLE so hotkeys work; a persistent message stays.
            if state_changed and not self._persistent:
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
            content = describe_overlay_content(state, normalized_text)
            # A new error after a dismissed one must show even within ERROR.
            reshow = content.dismissible and not self._persistent
            self._persistent = content.dismissible
            if state_changed:
                self._show_bar_mode(content.bars)
                self._start_animation_loop()
            if state_changed or reshow:
                self._root.deiconify()
            self._apply_content(content)
            if state != "RECORDING":
                self._direct_interim_until = 0.0

        if state_changed:
            self._set_interim_polling_active(state == "RECORDING")

        if state_changed:
            self._animation_start = time.perf_counter()

    def _handle_interim_text(self, text: str) -> None:
        if self._state != "RECORDING" or not getattr(self, "_label", None):
            return

        formatted = _format_recording_interim_text(text)
        if not formatted:
            self._apply_content(describe_overlay_content("RECORDING"))
            return
        self._set_label_config(
            text=formatted,
            font=TEXT_FONT,
            fg=INTERIM_TEXT_COLOR,
        )
        # Tk cannot fade: right-anchor overflowing text so the newest words stay.
        overflow = self._label.winfo_reqwidth() > self._text_width
        self._set_label_anchor("e" if overflow else "w")

    def _set_label_anchor(self, anchor: str) -> None:
        if getattr(self, "_label_anchor", "w") != anchor:
            self._label_anchor = anchor
            self._label.config(anchor=anchor)

    def _apply_content(self, content: OverlayContent) -> None:
        """Place the badge in the status slot and the text after it."""
        text_x = TEXT_X
        badge = getattr(self, "_badge_label", None)
        if badge is not None:
            if content.badge:
                background, foreground = BADGE_TONES[content.tone]
                badge.config(text=content.badge, bg=background, fg=foreground)
                badge.place(x=OVERLAY_PADDING_H, rely=0.5, anchor="w")
                # A badge wider than the slot pushes the text instead of overlapping.
                text_x = max(
                    TEXT_X, OVERLAY_PADDING_H + badge.winfo_reqwidth() + OVERLAY_GAP
                )
            else:
                badge.place_forget()
        self._text_x = text_x
        self._dismiss_shown = content.dismissible
        self._place_trailing()
        if getattr(self, "_label", None) is not None:
            self._set_label_anchor("w")
        self._set_label_config(
            text=content.text,
            font=TEXT_FONT,
            fg=TEXT_COLOR if content.emphasized else MUTED_TEXT_COLOR,
        )

    def _place_trailing(self) -> None:
        """Right-align "×" and meta, then give the text label the rest."""
        text_end = WINDOW_WIDTH - OVERLAY_PADDING_H
        dismiss = getattr(self, "_dismiss_label", None)
        if dismiss is not None:
            if self._dismiss_shown:
                dismiss.place(x=text_end, rely=0.5, anchor="e")
                text_end -= dismiss.winfo_reqwidth() + OVERLAY_GAP
            else:
                dismiss.place_forget()
        meta = getattr(self, "_meta_label", None)
        if meta is not None:
            if self._meta_text:
                meta.config(text=self._meta_text)
                meta.place(x=text_end, rely=0.5, anchor="e")
                text_end -= meta.winfo_reqwidth() + OVERLAY_GAP
            else:
                meta.place_forget()
        self._text_width = max(text_end - self._text_x, 0)
        if getattr(self, "_label", None) is not None and hasattr(self._label, "place"):
            # Fixed width clips long text instead of running under meta or "×".
            self._label.place(x=self._text_x, rely=0.5, anchor="w", width=self._text_width)

    def _start_clock(self) -> None:
        """Show ``0:00`` and tick once per second until ``_stop_clock``."""
        if getattr(self, "_clock_after_id", None) is not None or not self._root:
            return
        self._clock_start = time.monotonic()
        self._meta_text = format_recording_duration(0)
        self._clock_after_id = self._root.after(CLOCK_INTERVAL_MS, self._tick_clock)

    def _tick_clock(self) -> None:
        self._clock_after_id = None
        if not self._running or not self._root:
            return
        elapsed = time.monotonic() - self._clock_start
        shown = round(elapsed)
        self._meta_text = format_recording_duration(shown)
        self._place_trailing()
        # after() drifts; aim at the next whole second instead of +1000 ms.
        delay_ms = max(round((shown + 1 - elapsed) * 1000), 50)
        self._clock_after_id = self._root.after(delay_ms, self._tick_clock)

    def _stop_clock(self) -> None:
        self._meta_text = ""
        after_id = getattr(self, "_clock_after_id", None)
        self._clock_after_id = None
        if after_id is not None and self._root:
            try:
                self._root.after_cancel(after_id)
            except Exception:
                pass

    def _dismiss(self) -> None:
        """Hide a persistent message after a click on the overlay."""
        if not self._persistent or not self._root:
            return
        self._persistent = False
        self._root.withdraw()

    def _set_label_config(
        self, *, text: str, font: tuple[object, ...], fg: str
    ) -> None:
        if not getattr(self, "_label", None):
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

        if self._state != "RECORDING":
            self._animation_running = False
            return

        # Zeit seit Animation-Start
        t = time.perf_counter() - self._animation_start

        # Audio-Level an Animation-Logik übergeben
        self._anim.update_level(self._audio_level)

        # AGC: Einmal pro Frame berechnen (nicht pro Bar)
        self._anim.update_agc()

        self._render_bars(t)
        self._root.after(FRAME_MS, self._animate)

    def _show_bar_mode(self, mode: str) -> None:
        """Render static bar modes once; ``level`` is drawn by the loop."""
        self._bar_mode = mode
        canvas = self._canvas
        if not canvas:
            return
        bar_x_positions, center_y = self._bar_positions()
        self._ensure_pill_bar_items(bar_x_positions, center_y)
        if getattr(self, "_line_item_id", None) is None:
            x = bar_x_positions[0]
            self._line_item_id = canvas.create_rectangle(
                x,
                center_y - LINE_HEIGHT / 2,
                x + BAR_AREA_WIDTH,
                center_y + LINE_HEIGHT / 2,
                fill=BAR_COLORS["line"],
                outline="",
            )
        canvas.itemconfig(self._line_item_id, state="normal" if mode == "line" else "hidden")
        canvas.itemconfig("bars", state="normal" if mode in ("level", "low") else "hidden")
        if mode not in ("level", "low"):
            return
        self._set_bar_color(BAR_COLORS[mode])
        heights = LISTENING_BAR_HEIGHTS if mode == "low" else (BAR_MIN_HEIGHT,) * BAR_COUNT
        self._bar_heights = [float(height) for height in heights]
        for i, height in enumerate(heights):
            self._draw_pill_bar(i, bar_x_positions[i], center_y, BAR_WIDTH, height)

    def _bar_positions(self) -> tuple[list[float], float]:
        positions = getattr(self, "_bar_x_positions", None)
        if positions is None or len(positions) != BAR_COUNT:
            positions = [
                OVERLAY_PADDING_H + i * (BAR_WIDTH + BAR_GAP) for i in range(BAR_COUNT)
            ]
            self._bar_x_positions = positions
        center_y = getattr(self, "_bar_center_y", WINDOW_HEIGHT / 2)
        return positions, center_y

    def _target_bar_heights(self, t: float) -> tuple[float, ...]:
        calculate_frame_heights = getattr(self._anim, "calculate_frame_heights", None)
        if callable(calculate_frame_heights):
            return tuple(calculate_frame_heights(t, self._state))
        return tuple(
            self._anim.calculate_bar_height(i, t, self._state) for i in range(BAR_COUNT)
        )

    def _render_bars(self, t: float) -> None:
        if not self._canvas:
            return

        bar_x_positions, center_y = self._bar_positions()
        self._ensure_pill_bar_items(bar_x_positions, center_y)
        self._set_bar_color(BAR_COLORS["level"])

        for i, target in enumerate(self._target_bar_heights(t)):
            # Smoothing pro Bar
            if target > self._bar_heights[i]:
                alpha = 0.4
            else:
                alpha = 0.15
            self._bar_heights[i] += alpha * (target - self._bar_heights[i])
            height = max(BAR_MIN_HEIGHT, self._bar_heights[i])

            # Pill-förmige Bar zeichnen
            self._draw_pill_bar(i, bar_x_positions[i], center_y, BAR_WIDTH, height)

    def _ensure_pill_bar_items(self, bar_x_positions: list[float], center_y: float) -> None:
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
        for x in bar_x_positions:
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
