"""Unloading the local model after PULSESCRIBE_LOCAL_IDLE_UNLOAD_MINUTES of idle time."""

import threading
from unittest.mock import patch

import pytest

import pulsescribe_daemon
from pulsescribe_daemon import PulseScribeDaemon
from utils.state import AppState


class _FakeTimer:
    """Records the scheduled callback instead of waiting minutes."""

    created: list["_FakeTimer"] = []

    def __init__(self, interval, function, args=()):
        self.interval, self.function, self.args = interval, function, args
        self.cancelled = False
        self.daemon = False
        self.name = ""
        _FakeTimer.created.append(self)

    def start(self):
        pass

    def cancel(self):
        self.cancelled = True

    def fire(self):
        self.function(*self.args)


@pytest.fixture
def daemon(monkeypatch):
    _FakeTimer.created = []
    monkeypatch.setattr(pulsescribe_daemon.threading, "Timer", _FakeTimer)
    monkeypatch.setenv("PULSESCRIBE_LOCAL_IDLE_UNLOAD_MINUTES", "15")
    d = PulseScribeDaemon(mode="local")
    with d._local_warm_lock:
        d._local_warm_generation = 3
        d._local_preload_signature = d._local_provider_memory_signature()
    d._local_preload_complete.set()
    monkeypatch.setattr(d, "_do_ui_update", lambda *_a: None)
    monkeypatch.setattr(threading, "current_thread", lambda: type("T", (), {"name": "MainThread"})())
    return d


def test_idle_state_arms_timer_and_firing_releases_the_model(daemon):
    with patch.object(daemon, "_release_local_provider_model_cache") as release:
        daemon._update_state(AppState.DONE, "Hallo")
        timer = _FakeTimer.created[-1]
        assert timer.interval == 15 * 60
        daemon._update_state(AppState.IDLE)
        assert timer.cancelled  # every idle transition restarts the countdown
        _FakeTimer.created[-1].fire()

    release.assert_called_once()
    assert not daemon._local_preload_complete.is_set()
    assert daemon._local_preload_signature is None  # next recording preloads again


def test_recording_cancels_the_countdown(daemon):
    daemon._update_state(AppState.IDLE)
    timer = _FakeTimer.created[-1]
    daemon._update_state(AppState.LISTENING)
    assert timer.cancelled
    assert len(_FakeTimer.created) == 1


def test_stale_or_busy_timer_keeps_the_model(daemon):
    with patch.object(daemon, "_release_local_provider_model_cache") as release:
        daemon._update_state(AppState.IDLE)
        timer = _FakeTimer.created[-1]
        with daemon._local_warm_lock:
            daemon._local_warm_generation += 1  # settings reload reloaded the model
        timer.fire()
        daemon._update_state(AppState.DONE, "x")
        busy_timer = _FakeTimer.created[-1]
        daemon._update_state(AppState.TRANSCRIBING)
        busy_timer.fire()
    release.assert_not_called()


@pytest.mark.parametrize("minutes,mode", [("0", "local"), ("", "local"), ("15", "deepgram")])
def test_no_timer_when_disabled_or_not_local(daemon, monkeypatch, minutes, mode):
    monkeypatch.setenv("PULSESCRIBE_LOCAL_IDLE_UNLOAD_MINUTES", minutes)
    daemon.mode = mode
    daemon._update_state(AppState.IDLE)
    assert _FakeTimer.created == []


def test_listening_preloads_so_an_unloaded_model_loads_while_speaking(daemon, monkeypatch):
    calls = []
    monkeypatch.setattr(daemon, "_preload_local_model_async", lambda: calls.append(True))
    monkeypatch.setattr(daemon, "_recording_worker", lambda *a: None)
    monkeypatch.setattr(daemon, "_start_result_polling", lambda: None)
    monkeypatch.setattr(pulsescribe_daemon, "start_macos_latency_run", lambda **k: type("R", (), {"mark": lambda *a: None, "finish": lambda *a: None})())
    daemon._start_recording_locked()
    assert calls == [True]
