import math

from ui.animation import AnimationLogic


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
