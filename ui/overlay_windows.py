"""Windows Overlay für PulseScribe.

Tkinter-basiertes Overlay mit animierten Waveform-Bars.
Zeigt Status und Interim-Text während der Aufnahme.
"""

import logging
import math
import queue
import threading
import tkinter as tk
from pathlib import Path

logger = logging.getLogger("pulsescribe.overlay")

# =============================================================================
# Konstanten
# =============================================================================

WINDOW_WIDTH = 300
WINDOW_HEIGHT = 100
BAR_COUNT = 10
BAR_WIDTH = 6
BAR_GAP = 4
BAR_MIN_HEIGHT = 8
BAR_MAX_HEIGHT = 48
FPS = 60
FRAME_MS = 1000 // FPS  # ~16ms

# Farben pro State (wie macOS)
STATE_COLORS = {
    "LISTENING": "#FFB6C1",     # Pink
    "RECORDING": "#FF5252",     # Rot
    "TRANSCRIBING": "#FFB142",  # Orange
    "REFINING": "#9C27B0",      # Lila
    "DONE": "#33D9B2",          # Grün
    "ERROR": "#FF4757",         # Rot (heller)
}

# State-Texte
STATE_TEXTS = {
    "LISTENING": "Listening...",
    "RECORDING": "Recording...",
    "TRANSCRIBING": "Transcribing...",
    "REFINING": "Refining...",
    "DONE": "Done!",
    "ERROR": "Error",
}


