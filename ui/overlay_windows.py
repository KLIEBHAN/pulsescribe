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
import time
import tkinter as tk
from pathlib import Path

from ui.animation import (
    AnimationLogic,
    BAR_COUNT,
    BAR_WIDTH,
    BAR_GAP,
    BAR_MIN_HEIGHT,
    BAR_MAX_HEIGHT,
    FPS,
)

logger = logging.getLogger("pulsescribe.overlay")

# =============================================================================
# Window-Konstanten
# =============================================================================

WINDOW_WIDTH = 280
WINDOW_HEIGHT = 90
WINDOW_CORNER_RADIUS = 16
WINDOW_MARGIN_BOTTOM = 60  # Abstand vom unteren Bildschirmrand

# =============================================================================
# Bar-Konstanten (ähnlich macOS)
# =============================================================================

BAR_CORNER_RADIUS = 2  # Abgerundete Enden

# =============================================================================
# Animation-Konstanten
# =============================================================================

FRAME_MS = 1000 // FPS  # ~16ms

# =============================================================================
# Farben
# =============================================================================

BG_COLOR = "#1A1A1A"  # Etwas dunkler als vorher

STATE_COLORS = {
    "LISTENING": "#FFB6C1",  # Pink
    "RECORDING": "#FF5252",  # Rot
    "TRANSCRIBING": "#FFB142",  # Orange
    "REFINING": "#9C27B0",  # Lila
    "DONE": "#4CAF50",  # Grün (satter)
    "ERROR": "#FF4757",  # Rot
}

