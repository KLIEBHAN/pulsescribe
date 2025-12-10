"""Tests für Refine-Logik – Provider/Model-Auswahl und Fallbacks."""

from unittest.mock import Mock, patch

import pytest

from refine.llm import (
    refine_transcript,
    _get_refine_client,
    DEFAULT_REFINE_MODEL,
    DEFAULT_GROQ_REFINE_MODEL,
)
from transcribe import (
    maybe_refine_transcript,
    detect_context,
    _get_session_id,
    copy_to_clipboard,
)


# =============================================================================
# Tests: copy_to_clipboard
# =============================================================================


class TestCopyToClipboard:
    """Tests für copy_to_clipboard() – whisper_platform Wrapper."""

    def test_success(self):
        """Erfolgreicher Copy gibt True zurück."""
        mock_clipboard = Mock()
        mock_clipboard.copy.return_value = True
        with patch("whisper_platform.get_clipboard", return_value=mock_clipboard):
            result = copy_to_clipboard("test text")

        assert result is True
        mock_clipboard.copy.assert_called_once_with("test text")

    def test_empty_string(self):
        """Leerer String wird kopiert."""
        mock_clipboard = Mock()
        mock_clipboard.copy.return_value = True
        with patch("whisper_platform.get_clipboard", return_value=mock_clipboard):
            result = copy_to_clipboard("")

        assert result is True
        mock_clipboard.copy.assert_called_once_with("")

    def test_exception_returns_false(self):
        """Beliebiger Fehler gibt False zurück (fällt auf pyperclip zurück)."""
        # Wenn whisper_platform fehlschlägt, wird pyperclip als Fallback genutzt
        # Wir mocken beide um False sicherzustellen
        mock_pyperclip = Mock()
        mock_pyperclip.copy.side_effect = RuntimeError("Clipboard error")

        with patch("whisper_platform.get_clipboard", side_effect=ImportError):
            with patch.dict("sys.modules", {"pyperclip": mock_pyperclip}):
                result = copy_to_clipboard("test")

        assert result is False


# =============================================================================
# Tests: _get_refine_client
# =============================================================================


class TestGetRefineClient:
    """Tests für _get_refine_client() – Client-Erstellung pro Provider."""

    def test_openai_default(self):
        """OpenAI-Provider nutzt OpenAI-Client."""
        mock_openai_class = Mock()
        # OpenAI wird innerhalb der Funktion importiert
        with patch("openai.OpenAI", mock_openai_class):
            client = _get_refine_client("openai")

        mock_openai_class.assert_called_once_with()
        assert client == mock_openai_class.return_value

    def test_openrouter_with_api_key(self, monkeypatch):
        """OpenRouter-Provider nutzt OpenAI-Client mit custom base_url."""
        monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
        mock_openai_class = Mock()
        with patch("openai.OpenAI", mock_openai_class):
            client = _get_refine_client("openrouter")

        mock_openai_class.assert_called_once_with(
            base_url="https://openrouter.ai/api/v1",
            api_key="test-key",
        )

    def test_openrouter_missing_api_key(self, monkeypatch):
        """OpenRouter ohne API-Key wirft ValueError."""
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

        with pytest.raises(ValueError, match="OPENROUTER_API_KEY nicht gesetzt"):
            _get_refine_client("openrouter")

    def test_groq_uses_groq_client(self, monkeypatch):
        """Groq-Provider nutzt Groq-Client."""
        mock_groq_client = Mock()
        monkeypatch.setattr("refine.llm._get_groq_client", lambda: mock_groq_client)

        client = _get_refine_client("groq")

        assert client == mock_groq_client


# =============================================================================
# Tests: Provider und Model Auswahl (Inline in refine_transcript)
# =============================================================================


class TestRefineProviderSelection:
    """Tests für Provider-Auswahl in refine_transcript()."""

    def test_cli_provider_overrides_env(self, monkeypatch):
        """CLI-Parameter überschreibt ENV."""
        # from transcribe import refine_transcript (removed)

        monkeypatch.setenv("WHISPER_GO_REFINE_PROVIDER", "groq")

        with patch("refine.llm._get_refine_client") as mock_client:
            mock_client.return_value.responses.create.return_value = Mock(
                output_text="refined"
            )

            refine_transcript("test", provider="openai", model="gpt-5-nano")

        # CLI "openai" sollte ENV "groq" überschreiben
        mock_client.assert_called_with("openai")

    def test_env_provider_used_when_no_cli(self, monkeypatch):
        """ENV-Provider wird genutzt wenn kein CLI-Argument."""
        # from transcribe import refine_transcript (removed)

        monkeypatch.setenv("WHISPER_GO_REFINE_PROVIDER", "groq")

        with patch("refine.llm._get_refine_client") as mock_client:
            mock_client.return_value.chat.completions.create.return_value = Mock(
                choices=[Mock(message=Mock(content="refined"))]
            )

            refine_transcript("test", model="llama-3.3-70b-versatile")

        mock_client.assert_called_with("groq")

    def test_default_provider_is_openai(self, monkeypatch, clean_env):
        """Default-Provider ist openai."""
        # from transcribe import refine_transcript (removed)

        with patch("refine.llm._get_refine_client") as mock_client:
            mock_client.return_value.responses.create.return_value = Mock(
                output_text="refined"
            )

            refine_transcript("test", model="gpt-5-nano")

        mock_client.assert_called_with("openai")


