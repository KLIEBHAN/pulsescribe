"""Shared text formatting helpers for overlay implementations."""

DEFAULT_RECORDING_INTERIM_MAX_CHARS = 45


def format_recording_interim_text(
    text: str,
    max_chars: int = DEFAULT_RECORDING_INTERIM_MAX_CHARS,
) -> str:
    """Normalize whitespace and tail-truncate recording interim text."""
    if not text:
        return ""

    cleaned_tail, truncated = _normalized_tail(text, max_chars)
    if not cleaned_tail or not truncated:
        return cleaned_tail
    return _ellipsis_tail(cleaned_tail, max_chars)


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
