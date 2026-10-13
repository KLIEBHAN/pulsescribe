"""Bounded whitespace normalization for compact UI previews."""


def normalized_text_prefix(text: str, *, max_chars: int) -> str:
    """Return the first ``max_chars`` characters of whitespace-normalized text.

    Stop once the visible budget is filled instead of splitting a full transcript.
    """
    if max_chars <= 0:
        return ""

    chars: list[str] = []
    pending_space = False
    for char in text:
        if char.isspace():
            if chars:
                pending_space = True
            continue

        if pending_space:
            chars.append(" ")
            if len(chars) >= max_chars:
                break
            pending_space = False

        chars.append(char)
        if len(chars) >= max_chars:
            break

    return "".join(chars)
