"""Tests für utils/env.py – Environment Loading."""

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from utils.env import load_environment


class TestLoadEnvironmentReload:
    """Tests für load_environment() mit override_existing=True (Reload)."""

    def test_reload_removes_deleted_pulsescribe_vars(self, tmp_path, monkeypatch):
        """Entfernte PULSESCRIBE_* Variablen werden bei Reload aus os.environ gelöscht."""
        env_file = tmp_path / ".env"

        # Initial: Variable setzen
        env_file.write_text("PULSESCRIBE_CLIPBOARD_RESTORE=true\n")
        os.environ["PULSESCRIBE_CLIPBOARD_RESTORE"] = "true"

        # Mock USER_CONFIG_DIR und lokale .env
        with patch("utils.env.Path") as mock_path:
            # Lokale .env existiert nicht
            mock_local = mock_path.return_value
            mock_local.exists.return_value = False

            # User .env ist unsere tmp_path Datei
            with patch("config.USER_CONFIG_DIR", tmp_path):
                # Reload mit der Variable
                load_environment(override_existing=True)
                assert os.environ.get("PULSESCRIBE_CLIPBOARD_RESTORE") == "true"

                # Variable aus .env entfernen
                env_file.write_text("")

                # Reload ohne die Variable
                load_environment(override_existing=True)

                # Variable sollte aus os.environ entfernt sein
                assert "PULSESCRIBE_CLIPBOARD_RESTORE" not in os.environ

    def test_reload_preserves_non_pulsescribe_vars(self, tmp_path, monkeypatch):
        """Nicht-PULSESCRIBE Variablen werden bei Reload nicht entfernt."""
        env_file = tmp_path / ".env"
        env_file.write_text("")

        # Setze eine Nicht-PULSESCRIBE Variable
        os.environ["OTHER_VAR"] = "value"

        with patch("config.USER_CONFIG_DIR", tmp_path):
            load_environment(override_existing=True)

        # Variable sollte erhalten bleiben
        assert os.environ.get("OTHER_VAR") == "value"

        # Cleanup
        del os.environ["OTHER_VAR"]

    def test_reload_updates_changed_values(self, tmp_path, monkeypatch):
        """Geänderte Werte werden bei Reload aktualisiert."""
        env_file = tmp_path / ".env"
        env_file.write_text("PULSESCRIBE_MODE=deepgram\n")

        with patch("config.USER_CONFIG_DIR", tmp_path):
            load_environment(override_existing=True)
            assert os.environ.get("PULSESCRIBE_MODE") == "deepgram"

            # Wert ändern
            env_file.write_text("PULSESCRIBE_MODE=local\n")
            load_environment(override_existing=True)

            assert os.environ.get("PULSESCRIBE_MODE") == "local"

        # Cleanup
        if "PULSESCRIBE_MODE" in os.environ:
            del os.environ["PULSESCRIBE_MODE"]
