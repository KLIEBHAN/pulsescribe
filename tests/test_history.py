"""Tests für die Transkript-Historie."""

import json

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
