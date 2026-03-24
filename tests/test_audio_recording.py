"""Tests fuer audio.recording."""

from __future__ import annotations

import sys
from types import SimpleNamespace
from typing import Literal

import numpy as np
import pytest


class _DummyInputStream:
    def __init__(self, sink: dict, **kwargs):
        sink.update(kwargs)
        self._callback = kwargs["callback"]
        self.active = False

    def start(self) -> None:
        self.active = True

    def stop(self) -> None:
        self.active = False

    def close(self) -> None:
        self.active = False

    def __enter__(self):
        self.active = True
        self._callback(np.array([[0.1], [0.2]], dtype=np.float32), 2, None, None)
        return self

    def __exit__(self, exc_type, exc, tb) -> Literal[False]:
        self.active = False
        return False


def test_audio_recorder_start_uses_resolved_input_device(monkeypatch):
    import audio.recording as recording

    captured: dict = {}
    fake_sd = SimpleNamespace(
        InputStream=lambda **kwargs: _DummyInputStream(captured, **kwargs)
    )

    monkeypatch.setattr(recording, "get_input_device", lambda: (7, 48_000))
    monkeypatch.setattr(recording, "_play_sound", lambda _name: None)
    monkeypatch.setitem(sys.modules, "sounddevice", fake_sd)

    recorder = recording.AudioRecorder()
    recorder.start(play_ready_sound=False)

    assert captured["device"] == 7
    assert captured["samplerate"] == 48_000
    assert recorder._active_sample_rate == 48_000


def test_record_audio_uses_resolved_input_device_and_sample_rate(
    monkeypatch, tmp_path
):
    import audio.recording as recording

    captured_stream: dict = {}
    write_call: dict = {}
    fake_sd = SimpleNamespace(
        InputStream=lambda **kwargs: _DummyInputStream(captured_stream, **kwargs)
    )
    fake_sf = SimpleNamespace(
        write=lambda path, audio_data, sample_rate: write_call.update(
            path=path,
            audio_data=audio_data,
            sample_rate=sample_rate,
        )
    )
    inputs = iter(["", ""])

    monkeypatch.setattr(recording, "get_input_device", lambda: (5, 44_100))
    monkeypatch.setattr(recording, "_play_sound", lambda _name: None)
    monkeypatch.setattr(recording, "_log", lambda _message: None)
    monkeypatch.setattr(recording.tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr("builtins.input", lambda: next(inputs))
    monkeypatch.setitem(sys.modules, "sounddevice", fake_sd)
    monkeypatch.setitem(sys.modules, "soundfile", fake_sf)

    output_path = recording.record_audio()

    assert captured_stream["device"] == 5
    assert captured_stream["samplerate"] == 44_100
    assert write_call["sample_rate"] == 44_100
    assert output_path == tmp_path / recording.TEMP_RECORDING_FILENAME


def test_audio_recorder_start_cleans_up_when_stream_start_fails(monkeypatch):
    import audio.recording as recording

    state = {"closed": 0}

    class _BrokenInputStream:
        def __init__(self, **_kwargs):
            self.active = False

        def start(self) -> None:
            raise RuntimeError("device busy")

        def close(self) -> None:
            state["closed"] += 1

    fake_sd = SimpleNamespace(
        InputStream=lambda **kwargs: _BrokenInputStream(**kwargs)
    )

    monkeypatch.setattr(recording, "get_input_device", lambda: (7, 48_000))
    monkeypatch.setitem(sys.modules, "sounddevice", fake_sd)

    recorder = recording.AudioRecorder()

    with pytest.raises(RuntimeError, match="device busy"):
        recorder.start(play_ready_sound=False)

    assert recorder._stream is None
    assert recorder.wait_for_stop(timeout=0)
    assert state["closed"] == 1
