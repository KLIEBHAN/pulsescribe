"""Tests für Daten-Extraktion aus API-Responses."""

from unittest.mock import Mock

from transcribe import _extract_message_content, _extract_transcript


class TestExtractMessageContent:
    """Tests für _extract_message_content() - OpenAI/OpenRouter Response parsing."""

    def test_string_input(self):
        """String wird direkt zurückgegeben (getrimmt)."""
        assert _extract_message_content("Hello") == "Hello"
        assert _extract_message_content("  trimmed  ") == "trimmed"

    def test_none_input(self):
        """None wird zu leerem String."""
        assert _extract_message_content(None) == ""

    def test_list_of_dicts_with_text(self):
        """Liste von Dicts mit 'text'-Keys wird konkateniert."""
        content = [{"text": "Part1"}, {"text": "Part2"}]
        assert _extract_message_content(content) == "Part1Part2"

    def test_list_of_dicts_missing_text(self):
        """Dicts ohne 'text'-Key werden ignoriert."""
        content = [{"other": "ignored"}, {"text": "valid"}]
        assert _extract_message_content(content) == "valid"

    def test_list_with_strings(self):
        """Liste mit Strings wird konkateniert."""
        content = ["Hello", " ", "World"]
        assert _extract_message_content(content) == "Hello World"

    def test_mixed_list(self):
        """Gemischte Liste (Strings + Dicts) wird verarbeitet."""
        content = ["Prefix: ", {"text": "content"}]
        assert _extract_message_content(content) == "Prefix: content"

    def test_empty_list(self):
        """Leere Liste wird zu leerem String."""
        assert _extract_message_content([]) == ""


class TestExtractTranscript:
    """Tests für _extract_transcript() - Deepgram Response parsing."""

    def test_valid_response(self):
        """Gültige Deepgram-Response wird korrekt geparst."""
        result = Mock()
        result.channel = Mock()
        result.channel.alternatives = [Mock(transcript="Hello World")]

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

    def test_empty_transcript(self):
        """Leerer transcript-String gibt None zurück."""
        result = Mock()
        result.channel = Mock()
        result.channel.alternatives = [Mock(transcript="")]

        assert _extract_transcript(result) is None

    def test_missing_transcript_attr(self):
        """Fehlendes transcript-Attribut gibt None zurück."""
        result = Mock()
        result.channel = Mock()
        alternative = Mock(spec=[])  # Kein transcript-Attribut
        result.channel.alternatives = [alternative]

        # getattr mit Default "" → or None → None
        assert _extract_transcript(result) is None
