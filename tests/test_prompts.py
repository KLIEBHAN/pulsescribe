"""Tests für prompts.py - LLM-Prompts und Kontext-Mappings."""

import pytest

from prompts import (
    CONTEXT_PROMPTS,
    DEFAULT_APP_CONTEXTS,
    DEFAULT_REFINE_PROMPT,
    VOICE_COMMANDS_INSTRUCTION,
    get_prompt_for_context,
)


class TestGetPromptForContext:
    """Tests für get_prompt_for_context() - Prompt-Lookup mit Fallback."""

    @pytest.mark.parametrize("context", ["email", "chat", "code"])
    def test_known_contexts_without_voice_commands(self, context):
        """Bekannte Kontexte liefern ihre spezifischen Prompts (ohne Voice-Commands)."""
        result = get_prompt_for_context(context, voice_commands=False)
        assert result == CONTEXT_PROMPTS[context]

    def test_default_context_without_voice_commands(self):
        """'default' Kontext liefert DEFAULT_REFINE_PROMPT (ohne Voice-Commands)."""
        result = get_prompt_for_context("default", voice_commands=False)
        assert result == DEFAULT_REFINE_PROMPT

    @pytest.mark.parametrize("context", ["unknown", "xyz", ""])
    def test_unknown_context_fallback(self, context):
        """Unbekannte Kontexte fallen auf 'default' zurück."""
        result = get_prompt_for_context(context, voice_commands=False)
        assert result == DEFAULT_REFINE_PROMPT


class TestVoiceCommands:
    """Tests für Voice-Commands Integration in Prompts."""

    @pytest.mark.parametrize("context", ["email", "chat", "code", "default"])
    def test_voice_commands_included_by_default(self, context):
        """Voice-Commands werden standardmäßig eingefügt."""
        result = get_prompt_for_context(context)
        assert VOICE_COMMANDS_INSTRUCTION in result

    @pytest.mark.parametrize("context", ["email", "chat", "code", "default"])
    def test_voice_commands_excluded_when_disabled(self, context):
        """Voice-Commands werden nicht eingefügt wenn deaktiviert."""
        result = get_prompt_for_context(context, voice_commands=False)
        assert VOICE_COMMANDS_INSTRUCTION not in result

    @pytest.mark.parametrize(
        "command",
        [
            # Deutsch
            "neuer Absatz",
            "neue Zeile",
            "Punkt",
            "Komma",
            "Fragezeichen",
            "Ausrufezeichen",
            "Doppelpunkt",
            "Semikolon",
            # Englisch
            "new paragraph",
            "new line",
            "period",
            "comma",
            "question mark",
            "exclamation mark",
            "colon",
            "semicolon",
        ],
    )
    def test_voice_commands_instruction_contains_command(self, command):
        """VOICE_COMMANDS_INSTRUCTION enthält alle erwarteten Befehle."""
        assert command in VOICE_COMMANDS_INSTRUCTION

    def test_voice_commands_prepended(self):
        """Voice-Commands werden am Anfang des Prompts eingefügt."""
        result = get_prompt_for_context("default")
        assert result.startswith(VOICE_COMMANDS_INSTRUCTION)


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
