"""Tests für Refine-Logik – Provider/Model-Auswahl und Fallbacks."""

from types import SimpleNamespace
from unittest.mock import Mock, call, patch

import pytest

from refine.llm import (
    refine_transcript,
    _extract_message_content,
    _get_refine_client,
    DEFAULT_REFINE_MODEL,
    DEFAULT_GEMINI_REFINE_MODEL,
)
from transcribe import (
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

    @pytest.fixture(autouse=True)
    def reset_client_singletons(self):
        """Setzt Client-Singletons vor jedem Test zurück."""
        import refine.llm

        refine.llm._clients.clear()
        refine.llm._signatures.clear()
        yield
        refine.llm._clients.clear()
        refine.llm._signatures.clear()

    def test_openai_default(self, monkeypatch):
        """OpenAI-Provider nutzt OpenAI-Client."""
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        mock_openai_class = Mock()
        # OpenAI wird innerhalb der Funktion importiert
        with patch("openai.OpenAI", mock_openai_class):
            client = _get_refine_client("openai")

        mock_openai_class.assert_called_once_with(api_key="test-key")
        assert client == mock_openai_class.return_value

    def test_openai_missing_api_key(self, monkeypatch):
        """OpenAI ohne API-Key wirft ValueError statt einen alten Client zu reusen."""
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)

        with pytest.raises(ValueError, match="OPENAI_API_KEY nicht gesetzt"):
            _get_refine_client("openai")

    def test_openrouter_with_api_key(self, monkeypatch):
        """OpenRouter-Provider nutzt OpenAI-Client mit custom base_url."""
        monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
        mock_openai_class = Mock()
        with patch("openai.OpenAI", mock_openai_class):
            _get_refine_client("openrouter")

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

    def test_gemini_missing_api_key(self, monkeypatch):
        """Gemini ohne API-Key wirft ValueError (fail-fast statt kryptischer SDK-Fehler)."""
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)

        # Mock google.genai module to allow import
        mock_genai = Mock()
        with patch.dict("sys.modules", {"google": Mock(genai=mock_genai), "google.genai": mock_genai}):
            with pytest.raises(ValueError, match="GEMINI_API_KEY nicht gesetzt"):
                _get_refine_client("gemini")

    def test_gemini_routing(self, monkeypatch):
        """_get_refine_client("gemini") ruft _get_gemini_client() auf."""
        mock_gemini_client = Mock()
        monkeypatch.setattr("refine.llm._get_gemini_client", lambda: mock_gemini_client)

        client = _get_refine_client("gemini")

        assert client == mock_gemini_client

    def test_unknown_provider_raises_clear_value_error(self):
        """Ungültige Provider-Namen sollen nicht still auf OpenAI fallen."""
        with pytest.raises(ValueError, match="Unbekannter Refine-Provider 'groqq'"):
            _get_refine_client("groqq")

    def test_groq_client_rebuilds_after_api_key_change(self, monkeypatch):
        """Bei Key-Wechsel wird der Groq-Client neu erstellt statt stale gecached zu bleiben."""
        monkeypatch.setenv("GROQ_API_KEY", "first-key")
        first_client = Mock(name="first-client")
        second_client = Mock(name="second-client")
        mock_groq_class = Mock(side_effect=[first_client, second_client])

        with patch("groq.Groq", mock_groq_class):
            client_one = _get_refine_client("groq")
            monkeypatch.setenv("GROQ_API_KEY", "second-key")
            client_two = _get_refine_client("groq")

        assert client_one is first_client
        assert client_two is second_client
        assert mock_groq_class.call_args_list == [
            call(api_key="first-key"),
            call(api_key="second-key"),
        ]

    def test_openai_cached_client_invalidated_when_api_key_removed(self, monkeypatch):
        """Ein entfernter OpenAI-Key darf nicht durch einen alten Singleton kaschiert werden."""
        monkeypatch.setenv("OPENAI_API_KEY", "active-key")
        mock_openai_class = Mock(return_value=Mock(name="openai-client"))

        with patch("openai.OpenAI", mock_openai_class):
            _get_refine_client("openai")
            monkeypatch.delenv("OPENAI_API_KEY", raising=False)

            with pytest.raises(ValueError, match="OPENAI_API_KEY nicht gesetzt"):
                _get_refine_client("openai")


