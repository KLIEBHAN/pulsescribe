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
import math
import queue
import time
import tkinter as tk
from pathlib import Path

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

BAR_COUNT = 10
BAR_WIDTH = 4  # Schmaler wie macOS (war 6)
BAR_GAP = 5
BAR_MIN_HEIGHT = 6
BAR_MAX_HEIGHT = 42
BAR_CORNER_RADIUS = 2  # Abgerundete Enden

# =============================================================================
# Animation-Konstanten
# =============================================================================

FPS = 60
FRAME_MS = 1000 // FPS  # ~16ms

# Smoothing: Schneller Anstieg, langsames Abklingen (wie macOS)
SMOOTHING_ALPHA_RISE = 0.55
SMOOTHING_ALPHA_FALL = 0.12

# Audio-Visual Mapping
VISUAL_GAIN = 2.0  # Finale Verstärkung nach AGC
VISUAL_NOISE_GATE = 0.002  # Unter diesem Level = Stille
VISUAL_EXPONENT = 1.3  # Kompression für natürlicheren Look

# Adaptive Gain Control (AGC) - wie macOS
# Hält die Visualisierung bei leisen/lauteren Mics dynamisch
# macOS: 0.97 bei ~15Hz Audio-Updates. Windows: 60Hz Animation.
# Gleiche Zeitkonstante: 0.97^15 ≈ 0.63/s → bei 60Hz: 0.63^(1/60) ≈ 0.9923
AGC_DECAY = 0.9923  # Peak-Falloff pro Frame (~1s Zeitkonstante bei 60Hz)
AGC_MIN_PEAK = 0.01  # Untergrenze gegen Überverstärkung (wie macOS)
AGC_HEADROOM = 2.0  # Mehr Headroom = weniger Sensitivität/Clipping

# Traveling Wave (sanfte Modulation)
WAVE_WANDER_AMOUNT = 0.25
WAVE_WANDER_HZ_PRIMARY = 0.5
WAVE_WANDER_HZ_SECONDARY = 0.85
WAVE_WANDER_PHASE_STEP_PRIMARY = 0.8
WAVE_WANDER_PHASE_STEP_SECONDARY = 1.5
WAVE_WANDER_BLEND = 0.6

# Gaussian Envelope (wanderndes Energie-Paket)
ENVELOPE_STRENGTH = 0.75
ENVELOPE_BASE = 0.4  # Mindest-Faktor
ENVELOPE_SIGMA = 1.3  # Breite des Pakets
ENVELOPE_HZ_PRIMARY = 0.18
ENVELOPE_HZ_SECONDARY = 0.28
ENVELOPE_BLEND = 0.55

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


