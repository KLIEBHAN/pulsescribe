"""Tests für Daten-Extraktion aus API-Responses."""

from unittest.mock import Mock

import pytest

from transcribe import _extract_message_content, _extract_transcript


class TestExtractMessageContent:
    """Tests für _extract_message_content() - OpenAI/OpenRouter Response parsing."""

    @pytest.mark.parametrize(
        "content,expected",
        [
            ("Hello", "Hello"),
            ("  trimmed  ", "trimmed"),
            (None, ""),
            ([], ""),
            ([{"text": "Part1"}, {"text": "Part2"}], "Part1Part2"),
            ([{"other": "ignored"}, {"text": "valid"}], "valid"),
            (["Hello", " ", "World"], "Hello World"),
            (["Prefix: ", {"text": "content"}], "Prefix: content"),
        ],
        ids=[
            "string",
            "trimmed",
            "none",
            "empty_list",
            "list_of_dicts",
            "missing_text_key",
            "list_of_strings",
            "mixed_list",
        ],
    )
    def test_extract_message_content(self, content, expected):
        """Verschiedene Content-Formate werden korrekt extrahiert."""
        assert _extract_message_content(content) == expected


class TestExtractTranscript:
    """Tests für _extract_transcript() - Deepgram Response parsing."""

    def test_valid_response(self, deepgram_response):
        """Gültige Deepgram-Response wird korrekt geparst."""
        result = deepgram_response("Hello World")
        assert _extract_transcript(result) == "Hello World"

    def test_no_channel(self):
        """Fehlender channel-Attribut gibt None zurück."""
        result = Mock(spec=[])  # Kein channel-Attribut
        assert _extract_transcript(result) is None

    def test_channel_none(self):
        """channel=None gibt None zurück."""
        result = Mock()
        result.channel = None
        assert _extract_transcript(result) is None

    def test_empty_alternatives(self):
        """Leere alternatives-Liste gibt None zurück."""
        result = Mock()
        result.channel = Mock()
        result.channel.alternatives = []
        assert _extract_transcript(result) is None

    def test_empty_transcript(self, deepgram_response):
        """Leerer transcript-String gibt None zurück."""
        result = deepgram_response("")
        assert _extract_transcript(result) is None

    def test_missing_transcript_attr(self):
        """Fehlendes transcript-Attribut gibt None zurück."""
        result = Mock()
        result.channel = Mock()
        alternative = Mock(spec=[])  # Kein transcript-Attribut
        result.channel.alternatives = [alternative]
        assert _extract_transcript(result) is None