# =============================================================================
# Tests: Provider und Model Auswahl (Inline in refine_transcript)
# =============================================================================


class TestRefineProviderSelection:
    """Tests für Provider-Auswahl in refine_transcript()."""

    def test_cli_provider_overrides_env(self, monkeypatch):
        """CLI-Parameter überschreibt ENV."""
        # from transcribe import refine_transcript (removed)

        monkeypatch.setenv("PULSESCRIBE_REFINE_PROVIDER", "groq")

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

        monkeypatch.setenv("PULSESCRIBE_REFINE_PROVIDER", "groq")

        with patch("refine.llm._get_refine_client") as mock_client:
            mock_client.return_value.chat.completions.create.return_value = Mock(
                choices=[Mock(message=Mock(content="refined"))]
            )

            refine_transcript("test", model="openai/gpt-oss-120b")

        mock_client.assert_called_with("groq")

    def test_default_provider_is_groq(self, monkeypatch, clean_env):
        """Default-Provider ist groq."""
        # from transcribe import refine_transcript (removed)

        with patch("refine.llm._get_refine_client") as mock_client:
            mock_client.return_value.chat.completions.create.return_value = Mock(
                choices=[Mock(message=Mock(content="refined"))]
            )

            refine_transcript("test", model="openai/gpt-oss-120b")

        mock_client.assert_called_with("groq")


class TestRefineModelSelection:
    """Tests für Model-Auswahl in refine_transcript()."""

    def test_cli_model_overrides_all(self, monkeypatch):
        """CLI-Model überschreibt alles."""
        # from transcribe import refine_transcript (removed)

        monkeypatch.setenv("PULSESCRIBE_REFINE_MODEL", "env-model")
        monkeypatch.setenv("PULSESCRIBE_REFINE_PROVIDER", "openai")

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

        monkeypatch.setenv("PULSESCRIBE_REFINE_MODEL", "env-model")
        monkeypatch.setenv("PULSESCRIBE_REFINE_PROVIDER", "openai")

        with patch("refine.llm._get_refine_client") as mock_client:
            mock_client.return_value.responses.create.return_value = Mock(
                output_text="refined"
            )

            refine_transcript("test")

        call_kwargs = mock_client.return_value.responses.create.call_args
        assert call_kwargs[1]["model"] == "env-model"

    def test_groq_default_model(self, monkeypatch, clean_env):
        """Groq-Provider nutzt openai/gpt-oss-120b als Default."""
        # from transcribe import refine_transcript (removed)

        with patch("refine.llm._get_refine_client") as mock_client:
            mock_client.return_value.chat.completions.create.return_value = Mock(
                choices=[Mock(message=Mock(content="refined"))]
            )

            refine_transcript("test", provider="groq")

        call_kwargs = mock_client.return_value.chat.completions.create.call_args
        assert call_kwargs[1]["model"] == DEFAULT_REFINE_MODEL

    def test_gemini_default_model(self, monkeypatch, clean_env):
        """Gemini nutzt eigenes Default-Modell (nicht das generische DEFAULT_REFINE_MODEL)."""
        mock_response = Mock()
        mock_response.text = "refined"

        # Mock google.genai module hierarchy
        mock_types = Mock()
        mock_genai = Mock()
        mock_genai.types = mock_types

        with patch("refine.llm._get_refine_client") as mock_client:
            with patch.dict("sys.modules", {
                "google": Mock(genai=mock_genai),
                "google.genai": mock_genai,
                "google.genai.types": mock_types
            }):
                mock_client.return_value.models.generate_content.return_value = mock_response

                refine_transcript("test", provider="gemini")

        call_kwargs = mock_client.return_value.models.generate_content.call_args
        assert call_kwargs[1]["model"] == DEFAULT_GEMINI_REFINE_MODEL

    def test_default_model_with_default_provider(self, monkeypatch, clean_env):
        """Default-Provider (groq) nutzt DEFAULT_REFINE_MODEL."""
        # from transcribe import refine_transcript (removed)

        with patch("refine.llm._get_refine_client") as mock_client:
            mock_client.return_value.chat.completions.create.return_value = Mock(
                choices=[Mock(message=Mock(content="refined"))]
            )

            refine_transcript("test")

        call_kwargs = mock_client.return_value.chat.completions.create.call_args
        assert call_kwargs[1]["model"] == DEFAULT_REFINE_MODEL


