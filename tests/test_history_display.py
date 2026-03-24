from __future__ import annotations

from utils.history import format_transcripts_for_display


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
