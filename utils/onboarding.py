"""Onboarding state machine helpers.

The onboarding flow is implemented as a small state machine that can be
persisted in user preferences.
"""

from __future__ import annotations

from enum import Enum


class OnboardingChoice(str, Enum):
    FAST = "fast"
    PRIVATE = "private"
    ADVANCED = "advanced"


class OnboardingStep(str, Enum):
    CHOOSE_GOAL = "choose_goal"
    PERMISSIONS = "permissions"
    HOTKEY = "hotkey"
    TEST_DICTATION = "test_dictation"
    CHEAT_SHEET = "cheat_sheet"
    DONE = "done"


ONBOARDING_ORDER: list[OnboardingStep] = [
    OnboardingStep.CHOOSE_GOAL,
    OnboardingStep.PERMISSIONS,
    OnboardingStep.HOTKEY,
    OnboardingStep.TEST_DICTATION,
    OnboardingStep.CHEAT_SHEET,
    OnboardingStep.DONE,
]
_ONBOARDING_INDEX_BY_STEP = {
    step: index for index, step in enumerate(ONBOARDING_ORDER)
}


def _step_order_index(step: OnboardingStep | str) -> int | None:
    normalized_step = (
        step if isinstance(step, OnboardingStep) else coerce_onboarding_step(step)
    )
    if normalized_step is None:
        return None
    return _ONBOARDING_INDEX_BY_STEP.get(normalized_step)


def coerce_onboarding_step(value: str | None) -> OnboardingStep | None:
    if not value:
        return None
    try:
        return OnboardingStep(str(value))
    except ValueError:
        return None


def coerce_onboarding_choice(value: str | None) -> OnboardingChoice | None:
    if not value:
        return None
    try:
        return OnboardingChoice(str(value))
    except ValueError:
        return None


def next_step(step: OnboardingStep) -> OnboardingStep:
    idx = _step_order_index(step)
    if idx is None:
        return OnboardingStep.DONE
    return ONBOARDING_ORDER[min(idx + 1, len(ONBOARDING_ORDER) - 1)]


def prev_step(step: OnboardingStep) -> OnboardingStep:
    idx = _step_order_index(step)
    if idx is None:
        return OnboardingStep.CHOOSE_GOAL
    return ONBOARDING_ORDER[max(idx - 1, 0)]


def step_index(step: OnboardingStep) -> int:
    """Returns 1-based index for UI progress (excluding DONE)."""
    idx = _step_order_index(step)
    if idx is None:
        idx = 0
    # Clamp to the last "real" step.
    max_step = len(ONBOARDING_ORDER) - 2
    return min(idx, max_step) + 1


def total_steps() -> int:
    """Total count of UI steps (excluding DONE)."""
    return len(ONBOARDING_ORDER) - 1

