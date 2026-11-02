from ui.animation import LISTENING_BAR_HEIGHTS, BAR_MAX_HEIGHT
from ui.overlay_style import (
    BAR_AREA_WIDTH,
    OVERLAY_GAP,
    OVERLAY_PADDING_H,
    OVERLAY_WIDTH,
    PHASE_STEP_GAP,
    PHASE_STEP_WIDTH,
    STATUS_SLOT_WIDTH,
    compute_overlay_layout,
    hex_to_rgb,
    shimmer_track,
)

TEXT_X = OVERLAY_PADDING_H + STATUS_SLOT_WIDTH + OVERLAY_GAP


def test_text_zone_starts_after_the_status_slot_at_fixed_width() -> None:
    short = compute_overlay_layout(text_width=60)
    long = compute_overlay_layout(text_width=2000, max_width=1500)

    for layout in (short, long):
        assert layout.width == OVERLAY_WIDTH
        assert layout.status_x == OVERLAY_PADDING_H
        assert layout.text_x == TEXT_X
        assert layout.text_width == OVERLAY_WIDTH - OVERLAY_PADDING_H - TEXT_X
    assert BAR_AREA_WIDTH <= STATUS_SLOT_WIDTH


def test_meta_and_dismiss_are_right_aligned_and_shrink_the_text_zone() -> None:
    layout = compute_overlay_layout(text_width=50, meta_width=30, dismiss_width=8)

    right = OVERLAY_WIDTH - OVERLAY_PADDING_H
    assert layout.dismiss_x == right - 8
    assert layout.meta_x == layout.dismiss_x - OVERLAY_GAP - 30
    assert layout.text_x + layout.text_width == layout.meta_x - OVERLAY_GAP


def test_only_persistent_content_grows_capped_and_never_below_fixed_width() -> None:
    fitting = compute_overlay_layout(text_width=50, grow=True, max_width=1000)
    capped = compute_overlay_layout(
        text_width=2000, dismiss_width=8, grow=True, max_width=600
    )
    natural = compute_overlay_layout(text_width=500, grow=True, max_width=1000)

    assert fitting.width == OVERLAY_WIDTH
    assert capped.width == 600
    assert capped.dismiss_x == 600 - OVERLAY_PADDING_H - 8
    assert natural.width == TEXT_X + 500 + OVERLAY_PADDING_H
    assert natural.text_width == 500


def test_badge_wider_than_the_slot_pushes_the_text() -> None:
    layout = compute_overlay_layout(text_width=50, status_width=STATUS_SLOT_WIDTH + 6)

    assert layout.text_x == TEXT_X + 6


def test_static_listening_bars_stay_low() -> None:
    assert max(LISTENING_BAR_HEIGHTS) < BAR_MAX_HEIGHT / 2
    assert hex_to_rgb("#1b1b1b") == (27, 27, 27)


def test_shimmer_track_covers_the_line_or_the_active_step() -> None:
    assert shimmer_track("line") == (0.0, BAR_AREA_WIDTH)
    assert shimmer_track("phase1") == (0, PHASE_STEP_WIDTH)
    assert shimmer_track("phase2") == (PHASE_STEP_WIDTH + PHASE_STEP_GAP, PHASE_STEP_WIDTH)
    for mode in ("level", "low", "none"):
        assert shimmer_track(mode) is None
