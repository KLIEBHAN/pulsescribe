"""Shared text helpers for overlay status feedback."""

from __future__ import annotations

DEFAULT_OVERLAY_STATE_TEXTS = {
    "LISTENING": "Listening...",
    "RECORDING": "Recording...",
    "TRANSCRIBING": "Transcribing...",
    "REFINING": "Refining...",
    "LOADING": "Loading model...",
    "DONE": "Done!",
    "ERROR": "Error",
}

OVERLAY_FEEDBACK_DEFAULTS = {
    "LISTENING": "Listening — start speaking",
    "RECORDING": "Recording...",
    "TRANSCRIBING": "Transcribing audio…",
    "REFINING": "Polishing transcript…",
    "LOADING": "Preparing transcription…",
    "DONE": "Transcript pasted",
    "ERROR": "Something went wrong",
}



def _normalize_overlay_text(text: str | None) -> str:
    return " ".join((text or "").replace("\n", " ").split())



def _truncate_overlay_text(text: str, max_chars: int) -> str:
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    if max_chars <= 3:
        return text[:max_chars]
    return text[: max_chars - 3].rstrip() + "..."



def format_overlay_status_text(
    state: str | None,
    text: str | None = None,
    *,
    max_chars: int = 72,
) -> str:
    normalized_state = str(state or "").strip().upper()
    normalized_text = _normalize_overlay_text(text)

    if normalized_text:
        if normalized_state == "ERROR" and not normalized_text.lower().startswith("error"):
            normalized_text = f"Error: {normalized_text}"
        return _truncate_overlay_text(normalized_text, max_chars)

    return DEFAULT_OVERLAY_STATE_TEXTS.get(normalized_state, "")



def build_overlay_feedback_text(
    state: str | None,
    text: str | None = None,
    *,
    max_chars: int = 72,
) -> str:
    normalized_state = str(state or "").strip().upper()
    normalized_text = _normalize_overlay_text(text)

    if normalized_text:
        return format_overlay_status_text(normalized_state, normalized_text, max_chars=max_chars)

    fallback = OVERLAY_FEEDBACK_DEFAULTS.get(
        normalized_state,
        DEFAULT_OVERLAY_STATE_TEXTS.get(normalized_state, ""),
    )
    return _truncate_overlay_text(fallback, max_chars)
