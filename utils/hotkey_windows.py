"""Windows hotkey parsing and normalization helpers.

These helpers are platform-agnostic and reusable from UI + daemon code.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("pulsescribe")

_MODIFIER_ALIASES = {
    "ctrl": "ctrl",
    "control": "ctrl",
    "alt": "alt",
    "option": "alt",
    "shift": "shift",
    "win": "win",
    "cmd": "win",
    "command": "win",
}

_SPECIAL_KEY_ALIASES = {
    "space": "space",
    "tab": "tab",
    "enter": "enter",
    "return": "enter",
    "esc": "esc",
    "escape": "esc",
    "backspace": "backspace",
    "delete": "delete",
    "home": "home",
    "end": "end",
    "pageup": "pageup",
    "pagedown": "pagedown",
    "up": "up",
    "down": "down",
    "left": "left",
    "right": "right",
    "capslock": "capslock",
    "caps_lock": "capslock",
}

_MODIFIER_ORDER = ("ctrl", "alt", "shift", "win")
_PYNPUT_SPECIAL_KEY_ATTRS = {
    "space": "space",
    "tab": "tab",
    "enter": "enter",
    "esc": "esc",
    "backspace": "backspace",
    "delete": "delete",
    "home": "home",
    "end": "end",
    "pageup": "page_up",
    "pagedown": "page_down",
    "up": "up",
    "down": "down",
    "left": "left",
    "right": "right",
    "capslock": "caps_lock",
}


def _normalize_part(raw_part: str) -> str | None:
    part = raw_part.strip().lower()
    if not part:
        return None

    if part in _MODIFIER_ALIASES:
        return _MODIFIER_ALIASES[part]
    if part in _SPECIAL_KEY_ALIASES:
        return _SPECIAL_KEY_ALIASES[part]
    if part.startswith("f") and part[1:].isdigit():
        fn = int(part[1:])
        if 1 <= fn <= 24:
            return f"f{fn}"
        return None
    if len(part) == 1:
        return part
    return None


def normalize_windows_hotkey(hotkey_str: str | None) -> tuple[str, str | None]:
    """Return canonical hotkey string and optional validation error."""
    if hotkey_str is None:
        return "", None

    stripped = hotkey_str.strip()
    if not stripped:
        return "", None

    raw_parts = [part for part in stripped.split("+")]
    if any(not part.strip() for part in raw_parts):
        return "", "Ungueltiges Hotkey-Format."

    normalized_parts: list[str] = []
    seen: set[str] = set()
    for raw_part in raw_parts:
        part = _normalize_part(raw_part)
        if part is None:
            return "", f"Unbekannte Taste: {raw_part.strip().lower()}"
        if part in seen:
            continue
        seen.add(part)
        normalized_parts.append(part)

    modifiers = [m for m in _MODIFIER_ORDER if m in seen]
    keys = [p for p in normalized_parts if p not in _MODIFIER_ORDER]
    return "+".join(modifiers + keys), None


def parse_windows_hotkey_for_pynput(hotkey_str: str, keyboard: Any) -> set[Any]:
    """Parse a hotkey string into a ``set`` of ``pynput.keyboard`` keys."""
    normalized, error = normalize_windows_hotkey(hotkey_str)
    if error:
        logger.warning(f"Ungueltiger Hotkey '{hotkey_str}': {error}")
        return set()
    if not normalized:
        return set()

    hotkey_keys: set[Any] = set()
    for part in normalized.split("+"):
        if part == "ctrl":
            hotkey_keys.add(keyboard.Key.ctrl)
            continue
        if part == "alt":
            hotkey_keys.add(keyboard.Key.alt)
            continue
        if part == "shift":
            hotkey_keys.add(keyboard.Key.shift)
            continue
        if part == "win":
            hotkey_keys.add(keyboard.Key.cmd)
            continue
        if part.startswith("f") and part[1:].isdigit():
            key_obj = getattr(keyboard.Key, part, None)
            if key_obj is None:
                logger.warning(f"F-Taste nicht unterstuetzt: {part}")
                return set()
            hotkey_keys.add(key_obj)
            continue
        if part in _PYNPUT_SPECIAL_KEY_ATTRS:
            key_attr = _PYNPUT_SPECIAL_KEY_ATTRS[part]
            key_obj = getattr(keyboard.Key, key_attr, None)
            if key_obj is None:
                logger.warning(f"Sondertaste nicht unterstuetzt: {part}")
                return set()
            hotkey_keys.add(key_obj)
            continue
        if len(part) == 1:
            hotkey_keys.add(keyboard.KeyCode.from_char(part))
            continue
        logger.warning(f"Unbekannte Taste ignoriert: {part}")
        return set()

    return hotkey_keys


__all__ = ["normalize_windows_hotkey", "parse_windows_hotkey_for_pynput"]
