"""Tests für die Transkript-Historie."""

import json
import logging

import pytest


@pytest.fixture
def history_file(tmp_path, monkeypatch):
    """Temporäre History-Datei für Tests."""
    history_path = tmp_path / "history.jsonl"
    monkeypatch.setattr("utils.history.HISTORY_FILE", history_path)
    return history_path


class TestSaveTranscript:
    """Tests für save_transcript()."""

    def test_save_basic_transcript(self, history_file):
        """Speichert einfaches Transkript."""
        from utils.history import save_transcript

        result = save_transcript("Hello World")

        assert result is True
        assert history_file.exists()

        content = history_file.read_text()
        entry = json.loads(content.strip())

        assert entry["text"] == "Hello World"
        assert "timestamp" in entry

    def test_save_with_metadata(self, history_file):
        """Speichert Transkript mit Metadaten."""
        from utils.history import save_transcript

        result = save_transcript(
            "Test text",
            mode="deepgram",
            language="de",
            refined=True,
            app_context="Slack",
        )

        assert result is True

        content = history_file.read_text()
        entry = json.loads(content.strip())

        assert entry["text"] == "Test text"
        assert entry["mode"] == "deepgram"
        assert entry["language"] == "de"
        assert entry["refined"] is True
        assert entry["app"] == "Slack"

    def test_save_empty_text_returns_false(self, history_file):
        """Leerer Text wird nicht gespeichert."""
        from utils.history import save_transcript

        assert save_transcript("") is False
        assert save_transcript("   ") is False
        assert not history_file.exists()

    def test_save_multiple_transcripts(self, history_file):
        """Mehrere Transkripte werden angehängt."""
        from utils.history import save_transcript

        save_transcript("First")
        save_transcript("Second")
        save_transcript("Third")

        lines = history_file.read_text().strip().split("\n")
        assert len(lines) == 3

        texts = [json.loads(line)["text"] for line in lines]
        assert texts == ["First", "Second", "Third"]

    def test_save_logs_redacted_summary(self, history_file, caplog):
        """History-Logs dürfen keinen Transkriptinhalt enthalten."""
        from utils.history import save_transcript

        with caplog.at_level(logging.DEBUG, logger="utils.history"):
            save_transcript("Highly sensitive transcript")

        messages = " ".join(record.getMessage() for record in caplog.records)
        assert "Transcript saved to history: <redacted 27 chars>" in messages
        assert "Highly sensitive transcript" not in messages

    def test_save_trims_text_and_omits_empty_optional_metadata(self, history_file):
        """Leere Metadaten sollen nicht als Keys in der History landen."""
        from utils.history import save_transcript

        assert save_transcript("  Hello World  ", mode="", language=None, app_context="") is True

        entry = json.loads(history_file.read_text(encoding="utf-8").strip())

        assert entry == {
            "timestamp": entry["timestamp"],
            "text": "Hello World",
        }


