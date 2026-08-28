"""Tests für die plattformneutrale Local-Provider-Memory-Logik."""

import os
from unittest.mock import MagicMock

import pytest

from providers.local_memory import (
    LOCAL_MEMORY_ENV_KEYS,
    build_memory_signature,
    normalize_signature_value,
    release_provider_resources,
    sync_env_values,
)


def test_normalize_signature_value_treats_blank_as_none():
    assert normalize_signature_value(None) is None
    assert normalize_signature_value("") is None
    assert normalize_signature_value("   ") is None
    assert normalize_signature_value(" mlx ") == "mlx"


def test_build_memory_signature_shape_and_order():
    env = {
        "PULSESCRIBE_LOCAL_BACKEND": "mlx-whisper",
        "PULSESCRIBE_DEVICE": "mps",
        "PULSESCRIBE_FP16": "true",
        "PULSESCRIBE_LOCAL_COMPUTE_TYPE": "float16",
        "PULSESCRIBE_LOCAL_CPU_THREADS": "4",
        "PULSESCRIBE_LOCAL_NUM_WORKERS": "1",
        "PULSESCRIBE_LIGHTNING_BATCH_SIZE": "8",
        "PULSESCRIBE_LIGHTNING_QUANT": "int8",
    }
    signature = build_memory_signature(mode="local", model="large", getenv=env.get)

    assert signature == (
        "local",
        "mlx-whisper",
        "large",
        "mps",
        "true",
        "float16",
        "4",
        "1",
        "8",
        "int8",
    )


def test_build_memory_signature_normalizes_blank_env_values():
    env = {key: "  " for key in LOCAL_MEMORY_ENV_KEYS}
    signature = build_memory_signature(mode=" deepgram ", model=None, getenv=env.get)

    assert signature == ("deepgram", None, None, *(None,) * len(LOCAL_MEMORY_ENV_KEYS))


def test_sync_env_values_removes_missing_and_sets_present_keys(monkeypatch):
    # Alle Keys vorab per setenv seeden: sync_env_values schreibt direkt in
    # os.environ, der monkeypatch-Teardown restauriert daraus sauber.
    monkeypatch.setenv("PULSESCRIBE_DEVICE", "old-device")
    monkeypatch.setenv("PULSESCRIBE_FP16", "old")
    monkeypatch.setenv("PULSESCRIBE_MODEL", "orig")

    sync_env_values(
        {"PULSESCRIBE_DEVICE": "cpu", "PULSESCRIBE_MODEL": "large"},
        ("PULSESCRIBE_DEVICE", "PULSESCRIBE_FP16", "PULSESCRIBE_MODEL"),
    )

    assert os.environ["PULSESCRIBE_DEVICE"] == "cpu"
    assert "PULSESCRIBE_FP16" not in os.environ
    assert os.environ["PULSESCRIBE_MODEL"] == "large"


def test_release_provider_resources_prefers_clear_model_cache():
    provider = MagicMock(spec=["clear_model_cache", "cleanup"])

    release_provider_resources(provider)

    provider.clear_model_cache.assert_called_once_with()
    provider.cleanup.assert_not_called()


def test_release_provider_resources_falls_back_to_cleanup():
    provider = MagicMock(spec=["cleanup"])

    release_provider_resources(provider)

    provider.cleanup.assert_called_once_with()


def test_release_provider_resources_noop_without_methods():
    release_provider_resources(MagicMock(spec=[]))


def test_release_provider_resources_propagates_errors():
    provider = MagicMock(spec=["clear_model_cache"])
    provider.clear_model_cache.side_effect = RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        release_provider_resources(provider)


def test_daemon_signature_uses_instance_model(monkeypatch):
    from pulsescribe_daemon import PulseScribeDaemon

    monkeypatch.setenv("PULSESCRIBE_LOCAL_BACKEND", "mlx")
    daemon = PulseScribeDaemon(mode="local", model="large")

    assert daemon._local_provider_memory_signature() == (
        "local",
        "mlx",
        "large",
        *(None,) * len(LOCAL_MEMORY_ENV_KEYS),
    )

    daemon.model = "turbo"
    assert daemon._local_provider_memory_signature()[2] == "turbo"


def _load_windows_module():
    """Lädt pulsescribe_windows.py trotz des sys.platform-Guards (wie in
    test_windows_race_conditions._load_windows_module)."""
    import contextlib
    import importlib.util
    import io
    import sys
    from pathlib import Path

    module_name = "_pulsescribe_windows_local_memory_test"
    if module_name in sys.modules:
        return sys.modules[module_name]

    module_path = Path(__file__).resolve().parents[1] / "pulsescribe_windows.py"
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    original_exit = sys.exit
    try:
        sys.exit = lambda _code=0: None
        with contextlib.redirect_stderr(io.StringIO()):
            spec.loader.exec_module(module)
    finally:
        sys.exit = original_exit
    sys.modules[module_name] = module
    return module


def test_windows_signature_reads_model_from_env(monkeypatch):
    import asyncio

    # PulseScribeWindows.__init__ setzt eine Windows-Event-Loop-Policy,
    # die auf macOS nicht existiert (gleicher Workaround wie in
    # test_windows_race_conditions).
    monkeypatch.setattr(
        asyncio,
        "WindowsSelectorEventLoopPolicy",
        asyncio.DefaultEventLoopPolicy,
        raising=False,
    )
    monkeypatch.delenv("PULSESCRIBE_MODEL", raising=False)
    monkeypatch.setenv("PULSESCRIBE_LOCAL_MODEL", "large")
    windows_module = _load_windows_module()
    daemon = windows_module.PulseScribeWindows(mode="deepgram", overlay=False)
    daemon.mode = "local"

    assert daemon._local_provider_memory_signature()[2] == "large"

    # Fallback: ohne LOCAL_MODEL greift PULSESCRIBE_MODEL.
    monkeypatch.delenv("PULSESCRIBE_LOCAL_MODEL", raising=False)
    monkeypatch.setenv("PULSESCRIBE_MODEL", "turbo")
    assert daemon._local_provider_memory_signature()[2] == "turbo"