class TestRefineModelSelection:
    """Tests für Model-Auswahl in refine_transcript()."""

    def test_cli_model_overrides_all(self, monkeypatch):
        """CLI-Model überschreibt alles."""
        # from transcribe import refine_transcript (removed)

        monkeypatch.setenv("WHISPER_GO_REFINE_MODEL", "env-model")
        monkeypatch.setenv("WHISPER_GO_REFINE_PROVIDER", "openai")

        with patch("refine.llm._get_refine_client") as mock_client:
            mock_client.return_value.responses.create.return_value = Mock(
                output_text="refined"
            )

            refine_transcript("test", model="cli-model")

        # Prüfen der create-Aufrufe
        call_kwargs = mock_client.return_value.responses.create.call_args
        assert call_kwargs[1]["model"] == "cli-model"

    def test_env_model_used_when_no_cli(self, monkeypatch):
        """ENV-Model wird genutzt wenn kein CLI-Argument."""
        # from transcribe import refine_transcript (removed)

        monkeypatch.setenv("WHISPER_GO_REFINE_MODEL", "env-model")
        monkeypatch.setenv("WHISPER_GO_REFINE_PROVIDER", "openai")

        with patch("refine.llm._get_refine_client") as mock_client:
            mock_client.return_value.responses.create.return_value = Mock(
                output_text="refined"
            )

            refine_transcript("test")

        call_kwargs = mock_client.return_value.responses.create.call_args
        assert call_kwargs[1]["model"] == "env-model"

    def test_groq_default_model(self, monkeypatch, clean_env):
        """Groq-Provider nutzt llama-3.3 als Default."""
        # from transcribe import refine_transcript (removed)

        with patch("refine.llm._get_refine_client") as mock_client:
            mock_client.return_value.chat.completions.create.return_value = Mock(
                choices=[Mock(message=Mock(content="refined"))]
            )

            refine_transcript("test", provider="groq")

        call_kwargs = mock_client.return_value.chat.completions.create.call_args
        assert call_kwargs[1]["model"] == DEFAULT_GROQ_REFINE_MODEL

    def test_openai_default_model(self, monkeypatch, clean_env):
        """OpenAI-Provider nutzt gpt-5-nano als Default."""
        # from transcribe import refine_transcript (removed)

        with patch("refine.llm._get_refine_client") as mock_client:
            mock_client.return_value.responses.create.return_value = Mock(
                output_text="refined"
            )

            refine_transcript("test")

        call_kwargs = mock_client.return_value.responses.create.call_args
        assert call_kwargs[1]["model"] == DEFAULT_REFINE_MODEL


class TestRefineEdgeCases:
    """Tests für Edge-Cases in refine_transcript()."""

    def test_empty_transcript_returns_unchanged(self, clean_env):
        """Leeres Transkript wird nicht verarbeitet."""
        # from transcribe import refine_transcript (removed)

        with patch("refine.llm._get_refine_client") as mock_client:
            result = refine_transcript("")

        # Client sollte nie aufgerufen werden
        mock_client.assert_not_called()
        assert result == ""

    def test_whitespace_only_returns_unchanged(self, clean_env):
        """Nur Whitespace wird nicht verarbeitet."""
        # from transcribe import refine_transcript (removed)

        with patch("refine.llm._get_refine_client") as mock_client:
            result = refine_transcript("   \n\t  ")

        mock_client.assert_not_called()
        assert result == "   \n\t  "

    def test_none_transcript_returns_unchanged(self, clean_env):
        """None-Transkript gibt Falsy zurück."""
        # from transcribe import refine_transcript (removed)

        with patch("refine.llm._get_refine_client") as mock_client:
            result = refine_transcript(None)  # type: ignore

        mock_client.assert_not_called()
        assert result is None