class TestGetRecentTranscripts:
    """Tests für get_recent_transcripts()."""

    def test_get_empty_history(self, history_file):
        """Leere Historie gibt leere Liste zurück."""
        from utils.history import get_recent_transcripts

        result = get_recent_transcripts()
        assert result == []

    def test_get_recent_returns_newest_first(self, history_file):
        """Neueste Einträge zuerst."""
        from utils.history import get_recent_transcripts, save_transcript

        save_transcript("First")
        save_transcript("Second")
        save_transcript("Third")

        result = get_recent_transcripts(count=3)

        assert len(result) == 3
        assert result[0]["text"] == "Third"
        assert result[1]["text"] == "Second"
        assert result[2]["text"] == "First"

    def test_get_limited_count(self, history_file):
        """Begrenzte Anzahl von Einträgen."""
        from utils.history import get_recent_transcripts, save_transcript

        for i in range(10):
            save_transcript(f"Entry {i}")

        result = get_recent_transcripts(count=3)

        assert len(result) == 3
        assert result[0]["text"] == "Entry 9"

    def test_get_non_positive_count_returns_empty(self, history_file):
        """Nicht-positive count-Werte liefern keine Einträge."""
        from utils.history import get_recent_transcripts, save_transcript

        save_transcript("Entry")

        assert get_recent_transcripts(count=0) == []
        assert get_recent_transcripts(count=-5) == []

    def test_get_recent_prefers_tail_read_over_full_read(self, history_file, monkeypatch):
        """Normale Reads sollen ohne Full-File read_text auskommen."""
        from utils.history import get_recent_transcripts, save_transcript

        save_transcript("First")
        save_transcript("Second")
        save_transcript("Third")

        def fail_read_text(self, *args, **kwargs):
            raise AssertionError("full read_text should not be used for small tail reads")

        monkeypatch.setattr("pathlib.Path.read_text", fail_read_text)
        result = get_recent_transcripts(count=2)

        assert [entry["text"] for entry in result] == ["Third", "Second"]

    def test_get_recent_falls_back_to_full_read_when_tail_insufficient(
        self, history_file, monkeypatch
    ):
        """Fallback auf Full-Read stellt Korrektheit bei kleinem Tail-Scan sicher."""
        from utils.history import get_recent_transcripts, save_transcript

        save_transcript("First")
        save_transcript("Second")

        monkeypatch.setattr("utils.history._RECENT_SCAN_BYTES_MIN", 10)
        monkeypatch.setattr("utils.history._RECENT_SCAN_BYTES_MAX", 10)

        result = get_recent_transcripts(count=1)
        assert [entry["text"] for entry in result] == ["Second"]

    def test_get_recent_ignores_non_object_json_lines(self, history_file):
        """Kaputte oder inkompatible JSON-Zeilen dürfen die Historie nicht brechen."""
        from utils.history import get_recent_transcripts

        history_file.write_text(
            '\n'.join(
                [
                    '{"timestamp":"2026-03-03T10:00:00","text":"First"}',
                    '"legacy-string-entry"',
                    '["unexpected", "array"]',
                    '{"timestamp":"2026-03-03T10:01:00","text":"Second"}',
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        result = get_recent_transcripts(count=5)

        assert [entry["text"] for entry in result] == ["Second", "First"]

    def test_read_transcripts_from_offset_returns_valid_entries_in_file_order(
        self, history_file
    ):
        """Append-only Delta-Reads sollen nur neue, gültige JSONL-Einträge liefern."""
        from utils.history import read_transcripts_from_offset

        original = (
            '{"timestamp":"2026-03-03T10:00:00","text":"First"}\n'
            '{"timestamp":"2026-03-03T10:00:01","text":"Second"}\n'
        )
        appended = (
            '{"timestamp":"2026-03-03T10:00:02","text":"Third"}\n'
            '"legacy-string-entry"\n'
            '{"timestamp":"2026-03-03T10:00:03","text":"Fourth"}\n'
        )
        history_file.write_text(original + appended, encoding="utf-8")

        entries = read_transcripts_from_offset(len(original.encode("utf-8")))

        assert [entry["text"] for entry in entries] == ["Third", "Fourth"]

    def test_merge_recent_transcript_entries_keeps_visible_window(self):
        """Merged transcript windows sollen alte Einträge vorne verwerfen."""
        from utils.history import merge_recent_transcript_entries

        previous_entries = [
            {"timestamp": "2026-03-03T10:00:00", "text": "First"},
            {"timestamp": "2026-03-03T10:00:01", "text": "Second"},
        ]
        appended_entries = [
            {"timestamp": "2026-03-03T10:00:02", "text": "Third"},
            {"timestamp": "2026-03-03T10:00:03", "text": "Fourth"},
        ]

        merged = merge_recent_transcript_entries(
            previous_entries,
            appended_entries,
            max_entries=3,
        )

        assert [entry["text"] for entry in merged] == ["Second", "Third", "Fourth"]

    def test_merge_recent_transcript_entries_ignores_invalid_payloads(self):
        """Nur Dict-Einträge dürfen im sichtbaren Transcript-Fenster landen."""
        from utils.history import merge_recent_transcript_entries

        merged = merge_recent_transcript_entries(
            [
                {"timestamp": "2026-03-03T10:00:00", "text": "First"},
                "legacy-string-entry",
                None,
                {"timestamp": "2026-03-03T10:00:01", "text": "Second"},
            ],
            [
                "invalid-appended-entry",
                {"timestamp": "2026-03-03T10:00:02", "text": "Third"},
            ],
            max_entries=3,
        )

        assert [entry["text"] for entry in merged] == ["First", "Second", "Third"]


class TestFormatTranscriptsForDisplay:
    """Tests für format_transcripts_for_display()."""

    def test_empty_entries_message(self):
        from utils.history import format_transcripts_for_display

        assert format_transcripts_for_display([]) == "No transcripts yet."

    def test_format_transcript_entry_for_display_indents_multiline_text(self):
        from utils.history import format_transcript_entry_for_display

        formatted = format_transcript_entry_for_display(
            {
                "timestamp": "2026-03-03T10:01:30.000000",
                "text": "First line\nSecond line\n\nFourth line",
                "mode": "deepgram",
                "refined": True,
            }
        )

        assert formatted == (
            "[2026-03-03 10:01:30] (deepgram) ✨First line\n"
            "    Second line\n"
            "    \n"
            "    Fourth line"
        )

    def test_formats_entries_oldest_first_with_metadata(self):
        from utils.history import format_transcripts_for_display

        entries = [
            {
                "timestamp": "2026-03-03T10:01:30.000000",
                "text": "Neuester Eintrag",
                "mode": "deepgram",
                "refined": True,
            },
            {
                "timestamp": "2026-03-03T10:00:00.000000",
                "text": "Aelterer Eintrag",
            },
        ]

        formatted = format_transcripts_for_display(entries)
        assert "[2026-03-03 10:00:00] Aelterer Eintrag" in formatted
        assert "[2026-03-03 10:01:30] (deepgram) ✨Neuester Eintrag" in formatted
        first, second = formatted.split("\n\n")
        assert "Aelterer Eintrag" in first
        assert "Neuester Eintrag" in second

    def test_ignores_non_dict_entries(self):
        from utils.history import format_transcripts_for_display

        formatted = format_transcripts_for_display(
            [
                {"timestamp": "2026-03-03T10:01:30.000000", "text": "Neuester Eintrag"},
                "legacy-string-entry",
            ]
        )

        assert "Neuester Eintrag" in formatted
        assert "legacy-string-entry" not in formatted


class TestFormatTranscriptsForWelcome:
    """Tests für format_transcripts_for_welcome()."""

    def test_format_transcript_entry_for_welcome_returns_header_without_text(self):
        from utils.history import format_transcript_entry_for_welcome

        formatted = format_transcript_entry_for_welcome(
            {
                "timestamp": "2026-03-03T10:01:30.000000",
                "text": "   ",
                "mode": "deepgram",
                "language": "de",
            }
        )

        assert formatted == "[2026-03-03 10:01:30] (deepgram de)"

    def test_formats_entries_with_optional_metadata(self):
        from utils.history import format_transcripts_for_welcome

        entries = [
            {
                "timestamp": "2026-03-03T10:01:30.000000",
                "text": "Neuester Eintrag",
                "mode": "deepgram",
                "language": "de",
            },
            {
                "timestamp": "2026-03-03T10:00:00.000000",
                "text": "Aelterer Eintrag",
            },
        ]

        formatted = format_transcripts_for_welcome(entries)
        first, second = formatted.split("\n\n")

        assert first == "[2026-03-03 10:00:00]\nAelterer Eintrag"
        assert second == "[2026-03-03 10:01:30] (deepgram de)\nNeuester Eintrag"

    def test_ignores_non_dict_entries_and_can_preserve_newest_first_order(self):
        from utils.history import format_transcripts_for_welcome

        formatted = format_transcripts_for_welcome(
            [
                {"timestamp": "2026-03-03T10:00:00.000000", "text": "First"},
                "legacy-string-entry",
                {"timestamp": "2026-03-03T10:01:30.000000", "text": "Second"},
            ],
            newest_first=False,
        )

        assert formatted == "[2026-03-03 10:00:00]\nFirst\n\n[2026-03-03 10:01:30]\nSecond"
        assert "legacy-string-entry" not in formatted


class TestClearHistory:
    """Tests für clear_history()."""

    def test_clear_existing_history(self, history_file):
        """Löscht existierende Historie."""
        from utils.history import clear_history, save_transcript

        save_transcript("Test")
        assert history_file.exists()

        result = clear_history()

        assert result is True
        assert not history_file.exists()

    def test_clear_nonexistent_history(self, history_file):
        """Kein Fehler bei nicht existierender Historie."""
        from utils.history import clear_history

        result = clear_history()
        assert result is True


class TestRotation:
    """Tests für automatische Rotation."""

    def test_select_recent_lines_within_bytes_keeps_oversized_newest_entry(self):
        """Auch ein einzelner zu großer neuester Eintrag darf nicht verworfen werden."""
        from utils.history import _select_recent_lines_within_bytes

        lines = [
            json.dumps({"timestamp": "2026-03-25T10:00:00", "text": "old"}),
            json.dumps({"timestamp": "2026-03-25T10:00:01", "text": "x" * 200}),
        ]

        kept = _select_recent_lines_within_bytes(lines, max_size_bytes=32)

        assert kept == [lines[-1]]

    def test_rotation_when_file_too_large(self, history_file, monkeypatch):
        """Rotation bei zu großer Datei."""
        from utils.history import save_transcript

        # Set small max size for testing
        monkeypatch.setattr("utils.history.MAX_HISTORY_SIZE_MB", 0.0001)

        # Create entries that exceed the limit
        for i in range(100):
            save_transcript(f"Entry {i} with some extra text to make it larger")

        # File should have been rotated (fewer entries than written)
        lines = history_file.read_text().strip().split("\n")
        assert len(lines) < 100

    def test_rotation_trims_to_size_budget_with_few_large_entries(
        self, history_file, monkeypatch
    ):
        """Rotation muss auch bei wenigen großen Einträgen unter das Größenlimit kommen."""
        from utils.history import _rotate_if_needed

        monkeypatch.setattr("utils.history.MAX_HISTORY_SIZE_MB", 0.7)
        payload = "x" * 300_000
        limit_bytes = int(0.7 * 1024 * 1024)

        entries = [
            json.dumps(
                {
                    "timestamp": f"2026-03-24T12:00:{idx:02d}",
                    "text": f"{idx}:{payload}",
                },
                ensure_ascii=False,
            )
            for idx in range(6)
        ]
        history_file.write_text("\n".join(entries) + "\n", encoding="utf-8")

        _rotate_if_needed()

        remaining_lines = history_file.read_text(encoding="utf-8").splitlines()
        remaining_prefixes = [json.loads(line)["text"].split(":", 1)[0] for line in remaining_lines]

        assert history_file.stat().st_size <= limit_bytes
        assert remaining_prefixes == ["4", "5"]

    def test_save_transcript_rotates_immediately_after_large_append(
        self, history_file, monkeypatch
    ):
        """Ein großer neuer Save darf die Datei nicht bis zum nächsten Save oversized lassen."""
        from utils.history import save_transcript

        monkeypatch.setattr("utils.history.MAX_HISTORY_SIZE_MB", 0.001)
        limit_bytes = int(0.001 * 1024 * 1024)

        assert save_transcript("old-" + ("a" * 500)) is True
        assert history_file.stat().st_size < limit_bytes

        assert save_transcript("new-" + ("b" * 500)) is True

        lines = history_file.read_text(encoding="utf-8").splitlines()
        texts = [json.loads(line)["text"] for line in lines]

        assert history_file.stat().st_size <= limit_bytes
        assert texts[-1] == "new-" + ("b" * 500)
        assert len(texts) == 1

    def test_rotation_preserves_data_on_write_failure(
        self, history_file, monkeypatch
    ):
        """History bleibt intakt wenn atomarer Write während Rotation fehlschlägt."""
        from unittest.mock import patch

        from utils.history import _rotate_if_needed

        monkeypatch.setattr("utils.history.MAX_HISTORY_SIZE_MB", 0.0001)

        # Mehrere Einträge schreiben die über dem Limit liegen
        entries = [
            json.dumps({"timestamp": f"2026-03-25T10:00:{i:02d}", "text": f"Entry {i}"})
            for i in range(20)
        ]
        original_content = "\n".join(entries) + "\n"
        history_file.write_text(original_content, encoding="utf-8")

        # Simuliere einen Fehler beim atomaren Write (z.B. Disk voll)
        with patch("utils.history._write_text_atomic", side_effect=OSError("disk full")):
            _rotate_if_needed()

        # History muss INTAKT sein (keine Truncation, kein Datenverlust)
        assert history_file.read_text(encoding="utf-8") == original_content
