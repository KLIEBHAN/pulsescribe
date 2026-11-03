"""Tests for re-reading PortAudio devices after macOS hardware changes."""

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

import audio.device_refresh as refresh


class _PortAudioError(Exception):
    pass


@pytest.fixture
def sd():
    calls = {"terminate": 0, "initialize": 0}
    fake = SimpleNamespace(
        PortAudioError=_PortAudioError,
        _terminate=lambda: calls.__setitem__("terminate", calls["terminate"] + 1),
        _initialize=lambda: calls.__setitem__("initialize", calls["initialize"] + 1),
    )
    fake.calls = calls
    return fake


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    refresh._open_streams.clear()
    monkeypatch.setattr(refresh, "_known_fingerprint", None)
    resets = []
    monkeypatch.setattr("config.reset_input_device_cache", lambda: resets.append(1))
    yield resets
    refresh._open_streams.clear()


def _set_devices(monkeypatch, fingerprint):
    monkeypatch.setattr(refresh, "device_fingerprint", lambda: fingerprint)


def test_reinitializes_once_per_hardware_change(monkeypatch, sd, _clean_state):
    _set_devices(monkeypatch, ((1, 2), 1))
    assert refresh.refresh_if_devices_changed(sd) is True  # first sight: baseline
    assert refresh.refresh_if_devices_changed(sd) is False
    _set_devices(monkeypatch, ((1, 2, 7), 7))  # dock microphone appears and is default
    assert refresh.refresh_if_devices_changed(sd) is True
    assert sd.calls == {"terminate": 2, "initialize": 2}
    assert len(_clean_state) == 2  # cached device choice is dropped each time


def test_open_stream_blocks_refresh_until_clean_release(monkeypatch, sd):
    _set_devices(monkeypatch, ((1,), 1))
    stream = refresh.open_input_stream(sd, object)
    _set_devices(monkeypatch, ((1, 7), 7))

    assert refresh.refresh_if_devices_changed(sd) is False
    refresh.release_input_stream(stream)
    assert refresh.refresh_if_devices_changed(sd) is True


def test_portaudio_error_reinitializes_and_retries_once(monkeypatch, sd):
    _set_devices(monkeypatch, ((1,), 1))
    refresh.refresh_if_devices_changed(sd)
    attempts = []

    def create():
        attempts.append(1)
        if len(attempts) == 1:
            raise _PortAudioError("Internal PortAudio error [PaErrorCode -9986]")
        return object()

    stream = refresh.open_input_stream(sd, create)

    assert len(attempts) == 2
    assert sd.calls["initialize"] == 2
    assert id(stream) in refresh._open_streams


def test_other_errors_and_repeated_portaudio_errors_propagate(monkeypatch, sd):
    _set_devices(monkeypatch, ((1,), 1))

    def broken():
        raise ValueError("bad argument")

    with pytest.raises(ValueError):
        refresh.open_input_stream(sd, broken)

    def always_failing():
        raise _PortAudioError("still broken")

    with pytest.raises(_PortAudioError):
        refresh.open_input_stream(sd, always_failing)
    assert not refresh._open_streams


def test_without_coreaudio_nothing_is_reinitialized(monkeypatch, sd):
    _set_devices(monkeypatch, None)

    def failing():
        raise _PortAudioError("no retry outside macOS")

    assert refresh.refresh_if_devices_changed(sd) is False
    with pytest.raises(_PortAudioError):
        refresh.open_input_stream(sd, failing)
    assert sd.calls == {"terminate": 0, "initialize": 0}


@pytest.mark.skipif(sys.platform != "darwin", reason="CoreAudio only on macOS")
def test_coreaudio_fingerprint_reads_device_ids():
    fingerprint = refresh.device_fingerprint()
    assert fingerprint is not None
    device_ids, default_input = fingerprint
    assert all(isinstance(i, int) for i in device_ids)
    assert isinstance(default_input, int)


def test_microphone_list_refreshes_devices_first(monkeypatch):
    from ui import microphone

    calls = []
    fake_sd = SimpleNamespace(
        query_devices=lambda: [{"name": "Dock Mic", "max_input_channels": 1}]
    )
    monkeypatch.setitem(sys.modules, "sounddevice", fake_sd)
    monkeypatch.setattr(refresh, "refresh_if_devices_changed", lambda sd: calls.append(sd))

    options = microphone.get_microphone_options()

    assert calls == [fake_sd]
    assert ("Dock Mic", "Dock Mic") in options
