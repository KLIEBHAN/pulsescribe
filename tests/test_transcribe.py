"""Tests für die zentrale transcribe() Funktion."""

from pathlib import Path
from unittest.mock import Mock, patch

import pytest


class TestTranscribeFunction:
    """Tests für transcribe() – Provider-Integration."""

    @pytest.fixture
    def audio_file(self, tmp_path):
        """Erstellt eine temporäre Audio-Datei."""
        audio = tmp_path / "test.wav"
        audio.write_bytes(b"fake audio data")
        return audio

    def test_uses_provider_module(self, audio_file):
        """transcribe() nutzt providers.get_provider()."""
        with patch("providers.get_provider") as mock_get_provider:
            mock_provider = Mock()
            mock_provider.transcribe.return_value = "transcribed text"
            mock_get_provider.return_value = mock_provider

            from transcribe import transcribe
            result = transcribe(audio_file, mode="openai", model="test-model")

        mock_get_provider.assert_called_once_with("openai")
        mock_provider.transcribe.assert_called_once()
        assert result == "transcribed text"

    def test_invalid_mode_raises(self, audio_file):
        """Ungültiger Modus wirft ValueError."""
        from transcribe import transcribe

        with pytest.raises(ValueError, match="Ungültiger Modus"):
            transcribe(audio_file, mode="invalid_mode")

    def test_valid_modes(self, audio_file):
        """Alle gültigen Modi werden akzeptiert."""
        from transcribe import DEFAULT_MODELS

        for mode in DEFAULT_MODELS.keys():
            with patch("providers.get_provider") as mock_get_provider:
                mock_provider = Mock()
                mock_provider.transcribe.return_value = "text"
                mock_get_provider.return_value = mock_provider

                from transcribe import transcribe
                transcribe(audio_file, mode=mode)

            mock_get_provider.assert_called_with(mode)

    def test_openai_passes_response_format(self, audio_file):
        """OpenAI-Provider erhält response_format Parameter."""
        with patch("providers.get_provider") as mock_get_provider:
            mock_provider = Mock()
            mock_provider.transcribe.return_value = "text"
            mock_get_provider.return_value = mock_provider

            from transcribe import transcribe
            transcribe(audio_file, mode="openai", response_format="json")

        # OpenAI sollte response_format bekommen
        call_kwargs = mock_provider.transcribe.call_args[1]
        assert call_kwargs.get("response_format") == "json"

    def test_deepgram_ignores_response_format(self, audio_file):
        """Deepgram ignoriert response_format (kein Parameter übergeben)."""
        with patch("providers.get_provider") as mock_get_provider:
            mock_provider = Mock()
            mock_provider.transcribe.return_value = "text"
            mock_get_provider.return_value = mock_provider

            from transcribe import transcribe
            transcribe(audio_file, mode="deepgram", response_format="json")

        # Deepgram sollte KEIN response_format bekommen
        call_kwargs = mock_provider.transcribe.call_args[1]
        assert "response_format" not in call_kwargs

    def test_passes_language_parameter(self, audio_file):
        """Sprach-Parameter wird an Provider weitergegeben."""
        with patch("providers.get_provider") as mock_get_provider:
            mock_provider = Mock()
            mock_provider.transcribe.return_value = "text"
            mock_get_provider.return_value = mock_provider

            from transcribe import transcribe
            transcribe(audio_file, mode="groq", language="de")

        call_kwargs = mock_provider.transcribe.call_args[1]
        assert call_kwargs.get("language") == "de"

    def test_passes_model_parameter(self, audio_file):
        """Modell-Parameter wird an Provider weitergegeben."""
        with patch("providers.get_provider") as mock_get_provider:
            mock_provider = Mock()
            mock_provider.transcribe.return_value = "text"
            mock_get_provider.return_value = mock_provider

            from transcribe import transcribe
            transcribe(audio_file, mode="local", model="turbo")

        call_kwargs = mock_provider.transcribe.call_args[1]
        assert call_kwargs.get("model") == "turbo"
