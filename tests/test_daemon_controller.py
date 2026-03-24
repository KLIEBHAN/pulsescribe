from __future__ import annotations

import sys
from types import SimpleNamespace

import whisper_platform.daemon as daemon_mod


def test_windows_daemon_is_running_tasklist_fallback_requires_exact_pid(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "psutil", None)
    monkeypatch.setattr(
        daemon_mod.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout='"python.exe","512","Console","1","10,000 K"\r\n',
            stderr="",
        ),
    )

    controller = daemon_mod.WindowsDaemonController()

    assert controller.is_running(12) is False
    assert controller.is_running(512) is True


def test_windows_daemon_is_running_tasklist_fallback_ignores_no_tasks_message(
    monkeypatch,
) -> None:
    monkeypatch.setitem(sys.modules, "psutil", None)
    monkeypatch.setattr(
        daemon_mod.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout="INFO: No tasks are running which match the specified criteria.\r\n",
            stderr="",
        ),
    )

    controller = daemon_mod.WindowsDaemonController()

    assert controller.is_running(9999) is False


def test_windows_daemon_kill_returns_false_when_taskkill_fails(monkeypatch) -> None:
    monkeypatch.setattr(
        daemon_mod.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=128,
            stdout="",
            stderr="ERROR: The process does not exist.",
        ),
    )

    controller = daemon_mod.WindowsDaemonController()

    assert controller.kill(4040, force=True) is False


def test_windows_daemon_kill_returns_true_when_taskkill_succeeds(monkeypatch) -> None:
    monkeypatch.setattr(
        daemon_mod.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout="SUCCESS: The process has been terminated.\r\n",
            stderr="",
        ),
    )

    controller = daemon_mod.WindowsDaemonController()

    assert controller.kill(4040) is True
