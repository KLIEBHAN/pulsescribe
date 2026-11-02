"""Shared text formatting helpers for overlay implementations."""

DEFAULT_RECORDING_INTERIM_MAX_CHARS = 45
# Overlays that fade clipped text on the left need a tail longer than the
# text zone; the zone clips it, so no "..." is added.
FADED_RECORDING_INTERIM_MAX_CHARS = 120


def format_recording_duration(seconds: float) -> str:
    """Format a recording duration as ``m:ss`` (minutes are not capped)."""
    minutes, secs = divmod(max(int(seconds), 0), 60)
    return f"{minutes}:{secs:02d}"


def format_recording_interim_text(
    text: str,
    max_chars: int = DEFAULT_RECORDING_INTERIM_MAX_CHARS,
    *,
    ellipsis: bool = True,
) -> str:
    """Normalize whitespace and tail-truncate recording interim text."""
    if not text:
        return ""

    cleaned_tail, truncated = _normalized_tail(text, max_chars)
    if not cleaned_tail or not truncated:
        return cleaned_tail
    if not ellipsis:
        return cleaned_tail[-max_chars:]
    return _ellipsis_tail(cleaned_tail, max_chars)


def split_recording_interim_text(
    text: str,
    previous_text: str = "",
    max_chars: int = DEFAULT_RECORDING_INTERIM_MAX_CHARS,
    *,
    ellipsis: bool = True,
) -> tuple[str, str]:
    """Split formatted interim text into ``(earlier, newest)`` display parts.

    ``newest`` holds the words added since ``previous_text`` so overlays can
    emphasize them. A revised transcript (no common prefix) emphasizes only
    its last word.
    """
    formatted = format_recording_interim_text(text, max_chars, ellipsis=ellipsis)
    if not formatted:
        return "", ""
    if previous_text and text.startswith(previous_text):
        added = " ".join(text[len(previous_text) :].split())
        if not added:
            return formatted, ""
        split_at = max(len(formatted) - len(added), 0)
    else:
        split_at = len(formatted) if previous_text else 0
    split_at = formatted.rfind(" ", 0, split_at) + 1 if split_at else 0
    return formatted[:split_at], formatted[split_at:]


def _normalized_tail(text: str, max_chars: int) -> tuple[str, bool]:
    normalized_tail_reversed: list[str] = []
    normalized_length = 0
    pending_space = False
    truncated = False

    for char in reversed(text):
        if char.isspace():
            if normalized_length > 0:
                pending_space = True
            continue

        if pending_space:
            normalized_tail_reversed.append(" ")
            normalized_length += 1
            pending_space = False

        normalized_tail_reversed.append(char)
        normalized_length += 1
        if max_chars > 0 and normalized_length > max_chars:
            truncated = True
            break

    return "".join(reversed(normalized_tail_reversed)), truncated


def _ellipsis_tail(cleaned_tail: str, max_chars: int) -> str:
    if max_chars <= 0:
        return cleaned_tail

    tail_chars = max_chars - 3
    if tail_chars <= 0:
        return "..."
    return "..." + cleaned_tail[-tail_chars:]
