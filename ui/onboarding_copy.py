"""Setup wizard copy shared by the macOS and Windows wizards.

Callers pass hotkeys already formatted for their platform (e.g. "Fn" or
"Ctrl+Win"), so both wizards show the same sentences for the same setup.
Review rows return ``(detail, badge, tone)``; ``tone`` is a badge tone from
``ui.settings_theme.BADGE_TONES`` ("ok", "warn", "err", "muted").
"""

from __future__ import annotations

from utils.onboarding import OnboardingStep

STEP_NAV_LABELS = {
    OnboardingStep.CHOOSE_GOAL: "Setup",
    OnboardingStep.PERMISSIONS: "Permissions",
    OnboardingStep.HOTKEY: "Hotkey",
    OnboardingStep.TEST_DICTATION: "Practice",
    OnboardingStep.CHEAT_SHEET: "Review",
}

# Provider mode -> API key it needs (Local needs none).
MODE_API_KEYS = {
    "deepgram": "DEEPGRAM_API_KEY",
    "openai": "OPENAI_API_KEY",
    "groq": "GROQ_API_KEY",
}

FAST_API_KEY_PROMPT = "Fast mode needs a Deepgram API key. Paste one to continue."


HOTKEY_CUSTOM_KEY = "custom"
HOTKEY_CUSTOM_DESCRIPTION = "Record your own shortcut."


def hotkey_choice_title(kind: str, hotkey: str) -> str:
    """Row label such as "Hold Fn" or "Toggle Ctrl+Alt+R"."""
    return f"{'Hold' if kind == 'hold' else 'Toggle'} {hotkey}"


def select_hotkey_choice(toggle: str, hold: str, presets) -> str | None:
    """Hotkey row for a config; ``None`` when none or both hotkeys are set.

    ``presets`` yields ``(key, kind, hotkey)``; any other single hotkey is Custom.
    """
    if bool(toggle) == bool(hold):
        return None
    current = ("toggle", toggle) if toggle else ("hold", hold)
    for key, kind, hotkey in presets:
        if (kind, hotkey) == current:
            return key
    return HOTKEY_CUSTOM_KEY


def build_two_hotkeys_hint(toggle: str, hold: str) -> str:
    """Hint shown on the Hotkey step while both a Toggle and a Hold hotkey exist."""
    if not (toggle and hold):
        return ""
    return (
        f"You use two hotkeys: {hotkey_choice_title('toggle', toggle)} and "
        f"{hotkey_choice_title('hold', hold)}.\n"
        "Pick a row to use just one, or manage both in Settings → General."
    )


def build_provider_summary(mode_label: str, mode: str, has_key: bool) -> tuple[str, str, str]:
    """Provider row: the mode label and whether its API key is there."""
    if mode == "local" or has_key:
        return mode_label, "Ready", "ok"
    return mode_label, "No API key", "warn"


def build_hotkey_summary(toggle: str, hold: str) -> tuple[str, str, str]:
    """Hotkey row, e.g. ``("Toggle Ctrl+Alt+R · Hold Ctrl+Win", "Set", "ok")``."""
    parts = [f"Toggle {toggle}"] if toggle else []
    if hold:
        parts.append(f"Hold {hold}")
    if not parts:
        return "Choose one in the Hotkey step", "Not set", "warn"
    return " · ".join(parts), "Set", "ok"


def build_test_summary(outcome: str | None) -> tuple[str, str, str]:
    """Practice test row for a test outcome ("passed", "empty"/"no_speech", ...)."""
    normalized = (outcome or "pending").strip().lower()
    if normalized == "passed":
        return "Your speech was transcribed", "Worked", "ok"
    if normalized == "skipped":
        return "Skipped for now", "Skipped", "muted"
    if normalized == "cancelled":
        return "The test was cancelled", "Cancelled", "muted"
    if normalized in {"empty", "no_speech"}:
        return "No speech was detected", "No speech", "warn"
    if normalized == "error":
        return "The test could not finish", "Error", "err"
    if normalized in {"starting", "connecting", "recording", "processing"}:
        return "The test is still running", "Running", "warn"
    return "Not run yet", "Not run", "muted"


def build_ready_text(toggle: str, hold: str) -> str:
    """Closing line of the Review step, naming the configured hotkey."""
    if hold and toggle:
        return f"Hold {hold} or press {toggle} in any app to dictate."
    if hold:
        return f"Hold {hold} in any app to dictate."
    if toggle:
        return f"Press {toggle} in any app to dictate, and again to stop."
    return "Set a hotkey in Settings to start dictating."


__all__ = [
    "FAST_API_KEY_PROMPT",
    "HOTKEY_CUSTOM_DESCRIPTION",
    "HOTKEY_CUSTOM_KEY",
    "build_two_hotkeys_hint",
    "hotkey_choice_title",
    "select_hotkey_choice",
    "MODE_API_KEYS",
    "STEP_NAV_LABELS",
    "build_hotkey_summary",
    "build_provider_summary",
    "build_ready_text",
    "build_test_summary",
]
