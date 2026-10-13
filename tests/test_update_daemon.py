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


@pytest.mark.parametrize(
    ("idle", "answer", "installs", "boxes"),
    [(True, 6, True, 1), (True, 7, False, 1), (False, 6, False, 1)],
)
def test_windows_fallback_keeps_an_install_path(monkeypatch, idle, answer, installs, boxes):
    module = _load_windows_module()
    daemon = module.PulseScribeWindows(mode="openai", streaming=False, overlay=False)
    shown = []
    monkeypatch.setattr(
        module, "_show_message_box", lambda text, title, flags: shown.append(text) or answer
    )
    daemon._can_install_update = lambda: idle
    from ui.update_window_model import UpdateView

    view = UpdateView(
        "PulseScribe 9.0.0 is ready to install",
        detail="PulseScribe will quit, install the update, and restart.",
        notes="Long notes " * 500,
        primary_label="Restart and Install",
        primary_action="install",
    )
    daemon._update_ui = types.SimpleNamespace(handle=Mock(), current_view=lambda: view)

    daemon._confirm_update_action(view)

    assert len(shown) == boxes
    assert "Long notes" not in shown[0]  # Notes would overflow a message box
    if installs:
        daemon._update_ui.handle.assert_called_once_with("install")
    else:
        daemon._update_ui.handle.assert_not_called()


class _SyncThread:
    """Runs the thread target on start(), so prompt sequences are deterministic."""

    def __init__(self, target, args=(), **_kwargs):
        self._target, self._args = target, args

    def start(self):
        self._target(*self._args)


def _fallback_daemon(monkeypatch, boxes, answers):
    module = _load_windows_module()
    daemon = module.PulseScribeWindows(mode="openai", streaming=False, overlay=False)
    monkeypatch.setattr(module.threading, "Thread", _SyncThread)
    answer_iter = iter(answers)

    def message_box(text, title, flags):
        boxes.append(title)
        return next(answer_iter)

    monkeypatch.setattr(module, "_show_message_box", message_box)
    return module, daemon


def test_windows_fallback_without_action_only_notifies(monkeypatch):
    boxes = []
    _module, daemon = _fallback_daemon(monkeypatch, boxes, [])
    daemon._tray = types.SimpleNamespace(notify=Mock())
    from ui.update_window_model import UpdateView

    daemon._notify_update_fallback(
        UpdateView("Downloading PulseScribe 9.0.0… 5%", primary_action="install", primary_enabled=False)
    )

    daemon._tray.notify.assert_called_once()
    assert boxes == []


def test_windows_fallback_treats_automatic_download_as_status(monkeypatch):
    boxes = []
    _module, daemon = _fallback_daemon(monkeypatch, boxes, [])
    daemon._tray = types.SimpleNamespace(notify=Mock())
    from ui.update_window_model import UpdateView

    daemon._notify_update_fallback(
        UpdateView("PulseScribe 9.0.0 is available", primary_label="Download", primary_action="download")
    )

    daemon._tray.notify.assert_called_once()
    assert boxes == []


def test_windows_fallback_never_runs_on_the_callers_thread(monkeypatch):
    module = _load_windows_module()
    daemon = module.PulseScribeWindows(mode="openai", streaming=False, overlay=False)
    started = []
    monkeypatch.setattr(
        module.threading,
        "Thread",
        lambda **kwargs: types.SimpleNamespace(start=lambda: started.append(kwargs["target"])),
    )
    from ui.update_window_model import UpdateView

    daemon._notify_update_fallback(UpdateView("Up to date"))
    daemon._notify_update_fallback(
        UpdateView("Ready", primary_label="Restart and Install", primary_action="install")
    )

    assert started == [daemon._notify_update_status, daemon._drain_update_prompts]


def test_windows_fallback_revalidates_before_acting(monkeypatch):
    boxes = []
    module, daemon = _fallback_daemon(monkeypatch, boxes, [6])
    from ui.update_window_model import UpdateView

    retry = UpdateView("Update failed", primary_label="Retry", primary_action="retry")
    now_downloading = UpdateView("Downloading…", primary_action="install", primary_enabled=False)
    daemon._update_ui = types.SimpleNamespace(handle=Mock(), current_view=lambda: now_downloading)

    daemon._confirm_update_action(retry)

    daemon._update_ui.handle.assert_not_called()


def test_windows_fallback_shows_the_latest_state_after_the_open_prompt(monkeypatch):
    from ui.update_window_model import UpdateView

    retry = UpdateView("Update failed", primary_label="Retry", primary_action="retry")
    ready = UpdateView("PulseScribe 9.0.0 is ready", primary_label="Restart and Install", primary_action="install")
    current = [retry]
    boxes = []
    module, daemon = _fallback_daemon(monkeypatch, boxes, [])
    daemon._can_install_update = lambda: True
    daemon._update_ui = types.SimpleNamespace(handle=Mock(), current_view=lambda: current[0])

    def message_box(text, title, flags):
        boxes.append(title)
        if len(boxes) == 1:  # An automatic download finishes while Retry is open
            current[0] = ready
            daemon._notify_update_fallback(ready)
            daemon._notify_update_fallback(ready)
        return 7  # No

    monkeypatch.setattr(module, "_show_message_box", message_box)

    daemon._notify_update_fallback(retry)

    assert boxes == ["Update failed", "PulseScribe 9.0.0 is ready"]
    assert daemon._update_prompt_running is False


def test_windows_fallback_action_can_open_the_next_prompt(monkeypatch):
    from ui.update_window_model import UpdateView

    ready = UpdateView("Ready", primary_label="Restart and Install", primary_action="install")
    busy = UpdateView("Ready", notice="Finish the dictation", primary_label="Restart and Install", primary_action="install")
    current = [ready]
    boxes = []
    _module, daemon = _fallback_daemon(monkeypatch, boxes, [6, 7])
    daemon._can_install_update = lambda: True

    def handle(_action):  # Recording started meanwhile: UpdateUI posts a notice
        current[0] = busy
        daemon._notify_update_fallback(busy)

    daemon._update_ui = types.SimpleNamespace(handle=handle, current_view=lambda: current[0])

    daemon._notify_update_fallback(ready)

    assert boxes == ["Ready", "Ready"]


def test_windows_fallback_drops_queued_prompts_after_window_recovery(monkeypatch):
    from ui.update_window_model import UpdateView

    ready = UpdateView("Ready", primary_label="Restart and Install", primary_action="install")
    boxes = []
    _module, daemon = _fallback_daemon(monkeypatch, boxes, [7])
    daemon._update_ui = types.SimpleNamespace(handle=Mock(), current_view=lambda: ready)
    daemon._update_window = types.SimpleNamespace(failed=False)  # Reopened meanwhile

    daemon._notify_update_fallback(ready)

    assert boxes == []
