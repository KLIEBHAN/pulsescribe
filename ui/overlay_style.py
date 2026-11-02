"""Shared look of the recording overlay (macOS and Windows).

Colors are hex strings so every toolkit can convert them; the layout helper is
pure so both platforms place bars, badge, text and dismiss button identically.
"""

from __future__ import annotations

from dataclasses import dataclass

from ui.animation import BAR_COUNT, BAR_GAP, BAR_WIDTH

OVERLAY_HEIGHT = 44
OVERLAY_CORNER_RADIUS = 3
OVERLAY_PADDING_H = 16
OVERLAY_GAP = 12
OVERLAY_MIN_WIDTH = 260
OVERLAY_MAX_WIDTH_RATIO = 0.75  # of the screen width, for long interim text

BACKGROUND_COLOR = "#1b1b1b"
BORDER_COLOR = "#333333"
TEXT_COLOR = "#ececec"
MUTED_TEXT_COLOR = "#949494"
INTERIM_TEXT_COLOR = "#9a9a9a"

TEXT_FONT_SIZE = 13
BADGE_FONT_SIZE = 12.5
BADGE_CORNER_RADIUS = 3
BADGE_PADDING_H = 8
BADGE_PADDING_V = 3

# Bars: "level" follows the microphone, "low" is the static listening row,
# "line" collapses the bars into one flat processing line.
BAR_COLORS = {
    "level": "#ececec",
    "low": "#5a5a5a",
    "line": "#6f6f6f",
}
BAR_AREA_WIDTH = BAR_COUNT * BAR_WIDTH + (BAR_COUNT - 1) * BAR_GAP
LINE_HEIGHT = 3

# Badge tones: (background, text). Color is reserved for outcomes.
BADGE_TONES = {
    "success": ("#173326", "#6fd39b"),
    "warn": ("#3a2c12", "#f0b955"),
    "error": ("#3a1a1a", "#f08a8a"),
}


def hex_to_rgb(color: str) -> tuple[int, int, int]:
    """Convert ``#rrggbb`` into an RGB tuple for toolkit color constructors."""
    value = color.lstrip("#")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


@dataclass(frozen=True)
class OverlayLayout:
    """Horizontal positions of the overlay parts; absent parts are ``None``."""

    width: float
    bars_x: float | None
    badge_x: float | None
    text_x: float
    text_width: float


def compute_overlay_layout(
    *,
    bars: bool,
    badge_width: float,
    text_width: float,
    max_width: float,
) -> OverlayLayout:
    """Lay out ``[bars] [badge] [text]`` left to right with fixed gaps.

    The overlay grows with its content between ``OVERLAY_MIN_WIDTH`` and
    ``max_width``; text that still does not fit gets the remaining width so
    the caller can elide it.
    """
    x = float(OVERLAY_PADDING_H)
    bars_x = badge_x = None
    if bars:
        bars_x = x
        x += BAR_AREA_WIDTH + OVERLAY_GAP
    if badge_width > 0:
        badge_x = x
        x += badge_width + OVERLAY_GAP
    if text_width <= 0:
        x -= OVERLAY_GAP if (bars or badge_width > 0) else 0
    natural = x + max(text_width, 0.0) + OVERLAY_PADDING_H
    width = max(float(OVERLAY_MIN_WIDTH), min(natural, max(max_width, OVERLAY_MIN_WIDTH)))
    available = max(width - OVERLAY_PADDING_H - x, 0.0)
    return OverlayLayout(
        width=width,
        bars_x=bars_x,
        badge_x=badge_x,
        text_x=x,
        text_width=min(max(text_width, 0.0), available),
    )
