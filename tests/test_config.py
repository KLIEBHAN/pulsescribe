"""Tests für Konfigurations-Logik."""

import pytest

from transcribe import _should_use_streaming


class TestShouldUseStreaming:
    """Tests für _should_use_streaming() - Streaming-Flag-Logik."""

    def test_deepgram_default_streaming(self, mock_args, clean_env):
        """Deepgram nutzt standardmäßig Streaming."""
        args = mock_args(mode="deepgram")
        assert _should_use_streaming(args) is True

    @pytest.mark.parametrize("mode", ["openai", "local", "groq"])
    def test_non_deepgram_no_streaming(self, mock_args, mode, clean_env):
        """Andere Modi nutzen kein Streaming."""
        args = mock_args(mode=mode)
        assert _should_use_streaming(args) is False

    def test_no_streaming_flag(self, mock_args, clean_env):
        """--no-streaming deaktiviert Streaming."""
        args = mock_args(mode="deepgram", no_streaming=True)
        assert _should_use_streaming(args) is False

    @pytest.mark.parametrize(
        "env_value,expected",
        [
            ("false", False),
            ("FALSE", False),
            ("true", True),
            ("TRUE", True),
        ],
        ids=["false", "FALSE_upper", "true", "TRUE_upper"],
    )
    def test_env_streaming_values(
        self, mock_args, monkeypatch, clean_env, env_value, expected
    ):
        """WHISPER_GO_STREAMING ENV-Werte werden korrekt interpretiert."""
        monkeypatch.setenv("WHISPER_GO_STREAMING", env_value)
        args = mock_args(mode="deepgram")
        assert _should_use_streaming(args) is expected

    def test_cli_beats_env(self, mock_args, monkeypatch, clean_env):
        """--no-streaming schlägt ENV."""
        monkeypatch.setenv("WHISPER_GO_STREAMING", "true")
        args = mock_args(mode="deepgram", no_streaming=True)
        assert _should_use_streaming(args) is False