class WindowsOverlayController:
    """Tkinter-basiertes Overlay für Windows.

    Läuft in separatem Thread. Thread-safe via Queue.

    Usage:
        overlay = WindowsOverlayController()
        threading.Thread(target=overlay.run, daemon=True).start()

        # Thread-safe updates:
        overlay.update_state("RECORDING")
        overlay.update_audio_level(0.5)
        overlay.update_interim_text("Hello world...")
    """

    def __init__(self, interim_file: Path | None = None):
        """Initialisiert Overlay.

        Args:
            interim_file: Optional path to interim file for polling.
                         If provided, polls file for interim text.
        """
        self._root: tk.Tk | None = None
        self._canvas: tk.Canvas | None = None
        self._label: tk.Label | None = None

        self._state = "IDLE"
        self._audio_level = 0.0
        self._bar_heights = [BAR_MIN_HEIGHT] * BAR_COUNT
        self._animation_phase = 0.0

        self._queue: queue.Queue = queue.Queue()
        self._running = False
        self._interim_file = interim_file
        self._last_interim_text = ""

    # =========================================================================
    # Public API (thread-safe)
    # =========================================================================

    def update_state(self, state: str, text: str | None = None) -> None:
        """Thread-safe state update.

        Args:
            state: State name (IDLE, LISTENING, RECORDING, etc.)
            text: Optional text to display (overrides default state text)
        """
        self._queue.put(("state", state, text))

    def update_audio_level(self, level: float) -> None:
        """Thread-safe audio level update.

        Args:
            level: Audio level (0.0 - 1.0)
        """
        self._queue.put(("level", level, None))

    def update_interim_text(self, text: str) -> None:
        """Thread-safe interim text update.

        Args:
            text: Interim transcription text
        """
        self._queue.put(("interim", text, None))

    def run(self) -> None:
        """Start tkinter mainloop (call from dedicated thread)."""
        self._root = tk.Tk()
        self._setup_window()
        self._running = True
        self._poll_queue()
        self._animate()
        if self._interim_file:
            self._poll_interim_file()
        self._root.mainloop()

    def stop(self) -> None:
        """Stop overlay."""
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
        """Configure borderless overlay window."""
        if not self._root:
            return

        # Window title (hidden, but useful for debugging)
        self._root.title("PulseScribe")

        # Borderless, topmost, semi-transparent
        self._root.overrideredirect(True)
        self._root.attributes("-topmost", True)
        self._root.attributes("-alpha", 0.95)
        self._root.configure(bg="#1E1E1E")

        # Position: bottom-center, 60px above taskbar
        screen_w = self._root.winfo_screenwidth()
        screen_h = self._root.winfo_screenheight()
        x = (screen_w - WINDOW_WIDTH) // 2
        y = screen_h - WINDOW_HEIGHT - 60
        self._root.geometry(f"{WINDOW_WIDTH}x{WINDOW_HEIGHT}+{x}+{y}")

        # Canvas für Bars
        self._canvas = tk.Canvas(
            self._root,
            width=WINDOW_WIDTH,
            height=60,
            bg="#1E1E1E",
            highlightthickness=0,
        )
        self._canvas.pack(pady=(10, 0))

        # Label für Text
        self._label = tk.Label(
            self._root,
            text="",
            fg="white",
            bg="#1E1E1E",
            font=("Segoe UI", 11),
        )
        self._label.pack(pady=(5, 10))

        # Initial hidden
        self._root.withdraw()

        logger.debug("Overlay window initialized")

    # =========================================================================
    # Queue Processing
    # =========================================================================

    def _poll_queue(self) -> None:
        """Process queued updates from other threads."""
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
        """Poll interim file for text updates."""
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
            self._root.after(300, self._poll_interim_file)

    # =========================================================================
    # State Handling
    # =========================================================================

    def _handle_state_change(self, state: str, text: str | None) -> None:
        """Handle state change."""
        prev_state = self._state
        self._state = state

        if not self._root or not self._label:
            return

        if state == "IDLE":
            self._root.withdraw()
            self._last_interim_text = ""
            logger.debug("Overlay hidden")
        else:
            self._root.deiconify()
            display_text = text or STATE_TEXTS.get(state, "")
            self._label.config(
                text=display_text,
                font=("Segoe UI", 11),
                fg="white",
            )
            logger.debug(f"Overlay state: {state} -> '{display_text}'")

        # Reset animation on state change
        if state != prev_state:
            self._animation_phase = 0.0

    def _handle_interim_text(self, text: str) -> None:
        """Display interim text in ghost style."""
        if self._state != "RECORDING" or not self._label:
            return

        # Truncate to max 40 chars
        if len(text) > 40:
            text = "..." + text[-37:]

        self._label.config(
            text=text,
            font=("Segoe UI", 11, "italic"),
            fg="#808080",  # 50% gray (ghost look)
        )

    # =========================================================================
    # Animation
    # =========================================================================

    def _animate(self) -> None:
        """Animation loop at ~60 FPS."""
        if not self._running or not self._root:
            return

        if self._state == "IDLE":
            # Slow poll when idle
            self._root.after(100, self._animate)
            return

        self._animation_phase += 0.1
        self._render_bars()
        self._root.after(FRAME_MS, self._animate)

    def _render_bars(self) -> None:
        """Render waveform bars on canvas."""
        if not self._canvas:
            return

        self._canvas.delete("bars")
        color = STATE_COLORS.get(self._state, "#FFFFFF")

        # Calculate bar positions
        total_width = BAR_COUNT * BAR_WIDTH + (BAR_COUNT - 1) * BAR_GAP
        start_x = (WINDOW_WIDTH - total_width) // 2
        center_y = 30

        for i in range(BAR_COUNT):
            target = self._calculate_bar_height(i)

            # Smooth transition
            alpha = 0.3
            self._bar_heights[i] += alpha * (target - self._bar_heights[i])
            height = self._bar_heights[i]

            # Draw bar (centered vertically)
            x = start_x + i * (BAR_WIDTH + BAR_GAP)
            y1 = center_y - height / 2
            y2 = center_y + height / 2
            self._canvas.create_rectangle(
                x, y1, x + BAR_WIDTH, y2,
                fill=color, outline="", tags="bars"
            )

    def _calculate_bar_height(self, bar_index: int) -> float:
        """Calculate target height for a bar based on state."""
        i = bar_index
        phase = self._animation_phase

        if self._state == "RECORDING":
            # Audio-responsive with phase offset
            bar_phase = phase + i * 0.3
            return BAR_MIN_HEIGHT + (BAR_MAX_HEIGHT - BAR_MIN_HEIGHT) * (
                self._audio_level * (0.5 + 0.5 * math.sin(bar_phase))
            )

        elif self._state == "LISTENING":
            # Slow breathing animation
            return BAR_MIN_HEIGHT + 10 * (
                0.5 + 0.5 * math.sin(phase * 0.3 + i * 0.2)
            )

        elif self._state in ("TRANSCRIBING", "REFINING"):
            # Sequential pulse
            bar_phase = (phase - i * 0.15) % (2 * math.pi)
            return BAR_MIN_HEIGHT + 20 * max(0, math.sin(bar_phase))

        elif self._state == "DONE":
            # High static bars
            return BAR_MAX_HEIGHT * 0.7

        elif self._state == "ERROR":
            # Flash effect
            flash = (math.sin(phase * 3) + 1) / 2
            return BAR_MIN_HEIGHT + (BAR_MAX_HEIGHT - BAR_MIN_HEIGHT) * flash * 0.5

        return BAR_MIN_HEIGHT


__all__ = ["WindowsOverlayController"]
