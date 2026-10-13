import sys
import threading
import types
from unittest.mock import Mock

import pytest

from tests.test_windows_race_conditions import _load_windows_module
from utils.state import AppState


def test_windows_install_reserves_recording_start(monkeypatch):
    module = _load_windows_module()
    daemon = module.PulseScribeWindows(mode="openai", streaming=False, overlay=False)
    starts = []
    workers = []

    def launch():
        worker = threading.Thread(
            target=lambda: starts.append(daemon._start_recording())
        )
        workers.append(worker)
        worker.start()

    daemon._updater = types.SimpleNamespace(install=launch)
    daemon._quit = Mock()
    daemon._start_latency_run = Mock()
    assert daemon._install_update_if_idle()
    workers[0].join(timeout=2)
    assert not workers[0].is_alive()
    assert starts == [False]
    daemon._start_latency_run.assert_not_called()
    daemon._quit.assert_called_once()


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS daemon")
def test_mac_install_reserves_recording_start(monkeypatch):
    from pulsescribe_daemon import PulseScribeDaemon

    daemon = PulseScribeDaemon(mode="openai")
    workers = []

    def launch():
        worker = threading.Thread(target=daemon._start_recording)
        workers.append(worker)
        worker.start()

    daemon._updater = types.SimpleNamespace(install=launch)
    daemon._start_recording_locked = Mock()
    daemon.cleanup = Mock()
    terminate = Mock()
    monkeypatch.setitem(
        sys.modules,
        "AppKit",
        types.SimpleNamespace(
            NSApplication=types.SimpleNamespace(
                sharedApplication=lambda: types.SimpleNamespace(terminate_=terminate)
            )
        ),
    )
    assert daemon._install_update_if_idle()
    workers[0].join(timeout=2)
    assert not workers[0].is_alive()
    daemon._start_recording_locked.assert_not_called()
    daemon.cleanup.assert_called_once()
    terminate.assert_called_once()


def test_windows_busy_state_does_not_launch_or_quit():
    module = _load_windows_module()
    daemon = module.PulseScribeWindows(mode="openai", streaming=False, overlay=False)
    daemon._state = AppState.RECORDING
    daemon._updater = types.SimpleNamespace(install=Mock())
    daemon._quit = Mock()
    assert not daemon._install_update_if_idle()
    daemon._updater.install.assert_not_called()
    daemon._quit.assert_not_called()