STATE_TEXTS = {
    "LISTENING": "Listening...",
    "RECORDING": "Recording...",
    "TRANSCRIBING": "Transcribing...",
    "REFINING": "Refining...",
    "DONE": "Done!",
    "ERROR": "Error",
}

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
        self._bar_heights = [BAR_MIN_HEIGHT] * BAR_COUNT

        # Animation timing
        self._animation_start = time.perf_counter()

        self._queue: queue.Queue = queue.Queue()
        self._running = False
        self._interim_file = interim_file
        self._last_interim_text = ""

    # =========================================================================
    # Public API (thread-safe)
    # =========================================================================

    def update_state(self, state: str, text: str | None = None) -> None:
        self._queue.put(("state", state, text))

    def update_audio_level(self, level: float) -> None:
        self._queue.put(("level", level, None))

    def update_interim_text(self, text: str) -> None:
        self._queue.put(("interim", text, None))

    def run(self) -> None:
        """Start tkinter mainloop (call from dedicated thread)."""
        self._root = tk.Tk()
        self._setup_window()
        self._running = True
        self._animation_start = time.perf_counter()
        self._poll_queue()
        self._animate()
        if self._interim_file:
            self._poll_interim_file()
        self._root.mainloop()

    def stop(self) -> None:
        self._running = False
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

        # Position: bottom-center
        screen_w = self._root.winfo_screenwidth()
        screen_h = self._root.winfo_screenheight()
        x = (screen_w - WINDOW_WIDTH) // 2
        y = screen_h - WINDOW_HEIGHT - WINDOW_MARGIN_BOTTOM
        self._root.geometry(f"{WINDOW_WIDTH}x{WINDOW_HEIGHT}+{x}+{y}")

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
        try:
            while True:
                msg_type, value, text = self._queue.get_nowait()
                if msg_type == "state":
                    self._handle_state_change(value, text)
                elif msg_type == "level":
                    self._audio_level = value
                elif msg_type == "interim":
                    self._handle_interim_text(value)
        except queue.Empty:
            pass

        if self._running and self._root:
            self._root.after(10, self._poll_queue)

    def _poll_interim_file(self) -> None:
        if not self._running or not self._root or not self._interim_file:
            return

        if self._state == "RECORDING" and self._interim_file.exists():
            try:
                text = self._interim_file.read_text(encoding="utf-8").strip()
                if text and text != self._last_interim_text:
                    self._last_interim_text = text
                    self._handle_interim_text(text)
            except Exception:
                pass

        if self._running:
            self._root.after(200, self._poll_interim_file)  # 200ms wie macOS

    # =========================================================================
    # State Handling
    # =========================================================================

    def _handle_state_change(self, state: str, text: str | None) -> None:
        prev_state = self._state
        self._state = state

        if not self._root or not self._label:
            return

        if state == "IDLE":
            self._root.withdraw()
            self._last_interim_text = ""
            self._anim = AnimationLogic()
        else:
            self._root.deiconify()
            display_text = text or STATE_TEXTS.get(state, "")
            self._label.config(
                text=display_text,
                font=("Segoe UI", 11),
                fg="white",
            )

        if state != prev_state:
            self._animation_start = time.perf_counter()

    def _handle_interim_text(self, text: str) -> None:
        if self._state != "RECORDING" or not self._label:
            return

        if len(text) > 45:
            text = "..." + text[-42:]

        self._label.config(
            text=text,
            font=("Segoe UI", 10, "italic"),
            fg="#909090",
        )

    # =========================================================================
    # Animation
    # =========================================================================

    def _animate(self) -> None:
        if not self._running or not self._root:
            return

        if self._state == "IDLE":
            self._root.after(100, self._animate)
            return

        # Zeit seit Animation-Start
        t = time.perf_counter() - self._animation_start

        # Audio-Level an Animation-Logik übergeben
        self._anim.update_level(self._audio_level)

        # AGC: Einmal pro Frame berechnen (nicht pro Bar)
        if self._state == "RECORDING":
            self._anim.update_agc()

        self._render_bars(t)
        self._root.after(FRAME_MS, self._animate)

    def _update_agc(self) -> None:
        """Berechnet AGC-normalisierten Level (einmal pro Frame)."""
        # Noise Gate
        gated = max(self._smoothed_level - VISUAL_NOISE_GATE, 0.0)

        # AGC: Peak-Tracking (schneller Attack, langsamer Release)
        if gated > self._agc_peak:
            self._agc_peak = gated
        else:
            self._agc_peak = max(self._agc_peak * AGC_DECAY, AGC_MIN_PEAK)

        # Normalisieren auf Peak mit Headroom
        reference_peak = max(self._agc_peak * AGC_HEADROOM, AGC_MIN_PEAK)
        normalized = gated / reference_peak if reference_peak > 0 else 0.0

        # Visuelle Kurve + Gain
        shaped = (min(1.0, normalized) ** VISUAL_EXPONENT) * VISUAL_GAIN
        self._normalized_level = min(1.0, shaped)

    def _render_bars(self, t: float) -> None:
        if not self._canvas:
            return

        self._canvas.delete("bars")
        color = STATE_COLORS.get(self._state, "#FFFFFF")

        # Bar-Positionen berechnen
        total_width = BAR_COUNT * BAR_WIDTH + (BAR_COUNT - 1) * BAR_GAP
        start_x = (WINDOW_WIDTH - total_width) // 2
        center_y = 35  # Etwas höher für Text darunter

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
            self._draw_pill_bar(x, center_y, BAR_WIDTH, height, color)

    def _draw_pill_bar(
        self, x: float, center_y: float, width: float, height: float, color: str
    ) -> None:
        """Zeichnet eine Pill-förmige Bar (abgerundete Enden)."""
        if not self._canvas:
            return

        y1 = center_y - height / 2
        y2 = center_y + height / 2
        r = min(width / 2, height / 2, BAR_CORNER_RADIUS)

        # Wenn sehr klein, einfaches Oval
        if height <= width:
            self._canvas.create_oval(
                x, y1, x + width, y2, fill=color, outline="", tags="bars"
            )
        else:
            # Pill: Rechteck mit abgerundeten Enden
            # Oberes Halbrund
            self._canvas.create_arc(
                x,
                y1,
                x + width,
                y1 + width,
                start=0,
                extent=180,
                fill=color,
                outline="",
                tags="bars",
            )
            # Mittleres Rechteck
            if y2 - width / 2 > y1 + width / 2:
                self._canvas.create_rectangle(
                    x,
                    y1 + width / 2,
                    x + width,
                    y2 - width / 2,
                    fill=color,
                    outline="",
                    tags="bars",
                )
            # Unteres Halbrund
            self._canvas.create_arc(
                x,
                y2 - width,
                x + width,
                y2,
                start=180,
                extent=180,
                fill=color,
                outline="",
                tags="bars",
            )


__all__ = ["WindowsOverlayController"]
