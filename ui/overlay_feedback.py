"""What the overlay shows per state, independent of the UI toolkit."""

from __future__ import annotations

import re
from dataclasses import dataclass

from ui.daemon_status_feedback import build_daemon_status_label
from utils.state import AppState
from utils.transcript_delivery import COPIED, COPY_FAILED, PASTE_FAILED

ERROR_FEEDBACK_SECONDS = 4.0

OVERLAY_STATE_TEXTS = {
    "LISTENING": "Listening…",
    "RECORDING": "Recording",
    "TRANSCRIBING": "Transcribing…",
    "REFINING": "Refining…",
    "LOADING": "Preparing…",
}
NO_SPEECH_TEXT = "Nothing was pasted"

_BARS_BY_STATE = {
    "LISTENING": "low",
    "RECORDING": "level",
    "LOADING": "line",
    "TRANSCRIBING": "line",
    "REFINING": "line",
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
_ERROR_PREFIX = re.compile(r"^error\s*[:\-–—]?\s*", re.IGNORECASE)


@dataclass(frozen=True)
class OverlayContent:
    """One overlay frame: bar mode, optional outcome badge and a text line.

    ``bars`` is ``level`` (audio driven), ``low`` (static row), ``line``
    (flat processing line) or ``none``. ``tone`` picks the badge colors and is
    empty when there is no badge. ``emphasized`` text uses the primary color,
    everything else is muted.
    """

    bars: str = "none"
    text: str = ""
    emphasized: bool = False
    badge: str = ""
    tone: str = ""


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


def _loading_text(detail: str, *, max_chars: int) -> str:
    if not detail:
        return OVERLAY_STATE_TEXTS["LOADING"]
    label = build_daemon_status_label(
        AppState.LOADING,
        detail,
        prefer_detail=True,
        max_chars=max_chars,
    )
    label = label or OVERLAY_STATE_TEXTS["LOADING"]
    return _truncate_overlay_text(_ensure_progress_ellipsis(label), max_chars)


def _error_text(detail: str, *, max_chars: int) -> str:
    """Friendly error detail without an "Error:" prefix (the badge says it)."""
    if not detail:
        return "Something went wrong"
    label = _normalize_overlay_text(
        build_daemon_status_label(AppState.ERROR, detail, max_chars=max_chars)
    )
    if not (label and (label != detail or label.lower() in _FRIENDLY_ERROR_LABELS)):
        label = detail
    return _truncate_overlay_text(_ERROR_PREFIX.sub("", label) or label, max_chars)


def _done_content(detail: str) -> OverlayContent:
    match = _DONE_RTF_PATTERN.match(detail)
    speed = _normalize_overlay_text(match.group(1)) if match else ""
    badge = "Copied" if detail == COPIED else "Pasted"
    return OverlayContent(text=speed, badge=badge, tone="success")


def describe_overlay_content(
    state: str | None,
    text: str | None = None,
    *,
    max_chars: int = 72,
) -> OverlayContent:
    """Map a daemon state payload to what the overlay draws.

    Recording interim text is handled by the platforms on top of the plain
    ``RECORDING`` content because it updates far more often.
    """
    normalized_state = str(state or "").strip().upper()
    detail = _normalize_overlay_text(text)

    if normalized_state == "RECORDING":
        return OverlayContent("level", OVERLAY_STATE_TEXTS["RECORDING"], emphasized=True)
    if normalized_state == "LOADING":
        return OverlayContent("line", _loading_text(detail, max_chars=max_chars))
    if normalized_state in _BARS_BY_STATE:
        return OverlayContent(
            _BARS_BY_STATE[normalized_state], OVERLAY_STATE_TEXTS[normalized_state]
        )
    if normalized_state == "DONE":
        return _done_content(detail)
    if normalized_state == "NO_SPEECH":
        return OverlayContent(text=NO_SPEECH_TEXT, badge="No speech", tone="warn")
    if normalized_state == "ERROR":
        if detail in (PASTE_FAILED, COPY_FAILED):
            return OverlayContent(text=detail, badge="Not pasted", tone="warn")
        return OverlayContent(
            text=_error_text(detail, max_chars=max_chars), badge="Error", tone="error"
        )
    return OverlayContent()
