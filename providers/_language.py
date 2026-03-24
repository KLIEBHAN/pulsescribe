"""Shared helpers for provider language handling."""

from __future__ import annotations


def normalize_auto_language(language: str | None) -> str | None:
    """Return ``None`` for unset/"auto" language values.

    The app uses ``"auto"`` as a UI/env sentinel for auto-detection, but cloud
    providers expect the language field to be omitted entirely in that case.
    """
    if language is None:
        return None

    normalized = language.strip()
    if not normalized or normalized.lower() == "auto":
        return None

    return normalized
