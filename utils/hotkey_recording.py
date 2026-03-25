"""Helpers for recording hotkeys via macOS NSEvent.

This is used by both the Settings window and the onboarding wizard.
"""

from __future__ import annotations

import logging
from typing import Callable

from utils.hotkey import KEY_CODE_MAP

_REVERSE_KEY_CODE_MAP = {v: k for k, v in KEY_CODE_MAP.items()}
logger = logging.getLogger("pulsescribe.hotkey_recording")


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

    if keycode == 63:
        key = "fn"
    elif keycode == 57:
        key = "capslock"
    else:
        key = _REVERSE_KEY_CODE_MAP.get(keycode)

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


class HotkeyRecorder:
    """Reusable UI helper to record a hotkey via a local NSEvent monitor."""

    def __init__(self) -> None:
        self._recording = False
        self._monitor = None
        self._target_field = None
        self._prev_value: str | None = None
        self._buttons_to_reset: list[object] = []

    @property
    def recording(self) -> bool:
        return bool(self._recording)

    def start(
        self,
        *,
        field,
        button,
        buttons_to_reset: list[object],
        on_hotkey: Callable[[str], object] | Callable[[str], None],
        placeholder: str = "Press desired hotkey…",
    ) -> None:
        if field is None or button is None:
            return

        # Cancel any existing recording session.
        if self._recording:
            self.stop(cancelled=True)

        self._recording = True
        self._target_field = field
        self._prev_value = str(field.stringValue() or "")
        self._buttons_to_reset = [b for b in buttons_to_reset if b is not None]

        for b in self._buttons_to_reset:
            try:
                b.setTitle_("Record")
            except Exception:
                pass
        try:
            button.setTitle_("Press…")
        except Exception:
            pass

        try:
            field.setStringValue_("")
            field.setPlaceholderString_(placeholder)
        except Exception:
            pass

        def _on_hotkey(hotkey_str: str) -> None:
            if not self._recording:
                return
            accepted = True
            try:
                result = on_hotkey(hotkey_str)
                if result is False:
                    accepted = False
            except Exception:
                accepted = False

            if not accepted:
                self.stop(cancelled=True)
                return

            if self._target_field is not None:
                try:
                    self._target_field.setStringValue_(hotkey_str.upper())
                except Exception:
                    pass
            self.stop(cancelled=False)

        def _on_cancel() -> None:
            self.stop(cancelled=True)

        try:
            self._monitor = add_local_hotkey_monitor(
                on_hotkey=_on_hotkey,
                on_cancel=_on_cancel,
            )
        except Exception as exc:
            logger.warning("Hotkey-Aufnahme konnte nicht gestartet werden: %s", exc)
            self.stop(cancelled=True)

    def stop(self, *, cancelled: bool = False) -> None:
        if cancelled and self._target_field is not None and self._prev_value is not None:
            try:
                self._target_field.setStringValue_(self._prev_value)
            except Exception:
                pass

        if self._buttons_to_reset:
            for b in self._buttons_to_reset:
                try:
                    b.setTitle_("Record")
                except Exception:
                    pass

        if self._target_field is not None:
            try:
                self._target_field.setPlaceholderString_(None)
            except Exception:
                pass

        if self._monitor is not None:
            try:
                from AppKit import NSEvent  # type: ignore[import-not-found]

                NSEvent.removeMonitor_(self._monitor)
            except Exception as exc:
                logger.debug(
                    "Hotkey-Monitor konnte nicht sauber entfernt werden: %s",
                    exc,
                )
            self._monitor = None

        self._recording = False
        self._target_field = None
        self._prev_value = None
        self._buttons_to_reset = []


__all__ = ["HotkeyRecorder", "add_local_hotkey_monitor", "nsevent_to_hotkey_string"]
