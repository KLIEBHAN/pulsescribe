"""Shared text helpers for overlay status feedback."""

from __future__ import annotations

import re

from ui.daemon_status_feedback import build_daemon_status_label
from utils.state import AppState

DEFAULT_OVERLAY_STATE_TEXTS = {
    "LISTENING": "Listening…",
    "RECORDING": "Recording — keep speaking",
    "TRANSCRIBING": "Transcribing…",
    "REFINING": "Refining…",
    "LOADING": "Preparing transcription…",
    "DONE": "Transcript pasted",
    "NO_SPEECH": "No speech detected",
    "ERROR": "Error",
}

OVERLAY_FEEDBACK_DEFAULTS = {
    "LISTENING": "Listening — start speaking",
    "RECORDING": "Recording — keep speaking",
    "TRANSCRIBING": "Transcribing your dictation…",
    "REFINING": "Polishing wording and punctuation…",
    "LOADING": "Preparing transcription…",
    "DONE": "Transcript pasted",
    "NO_SPEECH": "No speech detected",
    "ERROR": "Something went wrong",
}

_DONE_RTF_PATTERN = re.compile(r"^[✓✔]\s*\(([^()]+)\)\s*$")
_FRIENDLY_ERROR_LABELS = {
    "missing api key",
    "pulsescribe is busy",
    "transcription timed out",
    "microphone unavailable",
    "could not reach the transcription service",
    "missing dependency",
    "something went wrong",
}


def _normalize_overlay_text(text: str | None) -> str:
    return " ".join((text or "").replace("\n", " ").split())


def _truncate_overlay_text(text: str, max_chars: int) -> str:
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    if max_chars <= 3:
        return text[:max_chars]
    return text[: max_chars - 3].rstrip() + "..."


def _ensure_progress_ellipsis(text: str) -> str:
    cleaned = str(text or "").strip()
    if not cleaned:
        return ""
    if cleaned.endswith("…"):
        return cleaned
    if cleaned.endswith("..."):
        return cleaned[:-3].rstrip() + "…"
    return cleaned.rstrip(".!? ") + "…"


def _build_loading_feedback_text(detail: str, *, max_chars: int) -> str:
    label = build_daemon_status_label(
        AppState.LOADING,
        detail,
        prefer_detail=True,
        max_chars=max_chars,
    )
    if not label:
        label = OVERLAY_FEEDBACK_DEFAULTS["LOADING"]
    return _truncate_overlay_text(_ensure_progress_ellipsis(label), max_chars)


def _build_done_feedback_text(detail: str, *, max_chars: int) -> str:
    match = _DONE_RTF_PATTERN.match(detail)
    if match:
        speed = _normalize_overlay_text(match.group(1))
        if speed:
            return _truncate_overlay_text(
                f"Transcript pasted • {speed} realtime",
                max_chars,
            )
    return format_overlay_status_text("DONE", detail, max_chars=max_chars)


def _build_no_speech_feedback_text(detail: str, *, max_chars: int) -> str:
    label = _normalize_overlay_text(
        build_daemon_status_label(
            AppState.NO_SPEECH,
            detail,
            max_chars=max_chars,
        )
    )
    if label:
        return _truncate_overlay_text(label, max_chars)
    return _truncate_overlay_text(OVERLAY_FEEDBACK_DEFAULTS["NO_SPEECH"], max_chars)


def _build_error_feedback_text(detail: str, *, max_chars: int) -> str:
    label = _normalize_overlay_text(
        build_daemon_status_label(
            AppState.ERROR,
            detail,
            max_chars=max_chars,
        )
    )
    if label and (label != detail or label.lower() in _FRIENDLY_ERROR_LABELS):
        return _truncate_overlay_text(label, max_chars)
    return format_overlay_status_text("ERROR", detail, max_chars=max_chars)


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
        if normalized_state == "LOADING":
            return _build_loading_feedback_text(normalized_text, max_chars=max_chars)
        if normalized_state == "DONE":
            return _build_done_feedback_text(normalized_text, max_chars=max_chars)
        if normalized_state == "NO_SPEECH":
            return _build_no_speech_feedback_text(normalized_text, max_chars=max_chars)
        if normalized_state == "ERROR":
            return _build_error_feedback_text(normalized_text, max_chars=max_chars)
        return format_overlay_status_text(
            normalized_state,
            normalized_text,
            max_chars=max_chars,
        )

    fallback = OVERLAY_FEEDBACK_DEFAULTS.get(
        normalized_state,
        DEFAULT_OVERLAY_STATE_TEXTS.get(normalized_state, ""),
    )
    return _truncate_overlay_text(fallback, max_chars)
