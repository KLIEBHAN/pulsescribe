"""Tests für Konfigurations-Logik."""

from unittest.mock import Mock

from transcribe import _should_use_streaming


class TestShouldUseStreaming:
    """Tests für _should_use_streaming() - Streaming-Flag-Logik."""

    def test_deepgram_default_streaming(self, monkeypatch, clean_env):
        """Deepgram nutzt standardmäßig Streaming."""
        args = Mock(mode="deepgram", no_streaming=False)

        assert _should_use_streaming(args) is True

    def test_non_deepgram_no_streaming(self, clean_env):
        """Andere Modi nutzen kein Streaming."""
        for mode in ["api", "local", "groq"]:
            args = Mock(mode=mode, no_streaming=False)
            assert _should_use_streaming(args) is False

    def test_no_streaming_flag(self, clean_env):
        """--no-streaming deaktiviert Streaming."""
        args = Mock(mode="deepgram", no_streaming=True)

        assert _should_use_streaming(args) is False

    def test_env_streaming_false(self, monkeypatch, clean_env):
        """WHISPER_GO_STREAMING=false deaktiviert Streaming."""
        monkeypatch.setenv("WHISPER_GO_STREAMING", "false")
        args = Mock(mode="deepgram", no_streaming=False)

        assert _should_use_streaming(args) is False

    def test_env_streaming_true(self, monkeypatch, clean_env):
        """WHISPER_GO_STREAMING=true aktiviert Streaming (explizit)."""
        monkeypatch.setenv("WHISPER_GO_STREAMING", "true")
        args = Mock(mode="deepgram", no_streaming=False)

        assert _should_use_streaming(args) is True

    def test_env_streaming_case_insensitive(self, monkeypatch, clean_env):
        """ENV-Wert ist case-insensitive."""
        monkeypatch.setenv("WHISPER_GO_STREAMING", "FALSE")
        args = Mock(mode="deepgram", no_streaming=False)

        assert _should_use_streaming(args) is False

    def test_cli_beats_env(self, monkeypatch, clean_env):
        """--no-streaming schlägt ENV."""
        monkeypatch.setenv("WHISPER_GO_STREAMING", "true")
        args = Mock(mode="deepgram", no_streaming=True)

        assert _should_use_streaming(args) is False
