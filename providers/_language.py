"""Shared helpers for provider language handling."""

from __future__ import annotations


def _normalize_language_text(language: str | None) -> str:
    """Return a stripped language string or ``""`` for unset values."""
    return "" if language is None else language.strip()


def is_auto_language(language: str | None) -> bool:
    """Return ``True`` when a language value means auto-detection."""
    normalized = _normalize_language_text(language)
    return not normalized or normalized.lower() == "auto"


def normalize_auto_language(language: str | None) -> str | None:
    """Return ``None`` for unset/"auto" language values.

    The app uses ``"auto"`` as a UI/env sentinel for auto-detection, but cloud
    providers expect the language field to be omitted entirely in that case.
    """
    normalized = _normalize_language_text(language)
    if not normalized or normalized.lower() == "auto":
        return None

    return normalized
