"""Helpers for recording hotkeys via macOS NSEvent.

This is used by both the Settings window and the onboarding wizard.
"""

from __future__ import annotations

from typing import Callable

from utils.hotkey import KEY_CODE_MAP


def nsevent_to_hotkey_string(event) -> str | None:
    """Converts an NSEvent (key down / flags changed) to a canonical hotkey string.

    Returns strings like:
      - "f19"
      - "fn"
      - "option+space"
      - "cmd+shift+r"
    """
    from AppKit import (  # type: ignore[import-not-found]
        NSEventModifierFlagCommand,
        NSEventModifierFlagControl,
        NSEventModifierFlagOption,
        NSEventModifierFlagShift,
        NSEventTypeFlagsChanged,
    )

    keycode = int(event.keyCode())

    # Ignore pure modifier flag changes (except Fn/CapsLock which we support).
    if event.type() == NSEventTypeFlagsChanged and keycode not in (63, 57):
        return None

    reverse_map = {v: k for k, v in KEY_CODE_MAP.items()}

    if keycode == 63:
        key = "fn"
    elif keycode == 57:
        key = "capslock"
    else:
        key = reverse_map.get(keycode)

    if not key:
        chars = event.charactersIgnoringModifiers()
        if chars:
            key = chars.lower()

    if not key:
        return None

    flags = int(event.modifierFlags())
    modifiers: list[str] = []
    if flags & NSEventModifierFlagControl:
        modifiers.append("ctrl")
    if flags & NSEventModifierFlagOption:
        modifiers.append("option")
    if flags & NSEventModifierFlagShift:
        modifiers.append("shift")
    if flags & NSEventModifierFlagCommand:
        modifiers.append("cmd")

    return "+".join(modifiers + [key]) if modifiers else key


def add_local_hotkey_monitor(
    *,
    on_hotkey: Callable[[str], None],
    on_cancel: Callable[[], None] | None = None,
) -> object:
    """Installs a local NSEvent monitor for recording a hotkey.

    - Pressing ESC triggers `on_cancel` (if provided).
    - The captured hotkey is delivered via `on_hotkey`.

    Returns an opaque monitor token that must be removed via `NSEvent.removeMonitor_`.
    """
    from AppKit import (  # type: ignore[import-not-found]
        NSEvent,
        NSEventMaskFlagsChanged,
        NSEventMaskKeyDown,
    )

    def handler(event):
        try:
            if int(event.keyCode()) == 53:  # ESC
                if callable(on_cancel):
                    on_cancel()
                return None
        except Exception:
            pass

        hotkey_str = nsevent_to_hotkey_string(event)
        if hotkey_str:
            try:
                on_hotkey(hotkey_str)
            except Exception:
                pass
            return None
        return event

    mask = NSEventMaskKeyDown | NSEventMaskFlagsChanged
    return NSEvent.addLocalMonitorForEventsMatchingMask_handler_(mask, handler)


__all__ = ["add_local_hotkey_monitor", "nsevent_to_hotkey_string"]

