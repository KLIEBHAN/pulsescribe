"""Tests für utils/env.py – Environment Loading."""

import os
from unittest.mock import patch

import config as config_module
import utils.env as env_module
from utils.env import load_environment


class TestLoadEnvironmentReload:
    """Tests für load_environment() mit override_existing=True (Reload)."""

    def test_reload_removes_deleted_pulsescribe_vars(self, tmp_path, monkeypatch):
        """Entfernte PULSESCRIBE_* Variablen werden bei Reload aus os.environ gelöscht."""
        env_file = tmp_path / ".env"

        # Initial: Variable setzen
        env_file.write_text("PULSESCRIBE_CLIPBOARD_RESTORE=true\n", encoding="utf-8")
        os.environ["PULSESCRIBE_CLIPBOARD_RESTORE"] = "true"

        local_env = tmp_path / "missing-local.env"
        with patch("config.USER_CONFIG_DIR", tmp_path), patch(
            "utils.env._get_local_env_path",
            return_value=local_env,
        ):
            # Reload mit der Variable
            load_environment(override_existing=True)
            assert os.environ.get("PULSESCRIBE_CLIPBOARD_RESTORE") == "true"

            # Variable aus .env entfernen
            env_file.write_text("", encoding="utf-8")

            # Reload ohne die Variable
            load_environment(override_existing=True)

            # Variable sollte aus os.environ entfernt sein
            assert "PULSESCRIBE_CLIPBOARD_RESTORE" not in os.environ

    def test_reload_preserves_non_pulsescribe_vars(self, tmp_path, monkeypatch):
        """Nicht-PULSESCRIBE Variablen werden bei Reload nicht entfernt."""
        env_file = tmp_path / ".env"
        env_file.write_text("", encoding="utf-8")

        # Setze eine Nicht-PULSESCRIBE Variable
        os.environ["OTHER_VAR"] = "value"

        with patch("config.USER_CONFIG_DIR", tmp_path), patch(
            "utils.env._get_local_env_path",
            return_value=tmp_path / "missing-local.env",
        ):
            load_environment(override_existing=True)

        # Variable sollte erhalten bleiben
        assert os.environ.get("OTHER_VAR") == "value"

        # Cleanup
        del os.environ["OTHER_VAR"]

    def test_reload_updates_changed_values(self, tmp_path, monkeypatch):
        """Geänderte Werte werden bei Reload aktualisiert."""
        env_file = tmp_path / ".env"
        env_file.write_text("PULSESCRIBE_MODE=deepgram\n", encoding="utf-8")

        with patch("config.USER_CONFIG_DIR", tmp_path), patch(
            "utils.env._get_local_env_path",
            return_value=tmp_path / "missing-local.env",
        ):
            load_environment(override_existing=True)
            assert os.environ.get("PULSESCRIBE_MODE") == "deepgram"

            # Wert ändern
            env_file.write_text("PULSESCRIBE_MODE=local\n", encoding="utf-8")
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
        env_file.write_text("GROQ_API_KEY=env-key\n", encoding="utf-8")

        with patch("config.USER_CONFIG_DIR", user_dir), patch(
            "utils.env._get_local_env_path",
            return_value=tmp_path / "missing-local.env",
        ):
            load_environment(override_existing=True)
            assert os.environ.get("GROQ_API_KEY") == "env-key"

            env_file.write_text("", encoding="utf-8")
            load_environment(override_existing=True)

            assert "GROQ_API_KEY" not in os.environ

    def test_reload_preserves_api_keys_that_were_not_loaded_from_env(self, tmp_path):
        """Bereits vorhandene Prozess-ENV-Keys bleiben erhalten, wenn .env sie nie gesetzt hat."""
        user_dir = tmp_path / "user"
        user_dir.mkdir()
        (user_dir / ".env").write_text("", encoding="utf-8")
        os.environ["OPENAI_API_KEY"] = "shell-key"

        try:
            with patch("config.USER_CONFIG_DIR", user_dir), patch(
                "utils.env._get_local_env_path",
                return_value=tmp_path / "missing-local.env",
            ):
                load_environment(override_existing=True)

            assert os.environ.get("OPENAI_API_KEY") == "shell-key"
        finally:
            os.environ.pop("OPENAI_API_KEY", None)

    def test_reload_uses_project_local_env_instead_of_current_working_directory(
        self, tmp_path, monkeypatch
    ):
        """Lokale `.env` wird relativ zum Projekt und nicht relativ zum cwd geladen."""
        repo_env = tmp_path / "repo.env"
        repo_env.write_text("PULSESCRIBE_MODE=local\n", encoding="utf-8")

        cwd = tmp_path / "cwd"
        cwd.mkdir()
        (cwd / ".env").write_text("PULSESCRIBE_MODE=deepgram\n", encoding="utf-8")
        monkeypatch.chdir(cwd)
        os.environ.pop("PULSESCRIBE_MODE", None)

        try:
            with patch("config.USER_CONFIG_DIR", tmp_path / "missing-user-dir"), patch(
                "utils.env._get_local_env_path",
                return_value=repo_env,
            ):
                load_environment(override_existing=True)

            assert os.environ.get("PULSESCRIBE_MODE") == "local"
        finally:
            os.environ.pop("PULSESCRIBE_MODE", None)

    def test_reload_removes_deleted_vars_that_were_preloaded_by_config(
        self, tmp_path, monkeypatch
    ):
        """Import-time config preload must not leave deleted `.env` values behind."""
        user_dir = tmp_path / ".pulsescribe"
        user_dir.mkdir()
        env_file = user_dir / ".env"
        env_file.write_text("PULSESCRIBE_TEST_PRELOADED=one\n", encoding="utf-8")

        monkeypatch.setattr(config_module.Path, "home", lambda: tmp_path)
        monkeypatch.setattr(
            env_module,
            "_get_local_env_path",
            lambda: tmp_path / "missing-local.env",
        )

        env_module._loaded_env_values = {}
        os.environ.pop("PULSESCRIBE_TEST_PRELOADED", None)

        try:
            config_module._preload_env_for_import_time_config()
            assert os.environ.get("PULSESCRIBE_TEST_PRELOADED") == "one"

            env_file.write_text("", encoding="utf-8")
            with patch("config.USER_CONFIG_DIR", user_dir):
                load_environment(override_existing=True)

            assert "PULSESCRIBE_TEST_PRELOADED" not in os.environ
        finally:
            os.environ.pop("PULSESCRIBE_TEST_PRELOADED", None)
            env_module._loaded_env_values = {}
