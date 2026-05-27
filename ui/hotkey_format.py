"""Shared hotkey display formatting for UI surfaces."""

from __future__ import annotations

from collections.abc import Mapping


def normalize_hotkey_text(value: str | None) -> str:
    """Return the raw hotkey text without surrounding whitespace."""
    return (value or "").strip()


def format_hotkey_for_display(
    value: str | None,
    token_labels: Mapping[str, str],
    *,
    strip_parts: bool = False,
    omit_empty_parts: bool = False,
    title_unknown_parts: bool = False,
) -> str:
    """Format a configured hotkey using the caller's platform-specific labels."""
    normalized = normalize_hotkey_text(value).lower()
    if not normalized:
        return ""

    display_parts: list[str] = []
    for raw_part in normalized.split("+"):
        part = raw_part.strip() if strip_parts else raw_part
        if omit_empty_parts and not part:
            continue

        display = token_labels.get(part)
        if display is None:
            if part.startswith("f") and part[1:].isdigit():
                display = part.upper()
            elif len(part) == 1:
                display = part.upper()
            elif title_unknown_parts:
                display = part.replace("_", " ").title()
            else:
                display = part.capitalize()
        display_parts.append(display)

    return "+".join(display_parts)


__all__ = ["format_hotkey_for_display", "normalize_hotkey_text"]
