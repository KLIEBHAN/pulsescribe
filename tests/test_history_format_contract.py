from __future__ import annotations

import pytest

from utils.history import (
    DISPLAY_TRANSCRIPTS_EMPTY_MESSAGE,
    WELCOME_TRANSCRIPTS_EMPTY_MESSAGE,
    format_transcript_entries_for_display,
    format_transcript_entries_for_welcome,
    format_transcript_entry_for_display,
    format_transcript_entry_for_welcome,
    format_transcripts_for_display,
    format_transcripts_for_welcome,
    merge_recent_transcript_entries,
)


@pytest.mark.parametrize(
    ("entry", "display", "welcome"),
    [
        (None, "", ""),
        ("legacy", "", ""),
        ([], "", ""),
        ({}, "[] ", "[]"),
        ({"text": None}, "[] ", "[]\nNone"),
        ({"text": False}, "[] ", "[]\nFalse"),
        ({"text": 0}, "[] ", "[]\n0"),
        ({"text": " \n\t "}, "[] ", "[]"),
        ({"text": "", "refined": True}, "[] ✨", "[]"),
        ({"text": "text", "refined": "yes"}, "[] ✨text", "[]\ntext"),
        ({"mode": "  "}, "[] (  ) ", "[]"),
        ({"mode": None, "language": False}, "[] (None) ", "[] (None False)"),
        (
            {"mode": " deepgram ", "language": " de "},
            "[] ( deepgram ) ",
            "[] (deepgram de)",
        ),
        ({"timestamp": None}, "[] ", "[]"),
        ({"timestamp": False}, "[] ", "[]"),
        ({"timestamp": 123}, "[123] ", "[123]"),
        (
            {"text": "\r\n  First  \r\n\r\nSecond\rThird\r\n"},
            "[]   First\n    \n    Second\n    Third",
            "[]\nFirst  \r\n\r\nSecond\rThird",
        ),
    ],
)
def test_transcript_formats_preserve_view_specific_rules(entry, display, welcome):
    assert format_transcript_entry_for_display(entry) == display
    assert format_transcript_entry_for_welcome(entry) == welcome
    assert format_transcript_entries_for_display([entry]) == (
        [display] if display else []
    )
    assert format_transcript_entries_for_welcome([entry]) == (
        [welcome] if welcome else []
    )
    assert format_transcripts_for_display([entry]) == (
        display or DISPLAY_TRANSCRIPTS_EMPTY_MESSAGE
    )
    assert format_transcripts_for_welcome([entry]) == (
        welcome or WELCOME_TRANSCRIPTS_EMPTY_MESSAGE
    )


@pytest.mark.parametrize("newest_first", [True, False])
@pytest.mark.parametrize(
    "formatter",
    [format_transcript_entries_for_display, format_transcript_entries_for_welcome],
)
def test_transcript_blocks_are_oldest_first_without_mutating_input(
    formatter, newest_first
):
    older = {"text": "older"}
    newer = {"text": "newer"}
    entries = (newer, None, older) if newest_first else (older, None, newer)

    blocks = formatter(entries, newest_first=newest_first)

    assert len(blocks) == 2
    assert blocks[0].endswith("older")
    assert blocks[1].endswith("newer")
    assert entries == ((newer, None, older) if newest_first else (older, None, newer))
    assert older == {"text": "older"}
    assert newer == {"text": "newer"}


@pytest.mark.parametrize("max_entries", [-1, 0, 1, 3, 10])
def test_merge_recent_transcripts_preserves_order_and_entry_identity(max_entries):
    older = {"text": "older"}
    newer = {"text": "newer"}
    newest = {"text": "newest"}
    previous = (older, None, newer)
    appended = ("legacy", newest)

    merged = merge_recent_transcript_entries(
        previous, appended, max_entries=max_entries
    )

    expected = [older, newer, newest][-max_entries:] if max_entries > 0 else []
    assert merged == expected
    assert all(actual is original for actual, original in zip(merged, expected))
    assert previous == ({"text": "older"}, None, {"text": "newer"})
    assert appended == ("legacy", {"text": "newest"})
