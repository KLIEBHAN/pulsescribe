"""Tests for import-time config environment preloading."""

from __future__ import annotations

import importlib
import sys
from types import SimpleNamespace


def _restore_input_device_cache(config_module, original_cache):
    config_module._cached_input_device = original_cache


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
        _restore_input_device_cache(config_module, original_cache)



def test_get_input_device_non_windows_prefers_named_microphone(monkeypatch) -> None:
    import config as config_module

    original_cache = config_module._cached_input_device
    config_module._cached_input_device = None

    fake_sounddevice = SimpleNamespace(
        default=SimpleNamespace(device=[-1, None]),
        query_devices=lambda: [
            {
                "name": "Line In",
                "max_input_channels": 1,
                "default_samplerate": 44_100,
            },
            {
                "name": "USB Microphone",
                "max_input_channels": 1,
                "default_samplerate": 48_000,
            },
        ],
    )
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setitem(sys.modules, "sounddevice", fake_sounddevice)

    try:
        assert config_module.get_input_device() == (1, 48_000)
    finally:
        _restore_input_device_cache(config_module, original_cache)



def test_get_input_device_windows_prefers_working_mic_array(monkeypatch) -> None:
    import config as config_module

    original_cache = config_module._cached_input_device
    config_module._cached_input_device = None
    attempts: list[tuple[int | None, int]] = []

    class _ProbeStream:
        def __init__(self, **kwargs):
            self.device = kwargs["device"]
            self.samplerate = kwargs["samplerate"]
            self._callback = kwargs["callback"]

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def start(self) -> None:
            attempts.append((self.device, self.samplerate))
            if self.device != 1:
                raise RuntimeError("device unavailable")
            self._callback(object(), 0, None, None)

    fake_sounddevice = SimpleNamespace(
        default=SimpleNamespace(device=[-1, None]),
        query_devices=lambda: [
            {
                "name": "USB Speaker",
                "max_input_channels": 2,
                "default_samplerate": 44_100,
            },
            {
                "name": "Mic Array (Realtek)",
                "max_input_channels": 2,
                "default_samplerate": 48_000,
            },
            {
                "name": "Microphone (USB)",
                "max_input_channels": 1,
                "default_samplerate": 16_000,
            },
        ],
        InputStream=lambda **kwargs: _ProbeStream(**kwargs),
    )
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "sounddevice", fake_sounddevice)

    try:
        assert config_module.get_input_device() == (1, 48_000)
        assert attempts == [(1, 48_000)]
    finally:
        _restore_input_device_cache(config_module, original_cache)



def test_get_input_device_windows_falls_back_to_working_microphone(monkeypatch) -> None:
    import config as config_module

    original_cache = config_module._cached_input_device
    config_module._cached_input_device = None
    attempts: list[tuple[int | None, int]] = []

    class _ProbeStream:
        def __init__(self, **kwargs):
            self.device = kwargs["device"]
            self.samplerate = kwargs["samplerate"]
            self._callback = kwargs["callback"]

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def start(self) -> None:
            attempts.append((self.device, self.samplerate))
            if self.device == 1:
                raise RuntimeError("mic array unavailable")
            if self.device == 2:
                self._callback(object(), 0, None, None)
                return
            raise AssertionError("speaker should be skipped")

    fake_sounddevice = SimpleNamespace(
        default=SimpleNamespace(device=[-1, None]),
        query_devices=lambda: [
            {
                "name": "USB Speaker",
                "max_input_channels": 2,
                "default_samplerate": 44_100,
            },
            {
                "name": "Mic Array (Realtek)",
                "max_input_channels": 2,
                "default_samplerate": 48_000,
            },
            {
                "name": "Microphone (USB)",
                "max_input_channels": 1,
                "default_samplerate": 16_000,
            },
        ],
        InputStream=lambda **kwargs: _ProbeStream(**kwargs),
    )
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "sounddevice", fake_sounddevice)

    try:
        assert config_module.get_input_device() == (2, 16_000)
        assert attempts[-1] == (2, 16_000)
        assert all(device != 0 for device, _samplerate in attempts)
    finally:
        _restore_input_device_cache(config_module, original_cache)



def test_get_input_device_windows_uses_non_output_capture_before_final_fallback(
    monkeypatch,
) -> None:
    import config as config_module

    original_cache = config_module._cached_input_device
    config_module._cached_input_device = None
    attempts: list[tuple[int | None, int]] = []

    class _ProbeStream:
        def __init__(self, **kwargs):
            self.device = kwargs["device"]
            self.samplerate = kwargs["samplerate"]
            self._callback = kwargs["callback"]

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def start(self) -> None:
            attempts.append((self.device, self.samplerate))
            if self.device == 2:
                self._callback(object(), 0, None, None)
                return
            raise AssertionError("Only non-output capture device should be probed")

    fake_sounddevice = SimpleNamespace(
        default=SimpleNamespace(device=[-1, None]),
        query_devices=lambda: [
            {
                "name": "USB Speaker",
                "max_input_channels": 2,
                "default_samplerate": 44_100,
            },
            {
                "name": "Monitor Mix",
                "max_input_channels": 2,
                "default_samplerate": 48_000,
            },
            {
                "name": "Studio Capture",
                "max_input_channels": 1,
                "default_samplerate": 16_000,
            },
        ],
        InputStream=lambda **kwargs: _ProbeStream(**kwargs),
    )
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "sounddevice", fake_sounddevice)

    try:
        assert config_module.get_input_device() == (2, 16_000)
        assert attempts == [(2, 16_000)]
    finally:
        _restore_input_device_cache(config_module, original_cache)



def test_get_input_device_windows_fallback_result_is_not_cached(monkeypatch) -> None:
    import config as config_module

    original_cache = config_module._cached_input_device
    config_module._cached_input_device = None
    attempts: list[tuple[int | None, int]] = []
    query_calls = 0

    class _ProbeStream:
        def __init__(self, **kwargs):
            self.device = kwargs["device"]
            self.samplerate = kwargs["samplerate"]

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def start(self) -> None:
            attempts.append((self.device, self.samplerate))
            raise RuntimeError("device unavailable")

    def _query_devices(device: int | None = None):
        nonlocal query_calls
        query_calls += 1
        assert device is None
        return [
            {
                "name": "Line In",
                "max_input_channels": 1,
                "default_samplerate": 44_100,
            },
            {
                "name": "Studio Capture",
                "max_input_channels": 1,
                "default_samplerate": 48_000,
            },
        ]

    fake_sounddevice = SimpleNamespace(
        default=SimpleNamespace(device=[-1, None]),
        query_devices=_query_devices,
        InputStream=lambda **kwargs: _ProbeStream(**kwargs),
    )
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "sounddevice", fake_sounddevice)

    try:
        assert config_module.get_input_device() == (0, 44_100)
        assert config_module.get_input_device() == (0, 44_100)
        assert query_calls == 2
        assert attempts == [
            (0, 44_100),
            (1, 48_000),
            (0, 44_100),
            (1, 48_000),
        ]
    finally:
        _restore_input_device_cache(config_module, original_cache)
