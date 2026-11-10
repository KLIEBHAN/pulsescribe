"""Shared text formatting helpers for overlay implementations."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

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


@dataclass(frozen=True)
class InterimLines:
    """Live text wrapped into lines, plus what the next update needs.

    ``lines`` holds ``(confirmed, fresh)`` per visible line, oldest first:
    ``fresh`` is the part that is new since the previous update and may still
    change. ``source`` is the text shown and ``start`` the offset in it where
    the first visible line begins.
    """

    lines: tuple[tuple[str, str], ...] = ()
    source: str = ""
    start: int = 0


_SENTENCE_END = (".", "!", "?", "…", "。", "！", "？")
_ANCHOR_CHARS = 24
_LONGEST_WORD_CHARS = 40  # longer runs without a space are text in a script without spaces


def wrap_interim_text(
    text: str,
    previous: InterimLines | None,
    fits: Callable[[str], bool],
    max_lines: int = 2,
) -> InterimLines:
    """Wrap growing live text into at most ``max_lines`` (>= 1) lines that do not reflow.

    ``fits(line)`` tells whether a line fits the text zone. Words keep their
    line and position while the text grows: the first visible line starts with
    the same words as in ``previous``, so later words cannot move earlier ones,
    and a line scrolls out as a whole. A revised word reflows only what follows
    it. That also holds when text before the visible lines changed or the
    source lost characters at its start (a tail read). A word wider than the
    zone, such as text in a script without spaces, breaks between characters.

    Visible text shared with ``previous`` counts as confirmed, the rest as
    fresh. Text that ends a sentence is confirmed as a whole.
    """
    source = " ".join(text.split())
    if not source:
        return InterimLines()

    start = 0
    confirmed = 0
    if previous is not None and previous.source:
        shown = previous.source[previous.start :]
        found = _find_line_start(source, shown, previous)
        start = found or 0
        before = previous.source if found is None else shown
        confirmed = start + _common_word_prefix(before, source[start:])
    if source.endswith(_SENTENCE_END):
        confirmed = len(source)

    lines: list[tuple[int, str]] = []  # (offset in source, text)
    offset = start
    for word in source[start:].split(" "):
        if lines and fits(f"{lines[-1][1]} {word}"):
            lines[-1] = (lines[-1][0], f"{lines[-1][1]} {word}")
        else:
            lines.extend(_break_word(word, offset, fits))
        offset += len(word) + 1
    visible = lines[-max_lines:]
    return InterimLines(
        lines=tuple(
            (line[: max(confirmed - at, 0)], line[max(confirmed - at, 0) :])
            for at, line in visible
        ),
        source=source,
        start=visible[0][0],
    )


def _find_line_start(source: str, shown: str, previous: InterimLines) -> int | None:
    """Offset in ``source`` where the first line shown before begins again, if it does."""
    if previous.start == 0:
        return 0
    found = _occurrences(source, shown[:_ANCHOR_CHARS])
    if not found:
        return None
    at = min(found, key=lambda position: abs(position - previous.start))
    # A line begins at a word, unless it already began inside one (no spaces).
    began_inside_word = previous.source[previous.start - 1] != " "
    return at if at == 0 or source[at - 1] == " " or began_inside_word else None


def _occurrences(text: str, part: str) -> list[int]:
    positions = []
    at = text.find(part)
    while at >= 0:
        positions.append(at)
        at = text.find(part, at + 1)
    return positions


def _break_word(word: str, offset: int, fits: Callable[[str], bool]) -> list[tuple[int, str]]:
    """``word`` as lines: one line, or broken between characters if it is too wide."""
    if fits(word):
        return [(offset, word)]
    pieces: list[tuple[int, str]] = []
    piece = ""
    for index, char in enumerate(word):
        if piece and not fits(piece + char):
            pieces.append((offset + index - len(piece), piece))
            piece = ""
        piece += char
    pieces.append((offset + len(word) - len(piece), piece))
    return pieces


def _common_word_prefix(previous: str, current: str) -> int:
    """Length of the leading part of ``current`` whose whole words match ``previous``."""
    limit = min(len(previous), len(current))
    same = next((i for i in range(limit) if previous[i] != current[i]), limit)
    if all(same == len(text) or text[same] == " " for text in (previous, current)):
        return same
    word_start = current.rfind(" ", 0, same) + 1
    return word_start if same - word_start <= _LONGEST_WORD_CHARS else same
