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


class FakePipe(io.BytesIO):
    """stdin that keeps its content readable after close()."""

    was_closed = False

    def close(self):
        self.was_closed = True


class FakeProcess:
    def __init__(self, stdout_lines=()):
        self.stdin = FakePipe()
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


def make_window(processes, actions=None, **kwargs):
    launches = []

    def launch(activate):
        launches.append(activate)
        return processes[len(launches) - 1]

    actions = [] if actions is None else actions
    return WindowsUpdateWindow(launch, actions.append, **kwargs), launches, actions


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
    assert wait_for(lambda: len(process.messages()) == 3)
    messages = process.messages()
    assert [m["view"]["headline"] for m in messages] == ["one", "two", "three"]
    assert [m["activate"] for m in messages] == [False, None, True]


def test_progress_updates_send_only_changed_fields():
    process = FakeProcess()
    window, _launches, _actions = make_window([process])
    notes = "Long release notes " * 500

    window.show(UpdateView("1%", notes=notes, progress=1), activate=False)
    window.render(UpdateView("2%", notes=notes, progress=2))
    window.render(UpdateView("2%", notes=notes, progress=2))  # unchanged: nothing sent

    assert wait_for(lambda: len(process.messages()) == 2)
    time.sleep(0.05)
    first, second = process.messages()
    assert first["view"]["notes"] == notes
    assert second["view"] == {"headline": "2%", "progress": 2}
    assert len(process.messages()) == 2


def test_a_hung_window_never_blocks_the_caller():
    class BlockingPipe(FakePipe):
        def write(self, data):
            release.wait(2)
            return super().write(data)

    release = threading.Event()
    process = FakeProcess()
    process.stdin = BlockingPipe()
    window, _launches, _actions = make_window([process])

    started = time.monotonic()
    window.show(UpdateView("ready", notes="n" * 20_000), activate=True)
    for percent in range(10):
        window.render(UpdateView(f"{percent}%", notes="n" * 20_000, progress=percent))
    elapsed = time.monotonic() - started
    release.set()

    assert elapsed < 0.5


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
    assert wait_for(lambda: len(second.messages()) == 1)
    assert second.messages()[0]["view"]["headline"] == "again"


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

    assert wait_for(lambda: actions == ["install"])
    assert wait_for(lambda: process.stdin.was_closed)  # close is handled here


def test_window_that_exits_before_ready_is_reported_unavailable():
    window, _launches, actions = make_window([FakeProcess([b"Traceback ...\n"])])

    window.show(UpdateView("ready"), activate=True)

    assert wait_for(lambda: actions == ["unavailable"])


def test_window_that_crashes_after_ready_is_reported_unavailable():
    process = FakeProcess([b'PULSESCRIBE-UPDATE {"action": "ready"}\n'])
    window, _launches, actions = make_window([process])

    window.show(UpdateView("downloading"), activate=False)

    assert wait_for(lambda: actions == ["unavailable"])


def test_failed_is_owned_by_the_window_and_cleared_by_a_new_launch():
    dead, healthy = FakeProcess(), FakeProcess()
    healthy.stdout = None  # Set below: keeps running
    stop = threading.Event()

    class RunningStdout:
        def __iter__(self):
            yield b'PULSESCRIBE-UPDATE {"action": "ready"}\n'
            stop.wait(2)

    healthy.stdout = RunningStdout()
    window, _launches, actions = make_window([dead, healthy])

    window.show(UpdateView("one"), activate=False)
    assert wait_for(lambda: actions == ["unavailable"])
    assert window.failed is True
    dead.returncode = 1

    window.show(UpdateView("again"), activate=True)

    assert window.failed is False
    window.close()
    stop.set()


def test_failed_launch_marks_the_window_failed():
    def launch(_activate):
        raise OSError("no exe")

    window = WindowsUpdateWindow(launch, lambda _action: None)

    with pytest.raises(OSError):
        window.show(UpdateView("ready"), activate=True)

    assert window.failed is True


def test_unavailable_is_reported_atomically_with_respect_to_show():
    locked_during_report = []
    window = None

    def on_action(action):
        locked_during_report.append(window._lock.locked())

    window = WindowsUpdateWindow(lambda _activate: FakeProcess(), on_action)
    window.show(UpdateView("downloading"), activate=False)

    assert wait_for(lambda: locked_during_report == [True])


