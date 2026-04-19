"""Tests für Custom Prompts - TOML-basierte Prompt-Konfiguration."""

from pathlib import Path
import tomllib
from types import SimpleNamespace

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

        prompts_file.write_text(valid_toml_content, encoding="utf-8")
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

    def test_prompt_section_without_prompt_field_falls_back_to_default(self, prompts_file):
        """Semantisch unvollständige Prompt-Sektion darf keinen Laufzeitfehler auslösen."""
        from utils.custom_prompts import load_custom_prompts

        prompts_file.write_text(
            """
[prompts.email]
note = "missing prompt field"
"""
        )

        result = load_custom_prompts(path=prompts_file)

        assert result["prompts"]["email"]["prompt"] == CONTEXT_PROMPTS["email"]

    def test_non_string_prompt_value_falls_back_to_default(self, prompts_file):
        """Nicht-string Prompt-Werte werden wie kaputte User-Konfig behandelt."""
        from utils.custom_prompts import get_custom_prompt_for_context

        prompts_file.write_text(
            """
[prompts.email]
prompt = 123
"""
        )

        assert get_custom_prompt_for_context("email") == CONTEXT_PROMPTS["email"]

    def test_non_table_voice_commands_falls_back_to_default(self, prompts_file):
        """Semantisch kaputte voice_commands-Rootwerte duerfen nicht crashen."""
        from utils.custom_prompts import load_custom_prompts

        prompts_file.write_text('voice_commands = "broken"\n', encoding="utf-8")

        result = load_custom_prompts(path=prompts_file)

        assert result["voice_commands"]["instruction"] == VOICE_COMMANDS_INSTRUCTION

    def test_non_string_voice_command_instruction_falls_back_to_default(
        self, prompts_file
    ):
        """Nicht-string Voice-Command-Instruktionen duerfen den Prompt-Flow nicht brechen."""
        from utils.custom_prompts import get_custom_voice_commands

        prompts_file.write_text(
            """
[voice_commands]
instruction = 123
""",
            encoding="utf-8",
        )

        assert get_custom_voice_commands() == VOICE_COMMANDS_INSTRUCTION

    def test_non_table_app_contexts_fall_back_to_defaults(self, prompts_file):
        """Semantisch kaputte app_contexts-Rootwerte duerfen nicht crashen."""
        from utils.custom_prompts import load_custom_prompts

        prompts_file.write_text("app_contexts = [1, 2]\n", encoding="utf-8")

        result = load_custom_prompts(path=prompts_file)

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

    def test_cache_invalidation_when_only_ctime_changes(self, prompts_file, monkeypatch):
        """Prompt-Reload darf nicht nur an ``st_mtime`` hängen."""
        from pathlib import Path
        from utils.custom_prompts import load_custom_prompts

        prompts_file.write_text(
            '''
[prompts.default]
prompt = """Version 1"""
''',
            encoding="utf-8",
        )

        original_stat = Path.stat
        ctime_ns = {"value": 1}

        def _patched_stat(self: Path):
            stat_result = original_stat(self)
            if self == prompts_file:
                return SimpleNamespace(
                    st_mtime=123.0,
                    st_mtime_ns=123_000_000_000,
                    st_size=stat_result.st_size,
                    st_ctime=ctime_ns["value"] / 1_000_000_000,
                    st_ctime_ns=ctime_ns["value"],
                )
            return stat_result

        monkeypatch.setattr(Path, "stat", _patched_stat)

        result1 = load_custom_prompts(path=prompts_file)
        assert "Version 1" in result1["prompts"]["default"]["prompt"]

        ctime_ns["value"] = 2
        prompts_file.write_text(
            '''
[prompts.default]
prompt = """Version 2"""
''',
            encoding="utf-8",
        )

        result2 = load_custom_prompts(path=prompts_file)
        assert "Version 2" in result2["prompts"]["default"]["prompt"]

    def test_cache_returns_defensive_copy(self, prompts_file):
        """Mutationen am Rückgabewert dürfen den internen Cache nicht verändern."""
        from utils.custom_prompts import load_custom_prompts

        prompts_file.write_text(
            '''
[prompts.default]
prompt = """Version 1"""
''',
            encoding="utf-8",
        )

        first = load_custom_prompts(path=prompts_file)
        first["prompts"]["default"]["prompt"] = "Mutated in caller"
        first["app_contexts"]["Mail"] = "chat"

        second = load_custom_prompts(path=prompts_file)

        assert second["prompts"]["default"]["prompt"] == "Version 1"
        assert second["app_contexts"]["Mail"] == DEFAULT_APP_CONTEXTS["Mail"]


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

    def test_normalizes_custom_app_context_values(self, prompts_file):
        """Gemischte Großschreibung in Custom-Kontexten bleibt funktionsfähig."""
        from utils.custom_prompts import get_custom_app_contexts

        prompts_file.write_text(
            """
[app_contexts]
CustomApp = "EMAIL"
"""
        )

        result = get_custom_app_contexts()
        assert result["CustomApp"] == "email"

    def test_falls_back_to_defaults_when_no_file(self, prompts_file):
        """Ohne Datei → Hardcoded Defaults."""
        from utils.custom_prompts import get_custom_app_contexts

        # Datei existiert nicht → Default
        result = get_custom_app_contexts()
        assert result == DEFAULT_APP_CONTEXTS

    def test_trims_app_names_and_ignores_invalid_contexts(self, prompts_file):
        """Merge-Logik soll App-Namen bereinigen und kaputte Werte verwerfen."""
        from utils.custom_prompts import get_custom_app_contexts

        prompts_file.write_text(
            """
[app_contexts]
"  Custom App  " = "EMAIL"
IgnoredApp = "unknown"
"   " = "chat"
"""
        )

        result = get_custom_app_contexts()

        assert result["Custom App"] == "email"
        assert "  Custom App  " not in result
        assert "IgnoredApp" not in result
        assert "" not in result


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

    def test_save_state_warms_cache_without_reloading_toml(self, prompts_file, monkeypatch):
        """Der Save-Pfad soll die gemergten Prompt-Daten direkt cachen."""
        from utils.custom_prompts import load_custom_prompts, save_custom_prompts_state

        saved_state = save_custom_prompts_state(
            {
                "prompts": {"email": {"prompt": "Custom Email Prompt"}},
                "app_contexts": {"My App": "code"},
            },
            path=prompts_file,
        )

        assert saved_state["prompts"]["default"]["prompt"] == CONTEXT_PROMPTS["default"]
        assert saved_state["prompts"]["email"]["prompt"] == "Custom Email Prompt"
        assert saved_state["app_contexts"]["My App"] == "code"

        original_read_text = Path.read_text

        def _patched_read_text(self: Path, *args, **kwargs):
            if self == prompts_file:
                raise AssertionError("save should have warmed the prompt cache")
            return original_read_text(self, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", _patched_read_text)

        loaded = load_custom_prompts(path=prompts_file)

        assert loaded["prompts"]["email"]["prompt"] == "Custom Email Prompt"
        assert loaded["app_contexts"]["My App"] == "code"

    def test_save_writes_sections_in_stable_order(self, prompts_file):
        """Section-Reihenfolge ist Teil des lesbaren Dateiformats."""
        from utils.custom_prompts import save_custom_prompts

        save_custom_prompts(
            {
                "app_contexts": {"My App": "code"},
                "prompts": {"default": {"prompt": "Prompt"}},
                "voice_commands": {"instruction": "Commands"},
            },
            path=prompts_file,
        )

        content = prompts_file.read_text(encoding="utf-8")

        assert content.index("[voice_commands]") < content.index("[prompts.default]")
        assert content.index("[prompts.default]") < content.index("[app_contexts]")

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

    def test_save_ignores_malformed_sections_but_persists_valid_entries(self, prompts_file):
        """Kaputte UI-Payloads sollen beim Speichern nicht die ganze Datei blockieren."""
        from utils.custom_prompts import save_custom_prompts, load_custom_prompts

        save_custom_prompts(
            {
                "voice_commands": "broken",
                "prompts": {
                    "default": {"prompt": "Keep me"},
                    "email": 123,
                },
                "app_contexts": {"My App": "code"},
            },
            path=prompts_file,
        )

        loaded = load_custom_prompts(path=prompts_file)

        assert loaded["voice_commands"]["instruction"] == VOICE_COMMANDS_INSTRUCTION
        assert loaded["prompts"]["default"]["prompt"] == "Keep me"
        assert loaded["prompts"]["email"]["prompt"] == CONTEXT_PROMPTS["email"]
        assert loaded["app_contexts"]["My App"] == "code"


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


class TestPromptEditorText:
    """Tests für get_prompt_editor_text()."""

    def test_reuses_cached_app_mappings_formatting(self, monkeypatch):
        import utils.custom_prompts as cp

        data = cp.get_defaults()
        calls: list[dict[str, str]] = []
        original_formatter = cp.format_app_mappings

        def _tracked_format(mappings: dict[str, str]) -> str:
            calls.append(dict(mappings))
            return original_formatter(mappings)

        monkeypatch.setattr(cp, "format_app_mappings", _tracked_format)

        text_cache: dict[str, str] = {}
        first = cp.get_prompt_editor_text(
            "app_mappings",
            data=data,
            text_cache=text_cache,
        )
        second = cp.get_prompt_editor_text(
            "app_mappings",
            data=data,
            text_cache=text_cache,
        )

        assert first == second
        assert len(calls) == 1


class TestFilterOverridesForStorage:
    """Tests für filter_overrides_for_storage()."""

    def test_returns_only_non_default_overrides(self):
        """Gemergte Daten werden auf echte Overrides reduziert."""
        from utils.custom_prompts import get_defaults, filter_overrides_for_storage

        defaults = get_defaults()
        merged = {
            "voice_commands": {"instruction": "Custom Voice"},
            "prompts": {
                "default": {"prompt": defaults["prompts"]["default"]["prompt"]},
                "email": {"prompt": "Custom Email Prompt"},
            },
            "app_contexts": {
                "Mail": defaults["app_contexts"]["Mail"],
                "My App": "code",
            },
        }

        result = filter_overrides_for_storage(merged, defaults=defaults)

        assert result["voice_commands"]["instruction"] == "Custom Voice"
        assert "default" not in result["prompts"]
        assert result["prompts"]["email"]["prompt"] == "Custom Email Prompt"
        assert "Mail" not in result["app_contexts"]
        assert result["app_contexts"]["My App"] == "code"

    def test_returns_empty_for_default_only_data(self):
        """Nur Defaults führen zu leerer Persistenz-Struktur."""
        from utils.custom_prompts import get_defaults, filter_overrides_for_storage

        defaults = get_defaults()
        result = filter_overrides_for_storage(defaults, defaults=defaults)
        assert result == {}

    def test_normalizes_and_filters_invalid_app_context_overrides(self):
        """Persistiert werden nur bereinigte, gültige App-Mappings."""
        from utils.custom_prompts import get_defaults, filter_overrides_for_storage

        defaults = get_defaults()
        result = filter_overrides_for_storage(
            {
                "app_contexts": {
                    "  My App  ": "EMAIL",
                    "Ignored": "unknown",
                    "   ": "chat",
                    "Mail": defaults["app_contexts"]["Mail"],
                }
            },
            defaults=defaults,
        )

        assert result == {"app_contexts": {"My App": "email"}}

    def test_ignores_malformed_sections_but_keeps_valid_overrides(self):
        """Storage-Reduktion soll bei kaputten UI-Daten nicht crashen."""
        from utils.custom_prompts import get_defaults, filter_overrides_for_storage

        defaults = get_defaults()
        result = filter_overrides_for_storage(
            {
                "voice_commands": "broken",
                "prompts": {
                    "default": {"prompt": defaults["prompts"]["default"]["prompt"]},
                    "email": {"prompt": "Custom Email Prompt"},
                    "chat": 123,
                },
                "app_contexts": {
                    " Mail ": defaults["app_contexts"]["Mail"],
                    "  My App  ": "CODE",
                    "Ignored": "unknown",
                },
            },
            defaults=defaults,
        )

        assert result == {
            "prompts": {"email": {"prompt": "Custom Email Prompt"}},
            "app_contexts": {"My App": "code"},
        }


# =============================================================================
# Edge Cases und Error Handling
# =============================================================================


class TestErrorHandling:
    """Tests für Fehlerbehandlung und Edge Cases."""

    def test_file_permission_denied(self, prompts_file, monkeypatch):
        """Datei nicht lesbar → graceful fallback zu Defaults."""
        from utils.custom_prompts import load_custom_prompts, _clear_cache
        import os

        # Datei erstellen
        prompts_file.write_text('[prompts.email]\nprompt = """Test"""')
        _clear_cache()

        # os.stat wirft OSError (Permission denied simulieren)
        original_stat = os.stat

        def mock_stat(path, *args, **kwargs):
            if str(path) == str(prompts_file):
                raise OSError("Permission denied")
            return original_stat(path, *args, **kwargs)

        monkeypatch.setattr(os, "stat", mock_stat)

        # Sollte graceful zu Defaults zurückfallen
        result = load_custom_prompts(path=prompts_file)
        assert result["prompts"]["default"]["prompt"] == CONTEXT_PROMPTS["default"]

    def test_toml_decode_error_logs_warning(self, prompts_file, caplog):
        """TOML Parse-Fehler loggt Warning und fällt zu Defaults."""
        from utils.custom_prompts import load_custom_prompts, _clear_cache
        import logging

        # Ungültiges TOML schreiben
        prompts_file.write_text("invalid = [unclosed bracket")
        _clear_cache()

        with caplog.at_level(logging.WARNING):
            result = load_custom_prompts(path=prompts_file)

        # Defaults zurückgegeben
        assert result["prompts"]["default"]["prompt"] == CONTEXT_PROMPTS["default"]
        # Warning wurde geloggt
        assert any("fehlerhaft" in record.message for record in caplog.records)

    def test_empty_toml_file(self, prompts_file):
        """Leere TOML-Datei → alle Defaults."""
        from utils.custom_prompts import load_custom_prompts

        prompts_file.write_text("")
        result = load_custom_prompts(path=prompts_file)

        # Alle Defaults müssen vorhanden sein
        assert result["prompts"]["default"]["prompt"] == CONTEXT_PROMPTS["default"]
        assert result["prompts"]["email"]["prompt"] == CONTEXT_PROMPTS["email"]
        assert result["voice_commands"]["instruction"] == VOICE_COMMANDS_INSTRUCTION
        assert result["app_contexts"] == DEFAULT_APP_CONTEXTS


class TestCacheBehavior:
    """Tests für Cache-Invalidation und mtime."""

    def test_cache_hits_with_same_mtime(self, prompts_file):
        """Cache-Hits liefern stabile Inhalte, aber keine geteilte Referenz."""
        from utils.custom_prompts import load_custom_prompts

        prompts_file.write_text('[prompts.default]\nprompt = """Cached"""')

        # Erstes Laden
        result1 = load_custom_prompts(path=prompts_file)
        # Zweites Laden (ohne Dateiänderung)
        result2 = load_custom_prompts(path=prompts_file)

        assert result1 == result2
        assert result1 is not result2

    def test_deleted_file_invalidates_cached_prompts(self, prompts_file):
        """Wird die Datei gelöscht, darf kein veralteter Cache-Inhalt zurückkommen."""
        from utils.custom_prompts import load_custom_prompts

        prompts_file.write_text(
            '''
[prompts.default]
prompt = """Cached version"""
''',
            encoding="utf-8",
        )

        cached = load_custom_prompts(path=prompts_file)
        assert cached["prompts"]["default"]["prompt"] == "Cached version"

        prompts_file.unlink()

        after_delete = load_custom_prompts(path=prompts_file)
        assert after_delete["prompts"]["default"]["prompt"] == CONTEXT_PROMPTS["default"]

    def test_save_invalidates_cache(self, prompts_file):
        """Nach save() gibt load() frische Daten."""
        from utils.custom_prompts import save_custom_prompts, load_custom_prompts

        # Erst speichern
        save_custom_prompts(
            {"prompts": {"default": {"prompt": "Version 1"}}},
            path=prompts_file,
        )
        result1 = load_custom_prompts(path=prompts_file)
        assert "Version 1" in result1["prompts"]["default"]["prompt"]

        # Neu speichern (sollte Cache invalidieren)
        save_custom_prompts(
            {"prompts": {"default": {"prompt": "Version 2"}}},
            path=prompts_file,
        )
        result2 = load_custom_prompts(path=prompts_file)
        assert "Version 2" in result2["prompts"]["default"]["prompt"]


class TestMergeBehavior:
    """Tests für Merge-Logik mit Defaults."""

    def test_merge_empty_prompts_section(self, prompts_file):
        """Leere [prompts] Sektion → alle Defaults."""
        from utils.custom_prompts import load_custom_prompts

        # [prompts] vorhanden aber leer
        prompts_file.write_text("[prompts]\n")
        result = load_custom_prompts(path=prompts_file)

        # Alle Prompt-Defaults müssen vorhanden sein
        assert result["prompts"]["default"]["prompt"] == CONTEXT_PROMPTS["default"]
        assert result["prompts"]["email"]["prompt"] == CONTEXT_PROMPTS["email"]
        assert result["prompts"]["chat"]["prompt"] == CONTEXT_PROMPTS["chat"]
        assert result["prompts"]["code"]["prompt"] == CONTEXT_PROMPTS["code"]


class TestParseAppMappings:
    """Tests für parse_app_mappings() Edge Cases."""

    def test_parse_app_mappings_malformed_lines(self):
        """Zeilen ohne '=' werden ignoriert."""
        from utils.custom_prompts import parse_app_mappings

        text = """# Comment
Mail = email
invalid line without equals
Slack = chat
"""
        result = parse_app_mappings(text)

        assert result == {"Mail": "email", "Slack": "chat"}
        assert "invalid" not in str(result)

    def test_parse_app_mappings_empty_string(self):
        """Leerer String → leeres Dict."""
        from utils.custom_prompts import parse_app_mappings

        assert parse_app_mappings("") == {}

    def test_parse_app_mappings_only_comments(self):
        """Nur Kommentare → leeres Dict."""
        from utils.custom_prompts import parse_app_mappings

        text = """# Comment 1
# Comment 2
# Another comment
"""
        assert parse_app_mappings(text) == {}

    def test_parse_app_mappings_normalizes_contexts_and_strips_comments(self):
        """Inline-Kommentare und Großschreibung dürfen Mappings nicht kaputt machen."""
        from utils.custom_prompts import parse_app_mappings

        text = """Safari = CHAT  # use chat style
"My IDE" = CODE
"""

        assert parse_app_mappings(text) == {"Safari": "chat", "My IDE": "code"}

    def test_parse_app_mappings_ignores_unknown_contexts(self):
        """Unbekannte Kontexte werden nicht als kaputte Overrides gespeichert."""
        from utils.custom_prompts import parse_app_mappings

        text = """Safari = unknown
Slack = chat
"""

        assert parse_app_mappings(text) == {"Slack": "chat"}


class TestSerializationEdgeCases:
    """Tests für Serialisierung mit schwierigen Zeichen."""

    def test_serialize_combined_escapes(self, prompts_file):
        """Text mit Backslash UND Triple-Quotes korrekt escaped."""
        from utils.custom_prompts import save_custom_prompts, load_custom_prompts

        # Kombinierter Edge Case: Backslash + Triple-Quotes
        tricky_prompt = 'Pfad: C:\\Users\\Test und """quoted""" text'

        save_custom_prompts(
            {"prompts": {"default": {"prompt": tricky_prompt}}},
            path=prompts_file,
        )

        loaded = load_custom_prompts(path=prompts_file)
        assert loaded["prompts"]["default"]["prompt"] == tricky_prompt

    def test_serialize_unicode_characters(self, prompts_file):
        """Unicode-Zeichen wie Pfeile (→) werden korrekt gespeichert.

        Regression test für Windows cp1252 Encoding Bug:
        'charmap' codec can't encode character '\\u2192'
        """
        from utils.custom_prompts import save_custom_prompts, load_custom_prompts

        # Prompt mit Unicode-Zeichen die cp1252 nicht unterstützt
        unicode_prompt = """Voice Commands:
- "neuer Absatz" → Neuer Absatz
- "neue Zeile" → Zeilenumbruch
- Emoji Test: 🎤 📝 ✅"""

        save_custom_prompts(
            {"voice_commands": {"instruction": unicode_prompt}},
            path=prompts_file,
        )

        loaded = load_custom_prompts(path=prompts_file)
        assert loaded["voice_commands"]["instruction"] == unicode_prompt
        assert "→" in loaded["voice_commands"]["instruction"]
        assert "🎤" in loaded["voice_commands"]["instruction"]

    def test_serialize_app_context_with_dots_in_name(self, prompts_file):
        """App-Namen mit Dots dürfen nicht als TOML-Tabellen interpretiert werden.

        Regression: Bare keys mit Dots wurden als verschachtelte Tabellen geparst,
        z.B. 'Microsoft.Teams = "chat"' wurde zu {Microsoft: {Teams: "chat"}}.
        """
        from utils.custom_prompts import save_custom_prompts, load_custom_prompts

        data = {
            "app_contexts": {
                "Microsoft.Teams": "chat",
                "com.apple.Mail": "email",
            },
        }

        save_custom_prompts(data, path=prompts_file)

        # TOML muss valide sein und die Keys korrekt zurückgeben
        loaded = load_custom_prompts(path=prompts_file)
        assert loaded["app_contexts"]["Microsoft.Teams"] == "chat"
        assert loaded["app_contexts"]["com.apple.Mail"] == "email"

    def test_serialize_app_context_with_quotes_in_name(self, prompts_file):
        """App-Namen mit Doppel-Quotes werden korrekt escaped."""
        from utils.custom_prompts import save_custom_prompts, load_custom_prompts

        data = {
            "app_contexts": {
                'App "Pro"': "code",
            },
        }

        save_custom_prompts(data, path=prompts_file)

        loaded = load_custom_prompts(path=prompts_file)
        assert loaded["app_contexts"]['App "Pro"'] == "code"

    def test_serialize_app_context_with_backslash_in_name(self, prompts_file):
        """App-Namen mit Backslashes werden korrekt escaped."""
        from utils.custom_prompts import save_custom_prompts, load_custom_prompts

        data = {
            "app_contexts": {
                "Path\\App": "code",
            },
        }

        save_custom_prompts(data, path=prompts_file)

        loaded = load_custom_prompts(path=prompts_file)
        assert loaded["app_contexts"]["Path\\App"] == "code"


class TestAtomicWrite:
    """Tests für atomare Schreibvorgänge in save_custom_prompts."""

    def test_save_uses_atomic_write(self, prompts_file, monkeypatch):
        """save_custom_prompts() nutzt _write_text_atomic statt write_text.

        Stellt sicher, dass ein Crash während des Schreibens die Datei nicht
        korrumpiert (write_text schreibt direkt, _write_text_atomic via Rename).
        """
        import utils.custom_prompts as cp

        calls = []
        original_write = cp._write_text_atomic

        def tracking_write(path, content, **kwargs):
            calls.append(("atomic", path))
            return original_write(path, content, **kwargs)

        monkeypatch.setattr(cp, "_write_text_atomic", tracking_write)

        cp.save_custom_prompts(
            {"prompts": {"default": {"prompt": "Test"}}},
            path=prompts_file,
        )

        assert any(call[0] == "atomic" for call in calls), (
            "save_custom_prompts must use _write_text_atomic for crash safety"
        )

    def test_save_leaves_no_temp_files_on_success(self, prompts_file):
        """Nach erfolgreichem Save dürfen keine .tmp-Dateien übrig bleiben."""
        from utils.custom_prompts import save_custom_prompts

        save_custom_prompts(
            {"prompts": {"default": {"prompt": "Clean write"}}},
            path=prompts_file,
        )

        tmp_files = list(prompts_file.parent.glob(".*prompts*.tmp"))
        assert tmp_files == [], f"Temp files left behind: {tmp_files}"