# Pre-computed
_HEIGHT_FACTORS = _build_height_factors()


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
        self._smoothed_level = 0.0
        self._agc_peak = AGC_MIN_PEAK  # Adaptive Gain Control
        self._normalized_level = 0.0  # AGC-normalisierter Level (einmal pro Frame)
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
            self._smoothed_level = 0.0
            self._agc_peak = AGC_MIN_PEAK  # AGC reset
            self._normalized_level = 0.0
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

        # Audio-Level smoothen (unterschiedliche Rise/Fall)
        target_level = self._audio_level
        if target_level > self._smoothed_level:
            alpha = SMOOTHING_ALPHA_RISE
        else:
            alpha = SMOOTHING_ALPHA_FALL
        self._smoothed_level += alpha * (target_level - self._smoothed_level)

        # AGC: Einmal pro Frame berechnen (nicht pro Bar)
        if self._state == "RECORDING":
            self._update_agc()

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
            target = self._calculate_bar_height(i, t)

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

    def _calculate_bar_height(self, bar_index: int, t: float) -> float:
        """Berechnet Zielhöhe für einen Bar basierend auf State und Zeit."""
        i = bar_index
        center = (BAR_COUNT - 1) / 2

        if self._state == "RECORDING":
            return self._calc_recording_height(i, t, center)
        elif self._state == "LISTENING":
            return self._calc_listening_height(i, t)
        elif self._state in ("TRANSCRIBING", "REFINING"):
            return self._calc_processing_height(i, t)
        elif self._state == "DONE":
            return self._calc_done_height(i, t)
        elif self._state == "ERROR":
            return self._calc_error_height(t)

        return BAR_MIN_HEIGHT

    def _calc_recording_height(self, i: int, t: float, center: float) -> float:
        """Recording: Audio-responsive mit Traveling Wave und Envelope."""
        # AGC-normalisierter Level (wird einmal pro Frame in _update_agc berechnet)
        level = self._normalized_level

        # Traveling Wave Modulation
        phase1 = (
            2 * math.pi * WAVE_WANDER_HZ_PRIMARY * t
            + i * WAVE_WANDER_PHASE_STEP_PRIMARY
        )
        phase2 = (
            2 * math.pi * WAVE_WANDER_HZ_SECONDARY * t
            + i * WAVE_WANDER_PHASE_STEP_SECONDARY
        )
        wave1 = (math.sin(phase1) + 1) / 2
        wave2 = (math.sin(phase2) + 1) / 2
        wave_mod = WAVE_WANDER_BLEND * wave1 + (1 - WAVE_WANDER_BLEND) * wave2
        wave_factor = 1.0 - WAVE_WANDER_AMOUNT + WAVE_WANDER_AMOUNT * wave_mod

        # Gaussian Envelope (wanderndes Energie-Paket)
        env_phase1 = 2 * math.pi * ENVELOPE_HZ_PRIMARY * t
        env_phase2 = 2 * math.pi * ENVELOPE_HZ_SECONDARY * t
        env_offset1 = math.sin(env_phase1) * center * 0.8
        env_offset2 = math.sin(env_phase2) * center * 0.6
        env_center = (
            center + ENVELOPE_BLEND * env_offset1 + (1 - ENVELOPE_BLEND) * env_offset2
        )

        distance = abs(i - env_center)
        env_factor = ENVELOPE_BASE + (1 - ENVELOPE_BASE) * _gaussian(
            distance, ENVELOPE_SIGMA
        )
        env_factor = ENVELOPE_STRENGTH * env_factor + (1 - ENVELOPE_STRENGTH) * 1.0

        # Basis-Höhenfaktor (Mitte höher)
        base_factor = _HEIGHT_FACTORS[i]

        # Kombinieren
        combined = level * base_factor * wave_factor * env_factor
        return BAR_MIN_HEIGHT + (BAR_MAX_HEIGHT - BAR_MIN_HEIGHT) * combined

    def _calc_listening_height(self, i: int, t: float) -> float:
        """Listening: Langsames Atmen."""
        phase = t * 0.4 + i * 0.25
        breath = (math.sin(phase) + 1) / 2
        return BAR_MIN_HEIGHT + 12 * breath * _HEIGHT_FACTORS[i]

    def _calc_processing_height(self, i: int, t: float) -> float:
        """Transcribing/Refining: Sequentieller Pulse."""
        # Pulse wandert von links nach rechts
        pulse_pos = (t * 1.5) % (BAR_COUNT + 2) - 1
        distance = abs(i - pulse_pos)
        intensity = max(0, 1 - distance / 2)
        return BAR_MIN_HEIGHT + (BAR_MAX_HEIGHT * 0.6) * intensity

    def _calc_done_height(self, i: int, t: float) -> float:
        """Done: Bounce-Animation."""
        if t < 0.3:
            # Schneller Anstieg
            progress = t / 0.3
            return (
                BAR_MIN_HEIGHT
                + (BAR_MAX_HEIGHT - BAR_MIN_HEIGHT) * progress * _HEIGHT_FACTORS[i]
            )
        elif t < 0.5:
            # Bounce
            progress = (t - 0.3) / 0.2
            bounce = 1 - abs(math.sin(progress * math.pi * 2)) * 0.3
            return BAR_MAX_HEIGHT * bounce * _HEIGHT_FACTORS[i]
        else:
            return BAR_MAX_HEIGHT * 0.7 * _HEIGHT_FACTORS[i]

    def _calc_error_height(self, t: float) -> float:
        """Error: Flash-Animation."""
        flash = (math.sin(t * 8) + 1) / 2
        return BAR_MIN_HEIGHT + (BAR_MAX_HEIGHT - BAR_MIN_HEIGHT) * flash * 0.5


__all__ = ["WindowsOverlayController"]
