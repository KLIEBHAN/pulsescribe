"""Shared UX copy for Save & Apply / local settings state.

Keeps Windows Settings and macOS Welcome aligned for:
- loaded/saved state communication
- local unsaved changes
- relaunch-required caveats
- small action hints that point back to Save & Apply
"""


def build_settings_loaded_feedback() -> tuple[str, str]:
    return (
        "Showing the current saved settings. Changes stay local until you click Save & Apply.",
        "text_secondary",
    )


def build_unsaved_settings_feedback(*, relaunch_required: bool = False) -> tuple[str, str]:
    text = "You have local changes. Click Save & Apply to keep them."
    if relaunch_required:
        text = f"{text} Dock icon changes still need a relaunch."
    return text, "warning"


def build_settings_saved_feedback(
    *,
    auto_reload_worked: bool | None = None,
    relaunch_required: bool = False,
) -> tuple[str, str]:
    normalized_reload_state = auto_reload_worked
    if auto_reload_worked in (0, 1):
        normalized_reload_state = bool(auto_reload_worked)
    elif isinstance(auto_reload_worked, str):
        normalized_text = auto_reload_worked.strip().lower()
        if normalized_text in {"true", "1", "yes", "on"}:
            normalized_reload_state = True
        elif normalized_text in {"false", "0", "no", "off"}:
            normalized_reload_state = False

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


def build_save_apply_change_hint(*, plural: bool = False) -> str:
    return (
        "Click Save & Apply to keep these changes."
        if plural
        else "Click Save & Apply to keep this change."
    )


__all__ = [
    "build_save_apply_change_hint",
    "build_settings_loaded_feedback",
    "build_settings_saved_feedback",
    "build_unsaved_settings_feedback",
]
