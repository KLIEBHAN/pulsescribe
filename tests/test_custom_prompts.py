"""Tests für Custom Prompts - TOML-basierte Prompt-Konfiguration."""

import tomllib

import pytest

from refine.prompts import (
    CONTEXT_PROMPTS,
    VOICE_COMMANDS_INSTRUCTION,
    DEFAULT_APP_CONTEXTS,
)


@pytest.fixture
def prompts_file(tmp_path, monkeypatch):
    """Fixture: Temporäre prompts.toml für isolierte Tests.

    - Patcht automatisch PROMPTS_FILE auf tmp_path
    - Leert Cache vor jedem Test
    - Kein manuelles try/finally mehr nötig
    """
    prompts_path = tmp_path / "prompts.toml"

    # Import und Patch
    import utils.custom_prompts as cp

    monkeypatch.setattr(cp, "PROMPTS_FILE", prompts_path)
    cp._clear_cache()

    return prompts_path


@pytest.fixture
def valid_toml_content():
    """Fixture: Gültiges TOML mit Custom Prompts."""
    return '''# Custom Prompts
[voice_commands]
instruction = """
Custom Voice Commands Instruction.
- "test" → Test
"""

[prompts.default]
prompt = """Custom Default Prompt."""

[prompts.email]
prompt = """Custom Email Prompt."""

[app_contexts]
CustomApp = "email"
"My IDE" = "code"
'''


class TestLoadCustomPrompts:
    """Tests für load_custom_prompts() - TOML-Parsing mit Fallbacks."""

    def test_file_not_exists_returns_defaults(self, prompts_file):
        """Fehlende Datei gibt Hardcoded Defaults zurück."""
        from utils.custom_prompts import load_custom_prompts

        result = load_custom_prompts(path=prompts_file)

        assert "prompts" in result
        assert result["prompts"]["default"]["prompt"] == CONTEXT_PROMPTS["default"]
        assert result["voice_commands"]["instruction"] == VOICE_COMMANDS_INSTRUCTION
        assert result["app_contexts"] == DEFAULT_APP_CONTEXTS

    def test_load_valid_toml(self, prompts_file, valid_toml_content):
        """Gültiges TOML wird korrekt geparst."""
        from utils.custom_prompts import load_custom_prompts

        prompts_file.write_text(valid_toml_content)
        result = load_custom_prompts(path=prompts_file)

        assert "Custom Default Prompt" in result["prompts"]["default"]["prompt"]
        assert "Custom Email Prompt" in result["prompts"]["email"]["prompt"]
        assert "Custom Voice Commands" in result["voice_commands"]["instruction"]
        assert result["app_contexts"]["CustomApp"] == "email"

    def test_load_invalid_toml_returns_defaults(self, prompts_file):
        """Fehlerhaftes TOML gibt Fallback auf Defaults."""
        from utils.custom_prompts import load_custom_prompts

        prompts_file.write_text("not valid toml {{{{")
        result = load_custom_prompts(path=prompts_file)

        # Fallback auf Defaults
        assert result["prompts"]["default"]["prompt"] == CONTEXT_PROMPTS["default"]

    def test_load_partial_config_merges_with_defaults(self, prompts_file):
        """Nur geänderte Felder überschreiben, Rest bleibt Default."""
        from utils.custom_prompts import load_custom_prompts

        # Nur email-Prompt überschreiben
        prompts_file.write_text(
            '''
[prompts.email]
prompt = """Mein Custom Email Prompt."""
'''
        )
        result = load_custom_prompts(path=prompts_file)

        # email ist custom
        assert "Mein Custom Email" in result["prompts"]["email"]["prompt"]
        # default bleibt Hardcoded
        assert result["prompts"]["default"]["prompt"] == CONTEXT_PROMPTS["default"]
        # voice_commands bleibt Default
        assert result["voice_commands"]["instruction"] == VOICE_COMMANDS_INSTRUCTION
        # app_contexts bleibt Default
        assert result["app_contexts"] == DEFAULT_APP_CONTEXTS

    def test_cache_invalidation_on_mtime_change(self, prompts_file):
        """Reload bei Datei-Änderung (mtime-basierter Cache)."""
        from utils.custom_prompts import load_custom_prompts

        # Erste Version
        prompts_file.write_text(
            '''
[prompts.default]
prompt = """Version 1"""
'''
        )
        result1 = load_custom_prompts(path=prompts_file)
        assert "Version 1" in result1["prompts"]["default"]["prompt"]

        # Datei ändern (mtime muss sich ändern)
        import time

        # 0.1s für zuverlässige mtime-Änderung auf allen Filesystems (HFS+, APFS, etc.)
        time.sleep(0.1)
        prompts_file.write_text(
            '''
[prompts.default]
prompt = """Version 2"""
'''
        )
        result2 = load_custom_prompts(path=prompts_file)
        assert "Version 2" in result2["prompts"]["default"]["prompt"]