class TestRefineRequestComposition:
    """Tests für Request-Aufbau und Prompt-/Provider-spezifische Optionen."""

    def test_explicit_prompt_skips_context_detection(self, clean_env):
        """Ein expliziter Prompt darf keine automatische Kontext-Erkennung auslösen."""
        custom_prompt = "Bitte nur leicht glätten"

        with patch("refine.llm.detect_context") as mock_detect_context, patch(
            "refine.llm.get_prompt_for_context"
        ) as mock_get_prompt, patch("refine.llm._get_refine_client") as mock_client:
            mock_client.return_value.responses.create.return_value = Mock(
                output_text="refined"
            )

            refine_transcript(
                "test",
                provider="openai",
                model="gpt-4o-mini",
                prompt=custom_prompt,
            )

        mock_detect_context.assert_not_called()
        mock_get_prompt.assert_not_called()
        call_kwargs = mock_client.return_value.responses.create.call_args
        assert call_kwargs[1]["input"] == f"{custom_prompt}\n\nTranskript:\ntest"

    def test_blank_prompt_falls_back_to_context_prompt(self, clean_env):
        """Leere Prompt-Strings sollen wie None behandelt werden."""
        with patch(
            "refine.llm.detect_context",
            return_value=("chat", "Slack", "App"),
        ) as mock_detect_context, patch(
            "refine.llm.get_prompt_for_context",
            return_value="context prompt",
        ) as mock_get_prompt, patch("refine.llm._get_refine_client") as mock_client:
            mock_client.return_value.responses.create.return_value = Mock(
                output_text="refined"
            )

            refine_transcript(
                "test",
                provider="openai",
                model="gpt-4o-mini",
                prompt="",
            )

        mock_detect_context.assert_called_once_with(None)
        mock_get_prompt.assert_called_once_with("chat")
        call_kwargs = mock_client.return_value.responses.create.call_args
        assert call_kwargs[1]["input"] == "context prompt\n\nTranskript:\ntest"

    def test_openrouter_forwards_provider_routing_configuration(
        self, monkeypatch, clean_env
    ):
        """OpenRouter-Routing-Optionen sollen unverändert an das SDK weitergereicht werden."""
        monkeypatch.setenv("OPENROUTER_PROVIDER_ORDER", "openai, groq")
        monkeypatch.setenv("OPENROUTER_ALLOW_FALLBACKS", "false")

        with patch("refine.llm._get_refine_client") as mock_client:
            mock_client.return_value.chat.completions.create.return_value = Mock(
                choices=[Mock(message=Mock(content="refined"))]
            )

            refine_transcript(
                "test",
                provider="openrouter",
                model="openai/gpt-oss-120b",
            )

        call_kwargs = mock_client.return_value.chat.completions.create.call_args
        assert call_kwargs[1]["extra_body"] == {
            "provider": {
                "order": ["openai", "groq"],
                "allow_fallbacks": False,
            }
        }

    def test_openrouter_ignores_blank_provider_entries(self, monkeypatch, clean_env):
        """Leere Provider-Tokens dürfen keine ungültige OpenRouter-Payload erzeugen."""
        monkeypatch.setenv("OPENROUTER_PROVIDER_ORDER", " openai, , groq,  ")
        monkeypatch.setenv("OPENROUTER_ALLOW_FALLBACKS", "false")

        with patch("refine.llm._get_refine_client") as mock_client:
            mock_client.return_value.chat.completions.create.return_value = Mock(
                choices=[Mock(message=Mock(content="refined"))]
            )

            refine_transcript(
                "test",
                provider="openrouter",
                model="openai/gpt-oss-120b",
            )

        call_kwargs = mock_client.return_value.chat.completions.create.call_args
        assert call_kwargs[1]["extra_body"] == {
            "provider": {
                "order": ["openai", "groq"],
                "allow_fallbacks": False,
            }
        }

    @pytest.mark.parametrize(
        ("model", "expected_level"),
        [
            ("gemini-3-flash-preview", "MINIMAL"),
            ("gemini-3-pro", "LOW"),
        ],
    )
    def test_gemini_uses_expected_thinking_level(
        self, model, expected_level, clean_env
    ):
        """Gemini-Modelle sollen das passende Thinking-Level erhalten."""
        mock_types = SimpleNamespace(
            ThinkingLevel=SimpleNamespace(MINIMAL="MINIMAL", LOW="LOW"),
            ThinkingConfig=Mock(
                side_effect=lambda *, thinking_level: {
                    "thinking_level": thinking_level
                }
            ),
            GenerateContentConfig=Mock(
                side_effect=lambda *, thinking_config: {
                    "thinking_config": thinking_config
                }
            ),
        )
        mock_genai = SimpleNamespace(types=mock_types)

        with patch("refine.llm._get_refine_client") as mock_client, patch.dict(
            "sys.modules",
            {
                "google": SimpleNamespace(genai=mock_genai),
                "google.genai": mock_genai,
                "google.genai.types": mock_types,
            },
        ):
            mock_client.return_value.models.generate_content.return_value = Mock(
                text="refined"
            )

            refine_transcript("test", provider="gemini", model=model)

        call_kwargs = mock_client.return_value.models.generate_content.call_args
        assert call_kwargs[1]["config"] == {
            "thinking_config": {"thinking_level": expected_level}
        }

    def test_openai_gpt5_models_enable_minimal_reasoning(self, clean_env):
        """GPT-5 Modelle sollen den minimalen Reasoning-Modus aktivieren."""
        with patch("refine.llm._get_refine_client") as mock_client:
            mock_client.return_value.responses.create.return_value = Mock(
                output_text="refined"
            )

            refine_transcript("test", provider="openai", model="gpt-5-mini")

        call_kwargs = mock_client.return_value.responses.create.call_args
        assert call_kwargs[1]["reasoning"] == {"effort": "minimal"}


class TestRefineEdgeCases:
    """Tests für Edge-Cases in refine_transcript()."""

    def test_extract_message_content_supports_single_text_part_objects(self):
        """SDK-Content-Objekte mit .text sollen wie String-Content behandelt werden."""
        content = SimpleNamespace(text=" refined ")

        assert _extract_message_content(content) == "refined"

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

    def test_invalid_env_provider_is_reported_clearly(
        self, monkeypatch, clean_env, caplog
    ):
        """Ein Tippfehler in PULSESCRIBE_REFINE_PROVIDER soll klar benannt werden."""
        from refine.llm import maybe_refine_transcript

        monkeypatch.setenv("PULSESCRIBE_REFINE_PROVIDER", "groqq")
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)

        result = maybe_refine_transcript("raw transcript", refine=True)

        assert result == "raw transcript"
        assert "Unbekannter Refine-Provider 'groqq'" in caplog.text
