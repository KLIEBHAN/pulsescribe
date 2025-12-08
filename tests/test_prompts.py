"""Tests für prompts.py - LLM-Prompts und Kontext-Mappings."""

from prompts import (
    CONTEXT_PROMPTS,
    DEFAULT_APP_CONTEXTS,
    DEFAULT_REFINE_PROMPT,
    get_prompt_for_context,
)


class TestGetPromptForContext:
    """Tests für get_prompt_for_context() - Prompt-Lookup mit Fallback."""

    def test_known_contexts(self):
        """Bekannte Kontexte liefern ihre spezifischen Prompts."""
        assert get_prompt_for_context("email") == CONTEXT_PROMPTS["email"]
        assert get_prompt_for_context("chat") == CONTEXT_PROMPTS["chat"]
        assert get_prompt_for_context("code") == CONTEXT_PROMPTS["code"]

    def test_default_context(self):
        """'default' Kontext liefert DEFAULT_REFINE_PROMPT."""
        assert get_prompt_for_context("default") == DEFAULT_REFINE_PROMPT

    def test_unknown_context_fallback(self):
        """Unbekannte Kontexte fallen auf 'default' zurück."""
        assert get_prompt_for_context("unknown") == CONTEXT_PROMPTS["default"]
        assert get_prompt_for_context("xyz") == DEFAULT_REFINE_PROMPT

    def test_empty_string_fallback(self):
        """Leerer String fällt auf 'default' zurück."""
        assert get_prompt_for_context("") == DEFAULT_REFINE_PROMPT


class TestPromptConstants:
    """Tests für Prompt-Konstanten - Struktur und Vollständigkeit."""

    def test_context_prompts_has_all_keys(self):
        """CONTEXT_PROMPTS enthält alle erwarteten Keys."""
        expected_keys = {"email", "chat", "code", "default"}
        assert set(CONTEXT_PROMPTS.keys()) == expected_keys

    def test_default_prompt_not_empty(self):
        """DEFAULT_REFINE_PROMPT ist nicht leer."""
        assert DEFAULT_REFINE_PROMPT
        assert len(DEFAULT_REFINE_PROMPT) > 50  # Sinnvoller Inhalt


class TestDefaultAppContexts:
    """Tests für DEFAULT_APP_CONTEXTS - App-zu-Kontext Mapping."""

    def test_email_apps_mapped(self):
        """E-Mail-Apps sind auf 'email' gemappt."""
        email_apps = ["Mail", "Outlook", "Spark", "Thunderbird"]
        for app in email_apps:
            assert (
                DEFAULT_APP_CONTEXTS.get(app) == "email"
            ), f"{app} should map to 'email'"

    def test_chat_apps_mapped(self):
        """Chat-Apps sind auf 'chat' gemappt."""
        chat_apps = ["Slack", "Discord", "Messages", "WhatsApp"]
        for app in chat_apps:
            assert (
                DEFAULT_APP_CONTEXTS.get(app) == "chat"
            ), f"{app} should map to 'chat'"

    def test_code_apps_mapped(self):
        """Code-Editoren sind auf 'code' gemappt."""
        code_apps = ["Code", "VS Code", "Cursor", "Terminal", "iTerm2"]
        for app in code_apps:
            assert (
                DEFAULT_APP_CONTEXTS.get(app) == "code"
            ), f"{app} should map to 'code'"

    def test_unknown_app_returns_none(self):
        """Unbekannte Apps sind nicht im Mapping."""
        assert DEFAULT_APP_CONTEXTS.get("Safari") is None
        assert DEFAULT_APP_CONTEXTS.get("Unknown App") is None
