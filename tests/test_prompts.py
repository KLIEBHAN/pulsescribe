"""Tests für prompts.py - LLM-Prompts und Kontext-Mappings."""

import pytest

from refine.prompts import (
    CONTEXT_PROMPTS,
    DEFAULT_REFINE_PROMPT,
    VOICE_COMMANDS_INSTRUCTION,
    get_prompt_for_context,
)


@pytest.fixture(autouse=True)
def clean_custom_prompts_state(tmp_path, monkeypatch):
    """Stellt sicher, dass Tests Defaults verwenden, nicht User-Dateien."""
    import utils.custom_prompts as cp

    # Auf nicht-existierenden Pfad zeigen → Defaults werden verwendet
    monkeypatch.setattr(cp, "PROMPTS_FILE", tmp_path / "prompts.toml")
    cp._clear_cache()


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

    def test_voice_commands_instruction_is_comprehensive(self):
        """VOICE_COMMANDS_INSTRUCTION enthält Befehle für beide Sprachen."""
        # Stichproben statt aller 16 Befehle einzeln
        assert "neuer Absatz" in VOICE_COMMANDS_INSTRUCTION  # Deutsch
        assert "new paragraph" in VOICE_COMMANDS_INSTRUCTION  # Englisch
        assert len(VOICE_COMMANDS_INSTRUCTION) > 100  # Sinnvoller Inhalt

    def test_voice_commands_prepended(self):
        """Voice-Commands werden am Anfang des Prompts eingefügt."""
        result = get_prompt_for_context("default")
        assert result.startswith(VOICE_COMMANDS_INSTRUCTION)
