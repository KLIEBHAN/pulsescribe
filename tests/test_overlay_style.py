from ui.animation import LISTENING_BAR_HEIGHTS, BAR_MAX_HEIGHT
from ui.overlay_style import (
    BAR_AREA_WIDTH,
    OVERLAY_GAP,
    OVERLAY_MIN_WIDTH,
    OVERLAY_PADDING_H,
    compute_overlay_layout,
    hex_to_rgb,
)


def test_short_content_keeps_min_width_and_left_aligns() -> None:
    layout = compute_overlay_layout(
        bars=True, badge_width=0, text_width=60, max_width=1000
    )
    assert layout.width == OVERLAY_MIN_WIDTH
    assert layout.bars_x == OVERLAY_PADDING_H
    assert layout.text_x == OVERLAY_PADDING_H + BAR_AREA_WIDTH + OVERLAY_GAP
    assert layout.text_width == 60


def test_badge_layout_without_bars() -> None:
    layout = compute_overlay_layout(
        bars=False, badge_width=70, text_width=200, max_width=1000
    )
    assert layout.bars_x is None
    assert layout.badge_x == OVERLAY_PADDING_H
    assert layout.text_x == OVERLAY_PADDING_H + 70 + OVERLAY_GAP
    assert layout.width == layout.text_x + 200 + OVERLAY_PADDING_H


def test_long_text_is_capped_and_gets_remaining_width() -> None:
    layout = compute_overlay_layout(
        bars=True, badge_width=0, text_width=2000, max_width=500
    )
    assert layout.width == 500
    assert layout.text_x + layout.text_width == 500 - OVERLAY_PADDING_H


def test_dismiss_glyph_sits_at_right_edge_and_reserves_space() -> None:
    layout = compute_overlay_layout(
        bars=False, badge_width=50, text_width=2000, max_width=600, dismiss_width=8
    )
    assert layout.dismiss_x == 600 - OVERLAY_PADDING_H - 8
    assert layout.text_x + layout.text_width == layout.dismiss_x - OVERLAY_GAP


def test_static_listening_bars_stay_low() -> None:
    assert max(LISTENING_BAR_HEIGHTS) < BAR_MAX_HEIGHT / 2
    assert hex_to_rgb("#1b1b1b") == (27, 27, 27)
