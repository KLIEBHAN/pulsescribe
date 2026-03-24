"""Tests for import-time config environment preloading."""

from __future__ import annotations

import importlib
import sys


def test_config_preloads_user_env_before_import_time_constants(
    tmp_path, monkeypatch
) -> None:
    home_dir = tmp_path / "home"
    user_dir = home_dir / ".pulsescribe"
    user_dir.mkdir(parents=True)
    (user_dir / ".env").write_text(
        "PULSESCRIBE_LOCAL_KEEPALIVE_INTERVAL=12.5\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.delenv("PULSESCRIBE_LOCAL_KEEPALIVE_INTERVAL", raising=False)

    original_config = sys.modules.get("config")

    try:
        sys.modules.pop("config", None)
        config = importlib.import_module("config")
        assert config.LOCAL_KEEPALIVE_INTERVAL == 12.5
    finally:
        sys.modules.pop("config", None)
        if original_config is not None:
            sys.modules["config"] = original_config


def test_config_preload_preserves_existing_process_env(tmp_path, monkeypatch) -> None:
    home_dir = tmp_path / "home"
    user_dir = home_dir / ".pulsescribe"
    user_dir.mkdir(parents=True)
    (user_dir / ".env").write_text(
        "PULSESCRIBE_LOCAL_KEEPALIVE_INTERVAL=12.5\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.setenv("PULSESCRIBE_LOCAL_KEEPALIVE_INTERVAL", "45.0")

    original_config = sys.modules.get("config")

    try:
        sys.modules.pop("config", None)
        config = importlib.import_module("config")
        assert config.LOCAL_KEEPALIVE_INTERVAL == 45.0
    finally:
        sys.modules.pop("config", None)
        if original_config is not None:
            sys.modules["config"] = original_config
