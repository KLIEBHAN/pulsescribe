import math
from unittest.mock import patch

from ui.animation import AnimationLogic, BAR_MAX_HEIGHT, BAR_MIN_HEIGHT


def test_update_level_ignores_non_finite_values() -> None:
    anim = AnimationLogic()

    anim.update_level(float("nan"))
    anim.update_level(float("inf"))
    anim.update_level(-float("inf"))
    anim.update_agc()

    value = anim.calculate_bar_normalized(0, 0.0, "RECORDING")
    assert math.isfinite(value)
    assert 0.0 <= value <= 1.0


def test_update_level_clamps_out_of_range_values() -> None:
    anim = AnimationLogic()

    anim.update_level(5.0)
    anim.update_level(-2.0)

    assert 0.0 <= anim._smoothed_level <= 1.0
    assert 0.0 <= anim._level_smoothed <= 1.0


def test_update_level_accepts_non_numeric_input_without_crashing() -> None:
    anim = AnimationLogic()

    anim.update_level(None)  # type: ignore[arg-type]
    anim.update_agc()

    value = anim.calculate_bar_height(0, 0.0, "RECORDING")
    assert math.isfinite(value)


def test_calculate_bar_normalized_reuses_frame_cache_for_same_state_and_time() -> None:
    anim = AnimationLogic()
    anim.update_level(0.6)
    anim.update_agc()

    with patch.object(
        anim,
        "_build_frame_values",
        wraps=anim._build_frame_values,
    ) as build_frame_values:
        first_pass = [
            anim.calculate_bar_normalized(i, 0.25, "RECORDING") for i in range(10)
        ]
        second_pass = [
            anim.calculate_bar_normalized(i, 0.25, "RECORDING") for i in range(10)
        ]

    assert build_frame_values.call_count == 1
    assert second_pass == first_pass


def test_calculate_frame_heights_reuses_frame_cache_and_matches_per_bar() -> None:
    anim = AnimationLogic()
    anim.update_level(0.6)
    anim.update_agc()

    with patch.object(
        anim,
        "_build_frame_values",
        wraps=anim._build_frame_values,
    ) as build_frame_values:
        frame_heights = anim.calculate_frame_heights(0.25, "RECORDING")
        per_bar = tuple(
            anim.calculate_bar_height(i, 0.25, "RECORDING") for i in range(10)
        )
        custom_heights = anim.calculate_frame_heights(
            0.25,
            "RECORDING",
            min_height=BAR_MIN_HEIGHT + 2,
            max_height=BAR_MAX_HEIGHT + 4,
        )

    assert build_frame_values.call_count == 1
    assert frame_heights == per_bar
    assert len(custom_heights) == 10
    assert min(custom_heights) >= BAR_MIN_HEIGHT + 2
    assert max(custom_heights) <= BAR_MAX_HEIGHT + 4
