"""Tests für Kontext-Erkennung und App-Mapping."""

import sys
from unittest.mock import patch

from refine.context import (
    detect_context,
    _get_custom_app_contexts,
    _app_to_context,
)


class TestGetCustomAppContexts:
    """Tests für _get_custom_app_contexts() - ENV-basiertes Mapping."""

    def test_no_env_set(self, clean_env):
        """Ohne ENV gibt leeres Dict zurück."""
        result = _get_custom_app_contexts()
        assert result == {}

    def test_valid_json(self, monkeypatch):
        """Gültiges JSON wird geparst."""
        import refine.context

        monkeypatch.setattr(refine.context, "_custom_app_contexts_cache", None)
        monkeypatch.setenv("PULSESCRIBE_APP_CONTEXTS", '{"CustomApp": "email"}')

        result = _get_custom_app_contexts()

        assert result == {"CustomApp": "email"}

    def test_valid_json_normalizes_context_values(self, monkeypatch):
        """Kontexte aus ENV-JSON werden robust normalisiert."""
        import refine.context

        monkeypatch.setattr(refine.context, "_custom_app_contexts_cache", None)
        monkeypatch.setattr(refine.context, "_custom_app_contexts_signature", None)
        monkeypatch.setenv("PULSESCRIBE_APP_CONTEXTS", '{"CustomApp": " CHAT "}')

        result = _get_custom_app_contexts()

        assert result == {"CustomApp": "chat"}

    def test_invalid_json(self, monkeypatch):
        """Ungültiges JSON gibt leeres Dict zurück."""
        import refine.context

        monkeypatch.setattr(refine.context, "_custom_app_contexts_cache", None)
        monkeypatch.setenv("PULSESCRIBE_APP_CONTEXTS", "not valid json")

        result = _get_custom_app_contexts()

        assert result == {}

    def test_non_object_json_returns_empty_mapping(self, monkeypatch):
        """Valides JSON mit falschem Root-Typ darf keinen Crash auslösen."""
        import refine.context

        monkeypatch.setattr(refine.context, "_custom_app_contexts_cache", None)
        monkeypatch.setattr(refine.context, "_custom_app_contexts_signature", None)
        monkeypatch.setenv("PULSESCRIBE_APP_CONTEXTS", '["Slack", "chat"]')

        result = _get_custom_app_contexts()

        assert result == {}

    def test_caching(self, monkeypatch):
        """Geänderte ENV-Werte invalidieren den Cache automatisch."""
        import refine.context

        monkeypatch.setattr(refine.context, "_custom_app_contexts_cache", None)
        monkeypatch.setattr(refine.context, "_custom_app_contexts_signature", None)
        monkeypatch.setenv("PULSESCRIBE_APP_CONTEXTS", '{"App1": "chat"}')

        # Erster Aufruf
        result1 = _get_custom_app_contexts()
        assert result1 == {"App1": "chat"}

        # ENV ändern - neuer Wert muss sichtbar werden
        monkeypatch.setenv("PULSESCRIBE_APP_CONTEXTS", '{"App2": "code"}')
        result2 = _get_custom_app_contexts()

        assert result2 == {"App2": "code"}

    def test_cache_resets_when_env_is_removed(self, monkeypatch):
        """Wird das ENV gelöscht, muss der Cache auf leeres Mapping zurückfallen."""
        import refine.context

        monkeypatch.setattr(refine.context, "_custom_app_contexts_cache", None)
        monkeypatch.setattr(refine.context, "_custom_app_contexts_signature", None)
        monkeypatch.setenv("PULSESCRIBE_APP_CONTEXTS", '{"App1": "chat"}')

        assert _get_custom_app_contexts() == {"App1": "chat"}

        monkeypatch.delenv("PULSESCRIBE_APP_CONTEXTS", raising=False)
        assert _get_custom_app_contexts() == {}


