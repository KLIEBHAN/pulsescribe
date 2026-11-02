"""Shared UX copy for Save & Apply / local settings state.

Keeps Windows Settings and macOS Welcome aligned for:
- saved state communication
- the unsaved change count in the save bar
- relaunch-required caveats
- small action hints that point back to Save & Apply
"""

from collections.abc import Sequence


# Settings tab that hosts each draft section saved by Save & Apply.
_SECTION_TAB_LABELS = {"prompts": "Refine", "vocabulary": "Vocabulary"}


def build_settings_saved_feedback(
    *,
    auto_reload_worked: bool | None = None,
    relaunch_required: bool = False,
    failed_sections: Sequence[str] = (),
) -> tuple[str, str]:
    """Report partial file saves together with any reload or relaunch caveat."""
    normalized_reload_state = auto_reload_worked
    if auto_reload_worked in (0, 1):
        normalized_reload_state = bool(auto_reload_worked)
    elif isinstance(auto_reload_worked, str):
        normalized_text = auto_reload_worked.strip().lower()
        if normalized_text in {"true", "1", "yes", "on"}:
            normalized_reload_state = True
        elif normalized_text in {"false", "0", "no", "off"}:
            normalized_reload_state = False

    if failed_sections:
        names = " and ".join(failed_sections)
        tab_labels = list(
            dict.fromkeys(
                _SECTION_TAB_LABELS.get(section, section.title())
                for section in failed_sections
            )
        )
        tabs = " and ".join(tab_labels)
        tab_word = "tab" if len(tab_labels) == 1 else "tabs"
        text = (
            f"Settings saved, but the {names} could not be saved. "
            f"Check the {tabs} {tab_word} and try again."
        )
        if normalized_reload_state is False:
            text += (
                " Automatic reload failed. Restart PulseScribe to apply saved settings."
            )
        if relaunch_required:
            text += " Relaunch PulseScribe to apply the Dock icon change."
        return text, "warning"

    if normalized_reload_state is True:
        if relaunch_required:
            return (
                "Settings saved. PulseScribe will reload most changes automatically. "
                "Relaunch PulseScribe to apply the Dock icon change.",
                "success",
            )
        return (
            "Settings saved. PulseScribe will reload them automatically.",
            "success",
        )

    if normalized_reload_state is False:
        if relaunch_required:
            return (
                "Settings saved, but automatic reload failed. Relaunch PulseScribe "
                "to apply the changes, including the Dock icon change.",
                "warning",
            )
        return (
            "Settings saved, but automatic reload failed. Restart PulseScribe to "
            "apply the changes.",
            "warning",
        )

    text = "Settings saved."
    color = "success"

    if relaunch_required:
        text = f"{text} Relaunch PulseScribe to apply the Dock icon change."

    return text, color


def count_settings_changes(saved: Sequence[object], current: Sequence[object]) -> int:
    """Count changed entries between two settings signatures.

    Nested tuples (such as the API key pairs) count once per changed entry.
    """
    changes = 0
    for before, after in zip(saved, current):
        if (
            isinstance(before, tuple)
            and isinstance(after, tuple)
            and len(before) == len(after)
        ):
            changes += sum(old != new for old, new in zip(before, after))
        else:
            changes += before != after
    return changes


def build_unsaved_changes_text(count: int) -> str:
    """Save bar label, e.g. ``"2 unsaved changes"``."""
    return f"{count} unsaved change{'' if count == 1 else 's'}"


def build_save_apply_change_hint(*, plural: bool = False) -> str:
    return (
        "Click Save & Apply to keep these changes."
        if plural
        else "Click Save & Apply to keep this change."
    )


__all__ = [
    "build_save_apply_change_hint",
    "build_unsaved_changes_text",
    "count_settings_changes",
    "build_settings_saved_feedback",
]
