"""Tests für utils/env.py – Environment Loading."""

import os
from unittest.mock import patch

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

    def test_reload_removes_deleted_api_keys_loaded_from_env(self, tmp_path):
        """Auch API-Keys aus .env werden bei Reload entfernt, wenn sie gelöscht wurden."""
        user_dir = tmp_path / "user"
        user_dir.mkdir()
        env_file = user_dir / ".env"
        env_file.write_text("GROQ_API_KEY=env-key\n")

        with patch("config.USER_CONFIG_DIR", user_dir), patch("utils.env.Path") as mock_path:
            mock_local = mock_path.return_value
            mock_local.exists.return_value = False

            load_environment(override_existing=True)
            assert os.environ.get("GROQ_API_KEY") == "env-key"

            env_file.write_text("")
            load_environment(override_existing=True)

            assert "GROQ_API_KEY" not in os.environ

    def test_reload_preserves_api_keys_that_were_not_loaded_from_env(self, tmp_path):
        """Bereits vorhandene Prozess-ENV-Keys bleiben erhalten, wenn .env sie nie gesetzt hat."""
        user_dir = tmp_path / "user"
        user_dir.mkdir()
        (user_dir / ".env").write_text("")
        os.environ["OPENAI_API_KEY"] = "shell-key"

        try:
            with patch("config.USER_CONFIG_DIR", user_dir), patch("utils.env.Path") as mock_path:
                mock_local = mock_path.return_value
                mock_local.exists.return_value = False

                load_environment(override_existing=True)

            assert os.environ.get("OPENAI_API_KEY") == "shell-key"
        finally:
            os.environ.pop("OPENAI_API_KEY", None)
