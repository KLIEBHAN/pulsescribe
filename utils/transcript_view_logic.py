"""Pure helpers for transcript-view refresh state.

The UI controllers for macOS and Windows are highly dynamic and difficult to
analyze statically. These helpers capture the append/empty-state decisions in a
small typed surface that can be verified with pyright.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence


TranscriptEntry = Mapping[str, object]


def build_transcript_payload(
    entries: Sequence[TranscriptEntry],
    *,
    blocks: Sequence[str],
    empty_text: str,
) -> tuple[str, list[TranscriptEntry], list[str], int]:
    """Build the rendered transcript payload plus cached oldest-first state."""
    rendered_blocks = [block for block in blocks if block]
    text = "\n\n".join(rendered_blocks) if rendered_blocks else empty_text
    newest_first_entries = list(entries)
    oldest_first_entries = list(reversed(newest_first_entries))
    return text, oldest_first_entries, rendered_blocks, len(newest_first_entries)


def should_append_transcript_delta_in_place(
    previous_entries: Sequence[TranscriptEntry] | None,
    *,
    entries_trimmed: bool,
    last_text: str | None,
    scroll_to_bottom: bool,
    is_near_bottom: bool,
) -> bool:
    """Return whether an append-only refresh may extend the current text view."""
    return (
        not entries_trimmed
        and bool(previous_entries)
        and last_text is not None
        and (scroll_to_bottom or is_near_bottom)
    )


__all__ = [
    "TranscriptEntry",
    "build_transcript_payload",
    "should_append_transcript_delta_in_place",
]
