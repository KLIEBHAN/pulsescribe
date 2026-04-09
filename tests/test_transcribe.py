"""Tests für die zentrale transcribe() Funktion."""

from pathlib import Path
from unittest.mock import Mock, patch

import pytest
import typer


def test_default_models_match_provider_defaults_for_cli_modes() -> None:
    """transcribe.py soll für alle CLI-Modi dieselben Defaults wie providers nutzen."""
    from cli.types import TranscriptionMode
    from providers import DEFAULT_MODELS as provider_default_models
    from transcribe import DEFAULT_MODELS

    expected = {
        mode.value: provider_default_models[mode.value] for mode in TranscriptionMode
    }
    assert DEFAULT_MODELS == expected


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
        from providers.openai import OpenAIProvider

        with patch("providers.get_provider") as mock_get_provider:
            # Mock muss spec=OpenAIProvider haben für isinstance()-Check
            mock_provider = Mock(spec=OpenAIProvider)
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
        from providers.openai import OpenAIProvider
        from transcribe import DEFAULT_MODELS

        for mode in DEFAULT_MODELS.keys():
            with patch("providers.get_provider") as mock_get_provider:
                # OpenAI braucht spec für isinstance()-Check
                if mode == "openai":
                    mock_provider = Mock(spec=OpenAIProvider)
                else:
                    mock_provider = Mock()
                mock_provider.transcribe.return_value = "text"
                mock_get_provider.return_value = mock_provider

                from transcribe import transcribe

                transcribe(audio_file, mode=mode)

            mock_get_provider.assert_called_with(mode)

    def test_openai_passes_response_format(self, audio_file):
        """OpenAI-Provider erhält response_format Parameter."""
        from providers.openai import OpenAIProvider

        with patch("providers.get_provider") as mock_get_provider:
            mock_provider = Mock(spec=OpenAIProvider)
            mock_provider.transcribe.return_value = "text"
            mock_get_provider.return_value = mock_provider

            from transcribe import transcribe

            transcribe(audio_file, mode="openai", response_format="json")

        # OpenAI sollte response_format bekommen
        call_kwargs = mock_provider.transcribe.call_args[1]
        assert call_kwargs.get("response_format") == "json"

    def test_openai_accepts_provider_subclasses(self, audio_file):
        """OpenAI-Sonderlogik soll auch Subklassen nicht unnötig blockieren."""
        from providers.openai import OpenAIProvider

        class CustomOpenAIProvider(OpenAIProvider):
            pass

        with patch("providers.get_provider") as mock_get_provider:
            mock_provider = Mock(spec=CustomOpenAIProvider)
            mock_provider.transcribe.return_value = "text"
            mock_get_provider.return_value = mock_provider

            from transcribe import transcribe

            transcribe(audio_file, mode="openai", response_format="json")

        call_kwargs = mock_provider.transcribe.call_args[1]
        assert call_kwargs.get("response_format") == "json"

    def test_openai_requires_openai_provider_instance(self, audio_file):
        """Die OpenAI-Sonderbehandlung darf keinen falschen Provider schlucken."""
        with patch("providers.get_provider", return_value=Mock()):
            from transcribe import transcribe

            with pytest.raises(TypeError, match="Expected OpenAIProvider"):
                transcribe(audio_file, mode="openai")

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