class TestGetCustomPromptForContext:
    """Tests für get_custom_prompt_for_context()."""

    def test_returns_custom_prompt(self, prompts_file):
        """Custom Prompt hat Priorität über Default."""
        from utils.custom_prompts import get_custom_prompt_for_context

        prompts_file.write_text(
            '''
[prompts.email]
prompt = """Mein Email Prompt."""
'''
        )

        result = get_custom_prompt_for_context("email")
        assert "Mein Email Prompt" in result

    def test_falls_back_to_default_for_missing_context(self, prompts_file):
        """Fehlender Custom-Kontext fällt auf Hardcoded Default zurück."""
        from utils.custom_prompts import get_custom_prompt_for_context

        prompts_file.write_text(
            '''
[prompts.email]
prompt = """Nur Email custom."""
'''
        )

        # chat ist nicht custom → Default
        result = get_custom_prompt_for_context("chat")
        assert result == CONTEXT_PROMPTS["chat"]

    def test_unknown_context_returns_default(self, prompts_file):
        """Unbekannter Kontext gibt 'default' Prompt zurück."""
        from utils.custom_prompts import get_custom_prompt_for_context

        # Keine Datei → Defaults, unbekannter Kontext → default
        result = get_custom_prompt_for_context("unknown_context")
        assert result == CONTEXT_PROMPTS["default"]

    def test_invalid_toml_returns_default(self, prompts_file):
        """Fehlerhafte TOML-Datei fällt auf Hardcoded Default zurück."""
        from utils.custom_prompts import _clear_cache, get_custom_prompt_for_context

        # Ungültiges TOML schreiben
        prompts_file.write_text("this is {{ not valid toml")
        _clear_cache()

        # Public API muss trotzdem funktionieren und Default liefern
        result = get_custom_prompt_for_context("default")
        assert result == CONTEXT_PROMPTS["default"]


class TestGetCustomVoiceCommands:
    """Tests für get_custom_voice_commands()."""

    def test_returns_custom_voice_commands(self, prompts_file):
        """Custom Voice-Commands werden geladen."""
        from utils.custom_prompts import get_custom_voice_commands

        prompts_file.write_text(
            '''
[voice_commands]
instruction = """Meine Custom Voice Commands."""
'''
        )

        result = get_custom_voice_commands()
        assert "Meine Custom Voice Commands" in result

    def test_falls_back_to_default_voice_commands(self, prompts_file):
        """Ohne Custom Voice-Commands → Hardcoded Default."""
        from utils.custom_prompts import get_custom_voice_commands

        # Datei existiert nicht → Default
        result = get_custom_voice_commands()
        assert result == VOICE_COMMANDS_INSTRUCTION


class TestGetCustomAppContexts:
    """Tests für get_custom_app_contexts()."""

    def test_returns_merged_app_contexts(self, prompts_file):
        """Custom App-Mappings werden mit Defaults gemergt."""
        from utils.custom_prompts import get_custom_app_contexts

        prompts_file.write_text(
            """
[app_contexts]
CustomApp = "email"
Mail = "chat"
"""
        )

        result = get_custom_app_contexts()
        # Custom App hinzugefügt
        assert result["CustomApp"] == "email"
        # Mail überschrieben (war "email" im Default)
        assert result["Mail"] == "chat"
        # Andere Defaults erhalten
        assert result["Slack"] == "chat"

    def test_falls_back_to_defaults_when_no_file(self, prompts_file):
        """Ohne Datei → Hardcoded Defaults."""
        from utils.custom_prompts import get_custom_app_contexts

        # Datei existiert nicht → Default
        result = get_custom_app_contexts()
        assert result == DEFAULT_APP_CONTEXTS


