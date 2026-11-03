"""Shared look of the recording overlay (macOS and Windows).

Colors are hex strings so every toolkit can convert them; the layout helper is
pure so both platforms place the three zones (status · text · meta) and the
dismiss button identically.
"""

from __future__ import annotations

from dataclasses import dataclass

from ui.animation import BAR_COUNT, BAR_GAP, BAR_WIDTH

OVERLAY_HEIGHT = 44
OVERLAY_CORNER_RADIUS = 3
OVERLAY_PADDING_H = 16
OVERLAY_GAP = 12
OVERLAY_WIDTH = 400  # fixed from LISTENING through DONE
OVERLAY_MAX_WIDTH_RATIO = 0.75  # of the screen width, for persistent messages
STATUS_SLOT_WIDTH = 84  # bars, phase line or badge, left-aligned
TEXT_FADE_WIDTH = 30  # static fade where live text is clipped on the left

BACKGROUND_COLOR = "#1b1b1b"
BORDER_COLOR = "#333333"
TEXT_COLOR = "#ececec"
MUTED_TEXT_COLOR = "#949494"
INTERIM_TEXT_COLOR = "#9a9a9a"

TEXT_FONT_SIZE = 13
BADGE_FONT_SIZE = 12.5
META_FONT_SIZE = 12.5  # tabular figures where the toolkit supports them
META_TEXT_COLOR = MUTED_TEXT_COLOR
LINK_TEXT_COLOR = "#ffffff"  # meta link (e.g. "Settings"), drawn underlined
LINK_HIT_SLOP = 4  # extra px around the link that still count as a link click
BADGE_CORNER_RADIUS = 3
BADGE_PADDING_H = 8
BADGE_PADDING_V = 3
DISMISS_GLYPH = "×"
DISMISS_FONT_SIZE = 15

# Bars: "level" follows the microphone, "low" is the listening row (a small
# level meter), "line" collapses the bars into one flat processing line.
BAR_COLORS = {
    "level": "#ececec",
    "low": "#6f6f6f",
    "line": "#6f6f6f",
}
BAR_AREA_WIDTH = BAR_COUNT * BAR_WIDTH + (BAR_COUNT - 1) * BAR_GAP
LINE_HEIGHT = 3

# Two-step line while Refine follows transcription: (step 1, step 2) colors.
PHASE_STEP_COLORS = {
    "phase1": ("#ececec", "#333333"),  # transcribing active, refining pending
    "phase2": ("#6f6f6f", "#ececec"),  # transcribing done, refining active
}
PHASE_STEP_GAP = 3
PHASE_STEP_WIDTH = (BAR_AREA_WIDTH - PHASE_STEP_GAP) / 2

# Shimmer while processing: a soft highlight band sweeps left to right across
# the line (or the active phase step), ease-in-out, repeating. Platforms that
# animate draw the active step in SHIMMER_ACTIVE_STEP_COLOR; with reduced
# motion (and on the Tk fallback) the static colors above stay.
SHIMMER_BAND_WIDTH = 22
SHIMMER_PERIOD_SECONDS = 1.4
SHIMMER_HIGHLIGHT_COLOR = "#f4f4f4"
SHIMMER_ACTIVE_STEP_COLOR = "#8a8a8a"
SHIMMER_MAX_FPS = 30  # repaint cap where Python draws the frames (Qt)
PHASE_ACTIVE_STEP = {"phase1": 0, "phase2": 1}

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


def shimmer_track(mode: str) -> tuple[float, float] | None:
    """``(x, width)`` of the shimmering part of the status slot, else ``None``.

    ``x`` is relative to the slot start. The band travels from
    ``-SHIMMER_BAND_WIDTH`` (fully left of the track) to ``width`` (fully
    right of it), clipped to the track.
    """
    if mode == "line":
        return 0.0, float(BAR_AREA_WIDTH)
    step = PHASE_ACTIVE_STEP.get(mode)
    if step is None:
        return None
    return step * (PHASE_STEP_WIDTH + PHASE_STEP_GAP), PHASE_STEP_WIDTH


@dataclass(frozen=True)
class OverlayLayout:
    """Horizontal zones of the overlay; absent parts are ``None``.

    ``status_x`` starts the bars/badge slot, ``text_x``/``text_width`` is the
    whole text zone (callers clip or elide inside it), ``meta_x`` starts the
    right-aligned meta text and ``dismiss_x`` the "×".
    """

    width: float
    status_x: float
    text_x: float
    text_width: float
    meta_x: float | None = None
    dismiss_x: float | None = None


def compute_overlay_layout(
    *,
    text_width: float,
    meta_width: float = 0,
    dismiss_width: float = 0,
    status_width: float = 0,
    grow: bool = False,
    max_width: float = OVERLAY_WIDTH,
) -> OverlayLayout:
    """Lay out ``[status] [text …] [meta] [×]`` at a fixed width.

    The text zone always starts after the ``STATUS_SLOT_WIDTH`` slot, so text
    does not move between states. Only ``grow`` content (persistent messages)
    widens the overlay to fit its text, capped at ``max_width`` and never
    below ``OVERLAY_WIDTH``. A badge wider than the slot pushes the text
    instead of overlapping it.
    """
    status_x = float(OVERLAY_PADDING_H)
    text_x = status_x + max(float(STATUS_SLOT_WIDTH), status_width) + OVERLAY_GAP
    trailing = 0.0
    if meta_width > 0:
        trailing += OVERLAY_GAP + meta_width
    if dismiss_width > 0:
        trailing += OVERLAY_GAP + dismiss_width
    width = float(OVERLAY_WIDTH)
    if grow:
        natural = text_x + max(text_width, 0.0) + trailing + OVERLAY_PADDING_H
        width = max(width, min(natural, max_width))
    right = width - OVERLAY_PADDING_H
    dismiss_x = meta_x = None
    if dismiss_width > 0:
        dismiss_x = right - dismiss_width
        right = dismiss_x - OVERLAY_GAP
    if meta_width > 0:
        meta_x = right - meta_width
        right = meta_x - OVERLAY_GAP
    return OverlayLayout(
        width=width,
        status_x=status_x,
        text_x=text_x,
        text_width=max(right - text_x, 0.0),
        meta_x=meta_x,
        dismiss_x=dismiss_x,
    )
