"""Tests für prompts.py - LLM-Prompts und Kontext-Mappings."""

import pytest

from prompts import (
    CONTEXT_PROMPTS,
    DEFAULT_APP_CONTEXTS,
    DEFAULT_REFINE_PROMPT,
    get_prompt_for_context,
)


class TestGetPromptForContext:
    """Tests für get_prompt_for_context() - Prompt-Lookup mit Fallback."""

    @pytest.mark.parametrize("context", ["email", "chat", "code"])
    def test_known_contexts(self, context):
        """Bekannte Kontexte liefern ihre spezifischen Prompts."""
        assert get_prompt_for_context(context) == CONTEXT_PROMPTS[context]

    def test_default_context(self):
        """'default' Kontext liefert DEFAULT_REFINE_PROMPT."""
        assert get_prompt_for_context("default") == DEFAULT_REFINE_PROMPT

    @pytest.mark.parametrize("context", ["unknown", "xyz", ""])
    def test_unknown_context_fallback(self, context):
        """Unbekannte Kontexte fallen auf 'default' zurück."""
        assert get_prompt_for_context(context) == DEFAULT_REFINE_PROMPT


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

    @pytest.mark.parametrize(
        "app,expected_context",
        [
            # Email-Apps
            ("Mail", "email"),
            ("Outlook", "email"),
            ("Spark", "email"),
            ("Thunderbird", "email"),
            # Chat-Apps
            ("Slack", "chat"),
            ("Discord", "chat"),
            ("Messages", "chat"),
            ("WhatsApp", "chat"),
            # Code-Editoren
            ("Code", "code"),
            ("VS Code", "code"),
            ("Cursor", "code"),
            ("Terminal", "code"),
            ("iTerm2", "code"),
        ],
    )
    def test_app_context_mapping(self, app, expected_context):
        """Apps sind auf ihre Kontexte gemappt."""
        assert DEFAULT_APP_CONTEXTS.get(app) == expected_context

    @pytest.mark.parametrize("app", ["Safari", "Unknown App", "Firefox"])
    def test_unknown_app_returns_none(self, app):
        """Unbekannte Apps sind nicht im Mapping."""
        assert DEFAULT_APP_CONTEXTS.get(app) is None
