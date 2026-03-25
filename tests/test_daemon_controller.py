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


def test_macos_daemon_start_ignores_stale_pid_file(tmp_path, monkeypatch) -> None:
    pid_file = tmp_path / "pulsescribe.pid"
    pid_file.write_text("9999\n", encoding="utf-8")

    waitpid_calls: list[tuple[int, int]] = []
    controller = daemon_mod.MacOSDaemonController(pid_file=pid_file)

    monkeypatch.setattr(daemon_mod.os, "fork", lambda: 1234)
    monkeypatch.setattr(
        daemon_mod.os,
        "waitpid",
        lambda pid, options: waitpid_calls.append((pid, options)),
    )

    assert controller.start(["python", "pulsescribe_daemon.py"]) is None
    assert waitpid_calls == [(1234, 0)]
    assert not pid_file.exists()


def test_macos_daemon_start_handles_corrupt_pid_file(tmp_path, monkeypatch) -> None:
    pid_file = tmp_path / "pulsescribe.pid"
    controller = daemon_mod.MacOSDaemonController(pid_file=pid_file)

    monkeypatch.setattr(daemon_mod.os, "fork", lambda: 1234)

    def _waitpid(pid: int, options: int) -> tuple[int, int]:
        pid_file.write_text("not-a-pid\n", encoding="utf-8")
        return pid, options

    monkeypatch.setattr(daemon_mod.os, "waitpid", _waitpid)

    assert controller.start(["python", "pulsescribe_daemon.py"]) is None
