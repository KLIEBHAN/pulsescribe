from utils.transcript_view_logic import (
    build_transcript_payload,
    should_append_transcript_delta_in_place,
)


def test_build_transcript_payload_uses_empty_text_without_blocks() -> None:
    text, oldest_first_entries, blocks, entry_count = build_transcript_payload(
        [],
        blocks=[],
        empty_text="No transcriptions yet.",
    )

    assert text == "No transcriptions yet."
    assert oldest_first_entries == []
    assert blocks == []
    assert entry_count == 0


def test_build_transcript_payload_keeps_oldest_first_cache() -> None:
    entries = [
        {"timestamp": "2026-03-24T10:01:00", "text": "Newest"},
        {"timestamp": "2026-03-24T10:00:00", "text": "Oldest"},
    ]

    text, oldest_first_entries, blocks, entry_count = build_transcript_payload(
        entries,
        blocks=["[2026-03-24 10:00:00]\nOldest", "[2026-03-24 10:01:00]\nNewest"],
        empty_text="No transcriptions yet.",
    )

    assert text == "[2026-03-24 10:00:00]\nOldest\n\n[2026-03-24 10:01:00]\nNewest"
    assert oldest_first_entries == list(reversed(entries))
    assert blocks == [
        "[2026-03-24 10:00:00]\nOldest",
        "[2026-03-24 10:01:00]\nNewest",
    ]
    assert entry_count == 2


def test_build_transcript_payload_ignores_whitespace_only_blocks() -> None:
    text, oldest_first_entries, blocks, entry_count = build_transcript_payload(
        [{"timestamp": "2026-03-24T10:01:00", "text": "Newest"}],
        blocks=["   ", "\n", "[2026-03-24 10:01:00]\nNewest"],
        empty_text="No transcriptions yet.",
    )

    assert text == "[2026-03-24 10:01:00]\nNewest"
    assert oldest_first_entries == [{"timestamp": "2026-03-24T10:01:00", "text": "Newest"}]
    assert blocks == ["[2026-03-24 10:01:00]\nNewest"]
    assert entry_count == 1


def test_should_append_transcript_delta_in_place_requires_existing_entries() -> None:
    assert (
        should_append_transcript_delta_in_place(
            [],
            entries_trimmed=False,
            last_text="placeholder",
            scroll_to_bottom=False,
            is_near_bottom=True,
        )
        is False
    )


def test_should_append_transcript_delta_in_place_accepts_existing_visible_entries() -> None:
    assert (
        should_append_transcript_delta_in_place(
            [{"timestamp": "2026-03-24T10:00:00", "text": "Alpha"}],
            entries_trimmed=False,
            last_text="[2026-03-24 10:00:00]\nAlpha",
            scroll_to_bottom=False,
            is_near_bottom=True,
        )
        is True
    )
