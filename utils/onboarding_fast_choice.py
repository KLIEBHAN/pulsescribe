"""Typed helpers for Fast onboarding mode selection.

These helpers keep the API-key resolution logic independent from AppKit/Qt UI
controllers so it can be type-checked in isolation.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FastChoiceResolution:
    """Resolved Fast-mode outcome for onboarding flows."""

    pending_updates: dict[str, str]
    resolved_mode: str | None
    should_prompt_for_api_key: bool


def _normalize(value: str | None) -> str:
    """Return a trimmed string value or an empty string."""
    return (value or "").strip()


def resolve_fast_choice_updates(
    *,
    entered_deepgram_key: str | None,
    cached_deepgram_key: str | None,
    env_deepgram_key: str | None,
    cached_groq_key: str | None,
    env_groq_key: str | None,
    current_mode: str | None = None,
) -> FastChoiceResolution:
    """Resolve Fast-mode env updates without mutating existing API keys.

    The user may continue with an already persisted Deepgram or Groq key without
    re-entering it. In that case we should switch the mode if needed, but must
    not clear the stored Deepgram key by writing ``None``.
    """
    entered_key = _normalize(entered_deepgram_key)
    current_deepgram_key = _normalize(cached_deepgram_key) or _normalize(
        env_deepgram_key
    )
    current_groq_key = _normalize(cached_groq_key) or _normalize(env_groq_key)
    normalized_mode = _normalize(current_mode).lower()

    pending_updates: dict[str, str] = {}
    if entered_key and entered_key != current_deepgram_key:
        pending_updates["DEEPGRAM_API_KEY"] = entered_key

    has_deepgram = bool(pending_updates.get("DEEPGRAM_API_KEY") or current_deepgram_key)
    has_groq = bool(current_groq_key)
    if not has_deepgram and not has_groq:
        return FastChoiceResolution(
            pending_updates={},
            resolved_mode=None,
            should_prompt_for_api_key=True,
        )

    resolved_mode = "deepgram" if has_deepgram else "groq"
    if normalized_mode != resolved_mode:
        pending_updates["PULSESCRIBE_MODE"] = resolved_mode

    return FastChoiceResolution(
        pending_updates=pending_updates,
        resolved_mode=resolved_mode,
        should_prompt_for_api_key=False,
    )


__all__ = ["FastChoiceResolution", "resolve_fast_choice_updates"]
