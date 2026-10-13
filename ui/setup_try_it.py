"""Shared copy for the Setup tab's "Try it" card on macOS and Windows.

Callers pass hotkeys already formatted for their platform (e.g. "Option+Space"
or "Ctrl+Alt+R") plus a platform-specific hint, so both settings windows show
the same guidance for the same hotkey configuration.
"""

CHANGE_HOTKEYS_LABEL = "Change Hotkeys…"
SET_UP_HOTKEYS_LABEL = "Set up Hotkeys…"


def build_setup_hotkey_info(toggle: str, hold: str, fallback: str = "") -> str:
    """Return the short hotkey summary shown next to the card title."""
    hotkey_parts = []
    if toggle:
        hotkey_parts.append(f"Toggle: {toggle}")
    if hold:
        hotkey_parts.append(f"Hold: {hold}")
    if hotkey_parts:
        return " • ".join(hotkey_parts)
    if fallback:
        return f"Hotkey: {fallback}"
    return "No hotkey configured"


def build_setup_try_it_content(
    toggle: str,
    hold: str,
    fallback: str = "",
    *,
    hint: str,
) -> tuple[str, str, str, str]:
    """Return ``(hotkey_info, body, hint, button_title)`` for the "Try it" card."""
    hotkey_info = build_setup_hotkey_info(toggle, hold, fallback)
    active_key = toggle or fallback

    if hold and toggle:
        body = f"Hold {hold} for push-to-talk, or press {toggle} once to start and again to stop."
    elif hold:
        body = f"Hold {hold} while speaking, then release it to transcribe into the frontmost app."
    elif active_key:
        body = f"Press {active_key} to start dictation, then use it again to stop and paste the result."
    else:
        return (
            hotkey_info,
            "No hotkey yet. Open Hotkeys to record one before you test dictation.",
            hint,
            SET_UP_HOTKEYS_LABEL,
        )
    return hotkey_info, body, hint, CHANGE_HOTKEYS_LABEL


__all__ = [
    "CHANGE_HOTKEYS_LABEL",
    "SET_UP_HOTKEYS_LABEL",
    "build_setup_hotkey_info",
    "build_setup_try_it_content",
]
