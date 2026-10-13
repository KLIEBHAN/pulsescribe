import io
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from ui.update_window_model import PROGRESS_INDETERMINATE, UpdateView
from ui.update_window_windows import WindowsUpdateWindow

ROOT = Path(__file__).resolve().parents[1]


class FakeProcess:
    def __init__(self, stdout_lines=()):
        self.stdin = io.BytesIO()
        self.stdout = list(stdout_lines)
        self.returncode = None
        self.waited = False
        self.terminated = False

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        self.waited = True
        self.returncode = 0
        return 0

    def terminate(self):
        self.terminated = True

    def messages(self):
        return [json.loads(line) for line in self.stdin.getvalue().splitlines()]


def make_window(processes, actions=None):
    launches = []

    def launch(activate):
        launches.append(activate)
        return processes[len(launches) - 1]

    actions = [] if actions is None else actions
    return WindowsUpdateWindow(launch, actions.append), launches, actions


def wait_for(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        time.sleep(0.01)
    return predicate()


def test_show_launches_once_and_forwards_views():
    process = FakeProcess()
    window, launches, _actions = make_window([process])

    window.show(UpdateView("one"), activate=False)
    window.render(UpdateView("two"))
    window.show(UpdateView("three"), activate=True)

    assert launches == [False]
    messages = process.messages()
    assert [m["view"]["headline"] for m in messages] == ["one", "two", "three"]
    assert [m["activate"] for m in messages] == [False, None, True]


def test_render_without_window_starts_nothing():
    window, launches, _actions = make_window([])

    window.render(UpdateView("ignored"))

    assert launches == []


def test_closed_window_process_is_relaunched_on_show():
    first, second = FakeProcess(), FakeProcess()
    window, launches, _actions = make_window([first, second])
    window.show(UpdateView("one"), activate=False)
    first.returncode = 0  # User closed the window

    window.render(UpdateView("lost"))
    window.show(UpdateView("again"), activate=True)

    assert launches == [False, True]
    assert [m["view"]["headline"] for m in second.messages()] == ["again"]


def test_button_actions_are_forwarded_and_noise_is_ignored():
    process = FakeProcess(
        [
            b"qt.qpa: some warning\n",
            b'PULSESCRIBE-UPDATE {"action": "ready"}\n',
            b'PULSESCRIBE-UPDATE {"action": "install"}\n',
            b"PULSESCRIBE-UPDATE not-json\n",
            b'PULSESCRIBE-UPDATE {"action": "close"}\n',
        ]
    )
    window, _launches, actions = make_window([process])

    window.show(UpdateView("ready"), activate=True)

    assert wait_for(lambda: actions == ["install", "close"])


def test_window_that_exits_before_ready_is_reported_unavailable():
    window, _launches, actions = make_window([FakeProcess([b"Traceback ...\n"])])

    window.show(UpdateView("ready"), activate=True)

    assert wait_for(lambda: actions == ["unavailable"])


def test_closing_a_starting_window_is_not_a_failure():
    started = threading.Event()

    class SlowStdout:
        def __iter__(self):
            started.wait(2)
            return iter([])

    process = FakeProcess()
    process.stdout = SlowStdout()
    window, _launches, actions = make_window([process])
    window.show(UpdateView("ready"), activate=False)

    window.close()
    started.set()
    time.sleep(0.1)

    assert actions == []


def test_close_ends_the_process_for_the_installer():
    process = FakeProcess()
    window, _launches, _actions = make_window([process])
    window.show(UpdateView("ready"), activate=False)

    window.close()

    assert process.stdin.closed
    assert process.waited
    window.render(UpdateView("after close"))  # No process left to write to


@pytest.mark.skipif(sys.platform == "win32", reason="offscreen Qt check runs on CI macOS")
def test_qt_window_process_renders_views_and_exits_with_the_daemon():
    pytest.importorskip("PySide6")
    process = subprocess.Popen(
        [sys.executable, str(ROOT / "ui" / "update_window_qt.py")],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(ROOT),
        env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
    )
    actions = []
    window = WindowsUpdateWindow(lambda _activate: process, actions.append)
    try:
        window.show(
            UpdateView("Downloading", notes="Notes", progress=PROGRESS_INDETERMINATE),
            activate=False,
        )
        for percent in range(0, 101, 25):
            window.render(UpdateView("Downloading", notes="Notes", progress=percent))
        time.sleep(0.5)
        assert process.poll() is None, process.stderr.read().decode(errors="replace")

        closer = threading.Thread(target=window.close, kwargs={"timeout": 15})
        closer.start()
        closer.join(timeout=20)
        assert process.wait(timeout=5) == 0
        time.sleep(0.2)
        assert "unavailable" not in actions  # "ready" arrived over stdout
    finally:
        if process.poll() is None:
            process.kill()


class _Field:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        return lambda *args: self.calls.append((name, args))


def test_mac_window_updates_only_changed_parts_and_settles_progress():
    from ui.update_window_mac import MacUpdateWindow

    window = MacUpdateWindow(lambda _action: None)
    parts = ("_headline", "_detail", "_notice", "_progress", "_notes", "_notes_scroll", "_primary", "_close")
    for name in parts:
        setattr(window, name, _Field())
    window._resize = lambda _with_notes: None

    window._apply(UpdateView("Checking", progress=PROGRESS_INDETERMINATE))
    window._apply(UpdateView("Downloading 1%", notes="n", progress=1))
    for name in parts:
        getattr(window, name).calls.clear()
    window._apply(UpdateView("Downloading 2%", notes="n", progress=2))

    assert window._headline.calls == [("setStringValue_", ("Downloading 2%",))]
    assert window._notes.calls == []  # Notes are not re-laid out per percent
    assert ("setDoubleValue_", (2.0,)) in window._progress.calls
    assert window._primary.calls == []


def test_mac_window_stops_the_busy_animation_after_checking():
    from ui.update_window_mac import MacUpdateWindow

    window = MacUpdateWindow(lambda _action: None)
    for name in ("_headline", "_detail", "_notice", "_progress", "_notes", "_notes_scroll", "_primary", "_close"):
        setattr(window, name, _Field())
    window._resize = lambda _with_notes: None

    window._apply(UpdateView("Checking", progress=PROGRESS_INDETERMINATE))
    window._apply(UpdateView("Up to date"))

    names = [name for name, _args in window._progress.calls]
    assert names.index("startAnimation_") < names.index("stopAnimation_")
    assert window._progress.calls[-1] == ("setHidden_", (True,))