class TestTranscribeHelpers:
    """Charakterisierungstests für interne CLI-Helfer."""

    def test_resolve_audio_source_returns_existing_file(self, tmp_path):
        from transcribe import _resolve_audio_source

        audio_file = tmp_path / "input.wav"
        audio_file.write_bytes(b"audio")

        assert _resolve_audio_source(audio_file, record=False) == (audio_file, None)

    def test_resolve_audio_source_returns_recorded_temp_file(self, tmp_path):
        from transcribe import _resolve_audio_source

        temp_audio = tmp_path / "recording.wav"
        temp_audio.write_bytes(b"audio")

        with patch("transcribe.record_audio", return_value=temp_audio):
            assert _resolve_audio_source(None, record=True) == (temp_audio, temp_audio)

    def test_resolve_audio_source_converts_recording_errors_to_cli_exit(self):
        from transcribe import _resolve_audio_source

        for side_effect in [ImportError("missing dependency"), ValueError("bad mic")]:
            with patch("transcribe.record_audio", side_effect=side_effect):
                with pytest.raises(typer.Exit) as exc_info:
                    _resolve_audio_source(None, record=True)

            assert exc_info.value.exit_code == 1

    def test_resolve_audio_source_rejects_missing_file(self, tmp_path):
        from transcribe import _resolve_audio_source

        with pytest.raises(typer.Exit) as exc_info:
            _resolve_audio_source(tmp_path / "missing.wav", record=False)

        assert exc_info.value.exit_code == 1

    def test_cleanup_temp_audio_file_ignores_os_errors(self, tmp_path):
        from transcribe import _cleanup_temp_audio_file

        temp_audio = tmp_path / "recording.wav"
        temp_audio.write_bytes(b"audio")

        with patch.object(Path, "unlink", side_effect=OSError("busy")):
            _cleanup_temp_audio_file(temp_audio)

        assert temp_audio.exists()

    def test_resolve_runtime_cli_options_uses_runtime_env_defaults(self, monkeypatch):
        from cli.types import RefineProvider, TranscriptionMode
        from transcribe import _resolve_runtime_cli_options

        monkeypatch.setenv("PULSESCRIBE_MODE", "groq")
        monkeypatch.setenv("PULSESCRIBE_MODEL", "whisper-large-v3")
        monkeypatch.setenv("PULSESCRIBE_LANGUAGE", "de")
        monkeypatch.setenv("PULSESCRIBE_REFINE", "true")
        monkeypatch.setenv("PULSESCRIBE_REFINE_MODEL", "llama-3.3")
        monkeypatch.setenv("PULSESCRIBE_REFINE_PROVIDER", "openai")

        resolved = _resolve_runtime_cli_options(
            mode=None,
            model=None,
            language=None,
            refine=False,
            no_refine=False,
            refine_model=None,
            refine_provider=None,
        )

        assert resolved.mode is TranscriptionMode.groq
        assert resolved.model == "whisper-large-v3"
        assert resolved.language == "de"
        assert resolved.refine is True
        assert resolved.refine_model == "llama-3.3"
        assert resolved.refine_provider is RefineProvider.openai

    def test_resolve_runtime_cli_options_preserves_explicit_cli_overrides(self, monkeypatch):
        from cli.types import RefineProvider, TranscriptionMode
        from transcribe import _resolve_runtime_cli_options

        monkeypatch.setenv("PULSESCRIBE_MODE", "deepgram")
        monkeypatch.setenv("PULSESCRIBE_MODEL", "env-model")
        monkeypatch.setenv("PULSESCRIBE_LANGUAGE", "en")
        monkeypatch.setenv("PULSESCRIBE_REFINE", "false")
        monkeypatch.setenv("PULSESCRIBE_REFINE_MODEL", "env-refine-model")
        monkeypatch.setenv("PULSESCRIBE_REFINE_PROVIDER", "groq")

        resolved = _resolve_runtime_cli_options(
            mode=TranscriptionMode.local,
            model="turbo",
            language="fr",
            refine=True,
            no_refine=False,
            refine_model="gpt-4.1-mini",
            refine_provider=RefineProvider.openai,
        )

        assert resolved.mode is TranscriptionMode.local
        assert resolved.model == "turbo"
        assert resolved.language == "fr"
        assert resolved.refine is True
        assert resolved.refine_model == "gpt-4.1-mini"
        assert resolved.refine_provider is RefineProvider.openai

    def test_resolve_runtime_cli_options_no_refine_beats_env_default(self, monkeypatch):
        from transcribe import _resolve_runtime_cli_options

        monkeypatch.setenv("PULSESCRIBE_REFINE", "true")

        resolved = _resolve_runtime_cli_options(
            mode=None,
            model=None,
            language=None,
            refine=False,
            no_refine=True,
            refine_model=None,
            refine_provider=None,
        )

        assert resolved.refine is False

    def test_resolve_runtime_cli_options_refine_flag_wins_even_if_no_refine_is_also_set(
        self, monkeypatch
    ):
        from transcribe import _resolve_runtime_cli_options

        monkeypatch.setenv("PULSESCRIBE_REFINE", "false")

        resolved = _resolve_runtime_cli_options(
            mode=None,
            model=None,
            language=None,
            refine=True,
            no_refine=True,
            refine_model=None,
            refine_provider=None,
        )

        assert resolved.refine is True

    def test_transcribe_with_cli_error_handling_maps_known_import_errors(self, tmp_path):
        from transcribe import _transcribe_with_cli_error_handling

        audio_file = tmp_path / "input.wav"
        audio_file.write_bytes(b"audio")

        cases = [
            (ImportError("No module named 'openai'"), "pip install openai"),
            (ImportError("deepgram SDK missing"), "pip install deepgram-sdk"),
            (ImportError("mlx_whisper import failed"), "pip install openai-whisper"),
        ]

        for side_effect, expected in cases:
            with (
                patch("transcribe.transcribe", side_effect=side_effect),
                patch("transcribe.error") as mock_error,
            ):
                with pytest.raises(typer.Exit) as exc_info:
                    _transcribe_with_cli_error_handling(
                        audio_file,
                        mode="openai",
                        model=None,
                        language=None,
                        response_format="text",
                    )

            assert exc_info.value.exit_code == 1
            assert expected in mock_error.call_args.args[0]

    def test_transcribe_with_cli_error_handling_preserves_runtime_errors(self, tmp_path):
        from transcribe import _transcribe_with_cli_error_handling

        audio_file = tmp_path / "input.wav"
        audio_file.write_bytes(b"audio")

        with (
            patch("transcribe.transcribe", side_effect=RuntimeError("boom")),
            patch("transcribe.error") as mock_error,
        ):
            with pytest.raises(typer.Exit) as exc_info:
                _transcribe_with_cli_error_handling(
                    audio_file,
                    mode="groq",
                    model="test-model",
                    language="de",
                    response_format="text",
                )

        assert exc_info.value.exit_code == 1
        assert mock_error.call_args.args == ("boom",)

    def test_maybe_refine_output_transcript_uses_refine_for_text(self):
        from cli.types import Context, RefineProvider, ResponseFormat
        from transcribe import _maybe_refine_output_transcript

        with patch("transcribe.maybe_refine_transcript", return_value="clean") as mock_refine:
            result = _maybe_refine_output_transcript(
                "raw transcript",
                response_format=ResponseFormat.text,
                refine=True,
                no_refine=False,
                refine_model="gpt-4.1-mini",
                refine_provider=RefineProvider.groq,
                context=Context.code,
            )

        assert result == "clean"
        assert mock_refine.call_args.args == ("raw transcript",)
        assert mock_refine.call_args.kwargs == {
            "refine": True,
            "no_refine": False,
            "refine_model": "gpt-4.1-mini",
            "refine_provider": "groq",
            "context": "code",
        }

    def test_maybe_refine_output_transcript_skips_non_text_output(self):
        from cli.types import ResponseFormat
        from transcribe import _maybe_refine_output_transcript

        with (
            patch("transcribe.maybe_refine_transcript") as mock_refine,
            patch("transcribe.log") as mock_log,
        ):
            result = _maybe_refine_output_transcript(
                '{"text":"raw"}',
                response_format=ResponseFormat.json,
                refine=True,
                no_refine=False,
                refine_model=None,
                refine_provider=None,
                context=None,
            )

        assert result == '{"text":"raw"}'
        mock_refine.assert_not_called()
        mock_log.assert_called_once()