def test_window_that_hangs_before_ready_is_stopped_and_reported():
    class HungProcess(FakeProcess):
        def __init__(self):
            super().__init__()
            self.ended = threading.Event()
            self.stdout = self  # Iterating blocks until terminate()

        def __iter__(self):
            self.ended.wait(2)
            return iter([])

        def terminate(self):
            self.terminated = True
            self.ended.set()

    process = HungProcess()
    window, _launches, actions = make_window([process], startup_timeout=0.05)

    window.show(UpdateView("downloading"), activate=False)

    assert wait_for(lambda: actions == ["unavailable"])
    assert process.terminated


def test_ready_handshake_is_atomic_with_the_startup_watchdog():
    locked_when_set = []

    class RecordingEvent(threading.Event):
        def set(self):
            locked_when_set.append(window._lock.locked())
            super().set()

    process = FakeProcess([b'PULSESCRIBE-UPDATE {"action": "ready"}\n'])
    window, _launches, _actions = make_window([process])
    window._process = process
    ready = RecordingEvent()

    window._read_actions(process, ready)
    window._stop_if_not_started(process, ready)

    assert locked_when_set == [True]
    assert not process.terminated


def test_ready_window_is_not_stopped_by_the_startup_watchdog():
    class RunningStdout:
        def __iter__(self):
            yield b'PULSESCRIBE-UPDATE {"action": "ready"}\n'
            stop.wait(2)

    stop = threading.Event()
    process = FakeProcess()
    process.stdout = RunningStdout()
    window, _launches, actions = make_window([process], startup_timeout=0.05)

    window.show(UpdateView("downloading"), activate=False)
    time.sleep(0.2)

    assert not process.terminated
    assert actions == []
    window.close()
    stop.set()


def test_failure_of_a_replaced_window_process_is_ignored():
    release = threading.Event()

    class DyingStdout:
        def __iter__(self):
            release.wait(2)
            return iter([b"Traceback ...\n"])

    class RunningStdout:
        def __iter__(self):
            yield b'PULSESCRIBE-UPDATE {"action": "ready"}\n'
            stop.wait(2)

    stop = threading.Event()
    old, new = FakeProcess(), FakeProcess()
    old.stdout, new.stdout = DyingStdout(), RunningStdout()
    window, launches, actions = make_window([old, new])
    window.show(UpdateView("one"), activate=False)
    old.returncode = 1  # poll() sees the exit before the reader reaches EOF

    window.show(UpdateView("again"), activate=True)
    release.set()
    time.sleep(0.1)

    assert launches == [False, True]
    assert actions == []
    window.close()
    stop.set()


def test_buffered_close_of_a_replaced_window_does_not_close_the_new_one():
    release = threading.Event()

    class LateClose:
        def __iter__(self):
            release.wait(2)
            return iter([b'PULSESCRIBE-UPDATE {"action": "close"}\n'])

    class RunningStdout:
        def __iter__(self):
            stop.wait(2)
            return iter([])

    stop = threading.Event()
    old, new = FakeProcess(), FakeProcess()
    old.stdout, new.stdout = LateClose(), RunningStdout()
    window, _launches, actions = make_window([old, new])
    window.show(UpdateView("one"), activate=False)
    old.returncode = 0

    window.show(UpdateView("again"), activate=True)
    release.set()
    time.sleep(0.1)

    assert actions == []
    assert not new.stdin.was_closed  # The stale close only targets its own process
    window.close()
    stop.set()


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

    assert wait_for(lambda: process.stdin.was_closed)
    assert process.waited
    window.render(UpdateView("after close"))  # No process left to write to
    time.sleep(0.05)
    assert len(process.messages()) == 1


def test_close_waits_for_a_terminated_hanging_window():
    class HangingProcess(FakeProcess):
        def __init__(self):
            super().__init__()
            self.events = []

        def wait(self, timeout=None):
            self.events.append("wait")
            if "terminate" not in self.events:
                raise subprocess.TimeoutExpired("window", timeout)
            self.returncode = 1
            return 1

        def terminate(self):
            self.events.append("terminate")

    process = HangingProcess()
    window, _launches, _actions = make_window([process])
    window.show(UpdateView("ready"), activate=False)

    window.close(timeout=0.01)

    assert process.events == ["wait", "terminate", "wait"]


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