class TestAppToContext:
    """Tests für _app_to_context() - App-Name zu Kontext-Typ."""

    def test_known_app(self, clean_env):
        """Bekannte Apps werden korrekt gemappt."""
        assert _app_to_context("Slack") == "chat"
        assert _app_to_context("Mail") == "email"
        assert _app_to_context("VS Code") == "code"

    def test_unknown_app(self, clean_env):
        """Unbekannte Apps geben 'default' zurück."""
        assert _app_to_context("Safari") == "default"
        assert _app_to_context("Unknown App") == "default"

    def test_custom_override(self, monkeypatch):
        """Custom Mapping überschreibt Default."""
        import refine.context

        monkeypatch.setattr(refine.context, "_custom_app_contexts_cache", None)
        # Safari ist normalerweise nicht gemappt
        monkeypatch.setenv("PULSESCRIBE_APP_CONTEXTS", '{"Safari": "chat"}')

        assert _app_to_context("Safari") == "chat"

    def test_custom_override_default(self, monkeypatch):
        """Custom Mapping kann Default überschreiben."""
        import refine.context

        monkeypatch.setattr(refine.context, "_custom_app_contexts_cache", None)
        # Slack ist normalerweise "chat"
        monkeypatch.setenv("PULSESCRIBE_APP_CONTEXTS", '{"Slack": "code"}')

        assert _app_to_context("Slack") == "code"

    def test_custom_override_normalizes_context_value(self, monkeypatch):
        """Custom Mapping darf gemischte Großschreibung verwenden."""
        import refine.context

        monkeypatch.setattr(refine.context, "_custom_app_contexts_cache", None)
        monkeypatch.setattr(refine.context, "_custom_app_contexts_signature", None)
        monkeypatch.setenv("PULSESCRIBE_APP_CONTEXTS", '{"Safari": "CHAT"}')

        assert _app_to_context("Safari") == "chat"


class TestDetectContext:
    """Tests für detect_context() - Priority: CLI > ENV > App > Default."""

    def test_cli_override_highest_priority(self, clean_env):
        """CLI-Override hat höchste Priorität."""
        context, app, source = detect_context(override="email")

        assert context == "email"
        assert app is None
        assert source == "CLI"

    def test_cli_override_normalizes_case(self, clean_env):
        """CLI-Override wird auf bekannte Kontexte normalisiert."""
        context, app, source = detect_context(override="EMAIL")

        assert context == "email"
        assert app is None
        assert source == "CLI"

    def test_env_override(self, monkeypatch, clean_env):
        """ENV-Override wenn kein CLI-Override."""
        monkeypatch.setenv("PULSESCRIBE_CONTEXT", "chat")

        context, app, source = detect_context()

        assert context == "chat"
        assert app is None
        assert source == "ENV"

    def test_env_case_insensitive(self, monkeypatch, clean_env):
        """ENV-Wert wird lowercase normalisiert."""
        monkeypatch.setenv("PULSESCRIBE_CONTEXT", "EMAIL")

        context, app, source = detect_context()

        assert context == "email"

    def test_invalid_env_override_falls_back_to_default(self, monkeypatch, clean_env):
        """Ungültige ENV-Kontexte blockieren die Default/Auto-Erkennung nicht."""
        monkeypatch.setenv("PULSESCRIBE_CONTEXT", "not-a-real-context")

        with patch.object(sys, "platform", "linux"):
            context, app, source = detect_context()

        assert context == "default"
        assert app is None
        assert source == "Default"

    def test_cli_beats_env(self, monkeypatch, clean_env):
        """CLI-Override schlägt ENV."""
        monkeypatch.setenv("PULSESCRIBE_CONTEXT", "chat")

        context, app, source = detect_context(override="code")

        assert context == "code"
        assert source == "CLI"

    def test_app_detection_on_macos(self, monkeypatch, clean_env):
        """App-Detection auf macOS wenn kein Override."""
        # Nur auf macOS testen
        if sys.platform != "darwin":
            return

        with patch("refine.context._get_frontmost_app", return_value="Slack"):
            context, app, source = detect_context()

        assert context == "chat"
        assert app == "Slack"
        assert source == "App"

    def test_default_fallback(self, monkeypatch, clean_env):
        """Default wenn nichts gesetzt."""
        # App-Detection mocken als None
        with patch("refine.context._get_frontmost_app", return_value=None):
            context, app, source = detect_context()

        assert context == "default"
        assert app is None
        assert source == "Default"

    def test_app_detection_skipped_non_darwin(self, clean_env):
        """App-Detection wird auf nicht-macOS übersprungen."""
        # Plattform mocken
        with patch.object(sys, "platform", "linux"):
            context, app, source = detect_context()

        assert context == "default"
        assert source == "Default"