class TestSaveCustomPrompts:
    """Tests für save_custom_prompts()."""

    def test_save_creates_valid_toml(self, prompts_file):
        """Speichern erstellt gültiges TOML."""
        from utils.custom_prompts import save_custom_prompts

        data = {
            "voice_commands": {"instruction": "Meine Voice Commands."},
            "prompts": {
                "default": {"prompt": "Mein Default Prompt."},
                "email": {"prompt": "Mein Email Prompt."},
            },
            "app_contexts": {"MyApp": "code"},
        }

        save_custom_prompts(data, path=prompts_file)

        # Datei existiert und ist valides TOML
        assert prompts_file.exists()
        content = prompts_file.read_text()
        parsed = tomllib.loads(content)

        assert "Meine Voice Commands" in parsed["voice_commands"]["instruction"]
        assert "Mein Default Prompt" in parsed["prompts"]["default"]["prompt"]
        assert parsed["app_contexts"]["MyApp"] == "code"

    def test_save_then_load_roundtrip(self, prompts_file):
        """Gespeicherte Daten können wieder geladen werden."""
        from utils.custom_prompts import save_custom_prompts, load_custom_prompts

        original_data = {
            "voice_commands": {"instruction": "Test Voice Commands."},
            "prompts": {
                "chat": {"prompt": "Chat Prompt mit Umlauten: äöü."},
            },
            "app_contexts": {"Test App": "email"},  # Mit Leerzeichen
        }

        save_custom_prompts(original_data, path=prompts_file)
        loaded = load_custom_prompts(path=prompts_file)

        assert "Test Voice Commands" in loaded["voice_commands"]["instruction"]
        assert "Chat Prompt mit Umlauten" in loaded["prompts"]["chat"]["prompt"]
        assert loaded["app_contexts"]["Test App"] == "email"

    def test_save_escapes_triple_quotes(self, prompts_file):
        """Triple-Quotes im Prompt werden korrekt escaped."""
        from utils.custom_prompts import save_custom_prompts, load_custom_prompts

        # Prompt mit Triple-Quotes (würde TOML brechen ohne Escaping)
        tricky_prompt = 'Hier sind Triple-Quotes: """ und noch mehr Text.'

        save_custom_prompts(
            {"prompts": {"default": {"prompt": tricky_prompt}}},
            path=prompts_file,
        )

        # Datei muss valides TOML sein
        loaded = load_custom_prompts(path=prompts_file)

        # Prompt muss exakt erhalten bleiben
        assert loaded["prompts"]["default"]["prompt"] == tricky_prompt

    def test_save_escapes_backslashes(self, prompts_file):
        """Backslashes im Prompt werden korrekt escaped."""
        from utils.custom_prompts import save_custom_prompts, load_custom_prompts

        # Prompt mit Backslashes (Windows-Pfade, Escape-Sequenzen)
        tricky_prompt = "Pfad: C:\\Users\\Test und \\n bleibt \\n"

        save_custom_prompts(
            {"prompts": {"email": {"prompt": tricky_prompt}}},
            path=prompts_file,
        )

        loaded = load_custom_prompts(path=prompts_file)

        assert loaded["prompts"]["email"]["prompt"] == tricky_prompt


class TestResetToDefaults:
    """Tests für reset_to_defaults()."""

    def test_reset_removes_file(self, prompts_file):
        """Reset löscht die User-Config-Datei."""
        from utils.custom_prompts import reset_to_defaults, save_custom_prompts

        # Erst eine Datei erstellen
        save_custom_prompts(
            {"prompts": {"default": {"prompt": "test"}}}, path=prompts_file
        )
        assert prompts_file.exists()

        # Reset
        reset_to_defaults(path=prompts_file)
        assert not prompts_file.exists()

    def test_reset_clears_cache(self, prompts_file):
        """Reset leert auch den Cache."""
        from utils.custom_prompts import (
            reset_to_defaults,
            save_custom_prompts,
            load_custom_prompts,
        )

        # Custom Prompt speichern
        save_custom_prompts(
            {"prompts": {"default": {"prompt": "Custom before reset."}}},
            path=prompts_file,
        )
        loaded = load_custom_prompts(path=prompts_file)
        assert "Custom before reset" in loaded["prompts"]["default"]["prompt"]

        # Reset
        reset_to_defaults(path=prompts_file)

        # Erneutes Laden gibt Defaults zurück
        loaded_after = load_custom_prompts(path=prompts_file)
        assert (
            loaded_after["prompts"]["default"]["prompt"] == CONTEXT_PROMPTS["default"]
        )


class TestGetDefaults:
    """Tests für get_defaults() - UI braucht Zugriff auf Defaults."""

    def test_get_defaults_returns_all_contexts(self):
        """get_defaults() liefert alle 4 Kontexte."""
        from utils.custom_prompts import get_defaults

        defaults = get_defaults()

        assert "prompts" in defaults
        assert "default" in defaults["prompts"]
        assert "email" in defaults["prompts"]
        assert "chat" in defaults["prompts"]
        assert "code" in defaults["prompts"]

    def test_get_defaults_includes_voice_commands(self):
        """get_defaults() enthält Voice-Commands."""
        from utils.custom_prompts import get_defaults

        defaults = get_defaults()

        assert "voice_commands" in defaults
        assert "instruction" in defaults["voice_commands"]
        assert "neuer Absatz" in defaults["voice_commands"]["instruction"]

    def test_get_defaults_includes_app_contexts(self):
        """get_defaults() enthält App-Mappings."""
        from utils.custom_prompts import get_defaults

        defaults = get_defaults()

        assert "app_contexts" in defaults
        assert defaults["app_contexts"]["Mail"] == "email"
        assert defaults["app_contexts"]["Slack"] == "chat"
