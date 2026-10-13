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


def test_windows_update_states_reach_tray_and_window(monkeypatch, tmp_path):
    module = _load_windows_module()
    daemon = module.PulseScribeWindows(mode="openai", streaming=False, overlay=False)
    monkeypatch.setattr(
        "utils.update_install.installed_target", lambda: tmp_path / "PulseScribe.exe"
    )
    daemon._tray = types.SimpleNamespace(update_menu=Mock(), notify=Mock())

    daemon._setup_updates()
    shown = []
    daemon._update_window.show = lambda view, *, activate: shown.append((view, activate))
    from utils.update_flow import UpdateState
    from utils.updates import UpdateOffer

    offer = UpdateOffer("9.0.0", "Notes", "PulseScribe-Setup-9.0.0.exe", 1, "a" * 64)
    daemon._updater.publish(UpdateState("1.0.0", status="downloading", offer=offer))

    daemon._tray.update_menu.assert_called_once()
    assert shown and shown[0][1] is False
    assert shown[0][0].headline.startswith("Downloading PulseScribe 9.0.0")


def test_windows_quit_closes_the_update_window_before_installing():
    module = _load_windows_module()
    daemon = module.PulseScribeWindows(mode="openai", streaming=False, overlay=False)
    daemon._update_window = types.SimpleNamespace(close=Mock())

    daemon._quit()

    daemon._update_window.close.assert_called_once()


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS daemon")
def test_mac_menu_opens_update_window_and_states_reach_it(monkeypatch, tmp_path):
    from pulsescribe_daemon import PulseScribeDaemon
    from ui.update_window_mac import MacUpdateWindow
    from utils.update_flow import UpdateState

    daemon = PulseScribeDaemon(mode="openai")
    monkeypatch.setattr(
        "utils.update_install.installed_target", lambda: tmp_path / "PulseScribe.app"
    )
    shown = []
    monkeypatch.setattr(
        MacUpdateWindow, "show", lambda self, view, *, activate: shown.append((view, activate))
    )
    menu = types.SimpleNamespace(set_update_callback=Mock(), update_update_state=Mock())
    daemon._menubar = menu
    daemon._call_on_main = lambda fn: fn()

    daemon._setup_updates()
    menu_action = menu.set_update_callback.call_args.args[0]
    daemon._updater.check = Mock()
    menu_action()
    daemon._updater.publish(UpdateState("1.0.0", status="checking", manual=True))

    assert shown[0][1] is True
    assert shown[0][0].headline == "Checking for updates…"
    daemon._updater.check.assert_called_once_with(manual=True)
    menu.update_update_state.assert_called_once()
