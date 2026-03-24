"""Tests for import-time config environment preloading."""

from __future__ import annotations

import importlib
import sys
from types import SimpleNamespace


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


def test_get_input_device_retries_after_initial_probe_failure(monkeypatch) -> None:
    import config as config_module

    original_cache = config_module._cached_input_device
    config_module._cached_input_device = None
    query_calls = 0

    def _query_devices(device: int | None = None):
        nonlocal query_calls
        query_calls += 1
        if query_calls == 1:
            raise RuntimeError("device temporarily unavailable")
        assert device == 0
        return {"default_samplerate": 48_000}

    fake_sounddevice = SimpleNamespace(
        default=SimpleNamespace(device=[0, None]),
        query_devices=_query_devices,
    )
    monkeypatch.setitem(sys.modules, "sounddevice", fake_sounddevice)

    try:
        assert config_module.get_input_device() == (None, config_module.WHISPER_SAMPLE_RATE)
        assert config_module.get_input_device() == (None, 48_000)
        assert query_calls == 2
    finally:
        config_module._cached_input_device = original_cache


def test_get_input_device_keeps_caching_successful_probe(monkeypatch) -> None:
    import config as config_module

    original_cache = config_module._cached_input_device
    config_module._cached_input_device = None
    query_calls = 0

    def _query_devices(device: int | None = None):
        nonlocal query_calls
        query_calls += 1
        assert device == 3
        return {"default_samplerate": 44_100}

    fake_sounddevice = SimpleNamespace(
        default=SimpleNamespace(device=[3, None]),
        query_devices=_query_devices,
    )
    monkeypatch.setitem(sys.modules, "sounddevice", fake_sounddevice)

    try:
        assert config_module.get_input_device() == (None, 44_100)
        assert config_module.get_input_device() == (None, 44_100)
        assert query_calls == 1
    finally:
        config_module._cached_input_device = original_cache
