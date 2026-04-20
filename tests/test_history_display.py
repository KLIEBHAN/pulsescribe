from __future__ import annotations

from utils.history import (
    format_transcript_entries_for_welcome,
    format_transcripts_for_display,
    format_transcripts_for_welcome,
)


def test_format_transcripts_for_display_indents_multiline_entries() -> None:
    formatted = format_transcripts_for_display(
        [
            {
                "timestamp": "2026-03-24T10:00:00.000000",
                "text": "Erste Zeile\n\nZweiter Absatz\nDritte Zeile",
            }
        ]
    )

    assert formatted == (
        "[2026-03-24 10:00:00] Erste Zeile\n"
        "    \n"
        "    Zweiter Absatz\n"
        "    Dritte Zeile"
    )


def test_format_transcripts_for_display_trims_outer_blank_lines() -> None:
    formatted = format_transcripts_for_display(
        [
            {
                "timestamp": "2026-03-24T10:00:00.000000",
                "text": "\n\nErste Zeile\nZweite Zeile\n\n",
            }
        ]
    )

    assert formatted == (
        "[2026-03-24 10:00:00] Erste Zeile\n"
        "    Zweite Zeile"
    )


def test_format_transcripts_for_welcome_preserves_given_order() -> None:
    formatted = format_transcripts_for_welcome(
        [
            {
                "timestamp": "2026-03-24T10:00:00.000000",
                "text": "Erster Eintrag",
            },
            {
                "timestamp": "2026-03-24T10:01:00.000000",
                "text": "Zweiter Eintrag",
                "mode": "deepgram",
                "language": "de",
            },
        ],
        newest_first=False,
    )

    assert formatted == (
        "[2026-03-24 10:00:00]\n"
        "Erster Eintrag\n\n"
        "[2026-03-24 10:01:00] (deepgram de)\n"
        "Zweiter Eintrag"
    )


def test_format_transcripts_for_display_skips_invalid_entries_without_extra_spacing() -> None:
    formatted = format_transcripts_for_display(
        [
            {
                "timestamp": "2026-03-24T10:00:00.000000",
                "text": "Erster Eintrag",
            },
            "legacy-string-entry",
            {
                "timestamp": "2026-03-24T10:01:00.000000",
                "text": "Zweiter Eintrag",
            },
        ],
        newest_first=False,
    )

    assert formatted == (
        "[2026-03-24 10:00:00] Erster Eintrag\n\n"
        "[2026-03-24 10:01:00] Zweiter Eintrag"
    )


def test_format_transcript_entries_for_welcome_returns_blocks() -> None:
    blocks = format_transcript_entries_for_welcome(
        [
            {
                "timestamp": "2026-03-24T10:00:00.000000",
                "text": "Erster Eintrag",
            },
            {
                "timestamp": "2026-03-24T10:01:00.000000",
                "text": "Zweiter Eintrag",
            },
        ],
        newest_first=False,
    )

    assert blocks == [
        "[2026-03-24 10:00:00]\nErster Eintrag",
        "[2026-03-24 10:01:00]\nZweiter Eintrag",
    ]


def test_format_transcripts_for_display_string_splits_into_oldest_first_blocks() -> None:
    formatted = format_transcripts_for_display(
        [
            {
                "timestamp": "2026-03-24T10:01:00.000000",
                "text": "Zweiter Eintrag",
                "mode": "deepgram",
            },
            {
                "timestamp": "2026-03-24T10:00:00.000000",
                "text": "Erster Eintrag",
            },
        ],
        newest_first=True,
    )

    assert formatted.split("\n\n") == [
        "[2026-03-24 10:00:00] Erster Eintrag",
        "[2026-03-24 10:01:00] (deepgram) Zweiter Eintrag",
    ]
