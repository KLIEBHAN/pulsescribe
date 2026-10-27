"""Main-thread responsiveness and shutdown contracts for queued macOS history."""

import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import utils.history as history
from pulsescribe_daemon import PulseScribeDaemon
from utils.macos_latency_diagnostics import MacOSLatencyRun
from utils.state import AppState, DaemonMessage, MessageType


@pytest.fixture
def daemon(monkeypatch):
    daemon = PulseScribeDaemon(mode="local", language="de")
    daemon._last_was_refined = True
    daemon._format_done_text = lambda text: text
    daemon._paste_result = Mock(return_value=(True, "Transcript pasted"))
    daemon._update_state = lambda state, *_args: setattr(
        daemon, "_current_state", state
    )
    daemon._enter_error_state = lambda _text: daemon._update_state(AppState.ERROR)
    daemon._flush_ui_and_wait = Mock()
    daemon._apply_pending_hotkey_reconfigure_if_safe = lambda: None
    monkeypatch.setattr("pulsescribe_daemon.get_sound_player", lambda: Mock())
    return daemon


@pytest.mark.parametrize("paste_success", [True, False])
def test_blocked_history_does_not_block_result_feedback_or_finish_trace_early(
    daemon, monkeypatch, tmp_path, paste_success
):
    entered, release = threading.Event(), threading.Event()
    original = history._save_transcript_entry
    caller = threading.current_thread()
    worker_threads = []
    trace = tmp_path / "latency.jsonl"
    monkeypatch.setenv("PULSESCRIBE_MACOS_LATENCY_DIAGNOSTICS_FILE", "true")
    run = MacOSLatencyRun(enabled=True, mode="local", log_path=trace)
    write_diagnostics = run._append_jsonl

    def append_diagnostics(summary):
        assert threading.current_thread() is not caller
        write_diagnostics(summary)

    monkeypatch.setattr(run, "_append_jsonl", append_diagnostics)
    daemon._paste_result.return_value = (paste_success, "delivery result")

    def blocked(entry):
        worker_threads.append(threading.current_thread())
        entered.set()
        assert release.wait(timeout=5)
        return original(entry)

    monkeypatch.setattr(history, "_save_transcript_entry", blocked)
    try:
        daemon._handle_transcript_result("private transcript", latency_run=run)
        assert entered.wait(timeout=5)
        assert daemon._current_state == (
            AppState.IDLE if paste_success else AppState.ERROR
        )
        assert daemon._flush_ui_and_wait.call_count == int(paste_success)
        assert not history.HISTORY_FILE.exists()
        assert not trace.exists()
        assert worker_threads[0] is not caller
    finally:
        release.set()
        history.shutdown_history_writer()

    assert json.loads(history.HISTORY_FILE.read_text())["text"] == "private transcript"
    summary = json.loads(trace.read_text())
    assert summary["outcome"] == ("success" if paste_success else "paste_failed")
    names = [event["name"] for event in summary["events"]]
    assert names[-3:] == ["history_start", "history_done", "finish"]
    assert names.index("history_queued") < names.index("history_start")
    assert "history_duration" in summary["durations_ms"]
    assert "private transcript" not in trace.read_text()


def test_reentrant_result_queues_captured_metadata_before_ui_flush(daemon, monkeypatch):
    entries = []
    entered, release = threading.Event(), threading.Event()

    def save(entry):
        if entry["text"] == "first":
            entered.set()
            assert release.wait(timeout=5)
        entries.append(entry)
        return True

    def flush():
        assert entered.wait(timeout=5)
        daemon._flush_ui_and_wait = lambda: None
        daemon._run_mode = "groq"
        daemon.language = "en"
        daemon._last_was_refined = False
        daemon._handle_transcript_result("second")

    monkeypatch.setattr(history, "_save_transcript_entry", save)
    daemon._flush_ui_and_wait = flush
    try:
        daemon._handle_transcript_result("first")
        assert entries == []
    finally:
        release.set()
        history.shutdown_history_writer()

    assert [entry["text"] for entry in entries] == ["first", "second"]
    assert [(entry["mode"], entry["language"]) for entry in entries] == [
        ("local", "de"),
        ("groq", "en"),
    ]
    assert entries[0]["refined"] is True and "refined" not in entries[1]
    assert entries[0]["timestamp"] <= entries[1]["timestamp"]


@pytest.mark.parametrize("failure", ["false", "raise", "submission"])
def test_history_failure_still_finishes_the_captured_run(daemon, monkeypatch, failure):
    run = Mock()

    def fail(*_args, **_kwargs):
        if failure == "false":
            return False
        raise RuntimeError("injected failure")

    monkeypatch.setattr(
        history,
        "enqueue_transcript" if failure == "submission" else "_save_transcript_entry",
        fail,
    )
    daemon._handle_transcript_result("text", latency_run=run)
    history.shutdown_history_writer()

    run.mark.assert_any_call("history_done", saved=False)
    run.finish.assert_called_once_with("success")
    assert daemon._current_state == AppState.IDLE


@pytest.mark.parametrize("exit_path", ["native", "signal", "atexit", "update"])
def test_exit_drains_history_and_diagnostics_before_termination(
    daemon, monkeypatch, exit_path
):
    entered, release = threading.Event(), threading.Event()
    sealed = threading.Event()
    finished = []
    registration = []
    signals = []
    exit_handlers = []
    center = SimpleNamespace(
        addObserverForName_object_queue_usingBlock_=lambda *args: (
            registration.append(args) or object()
        )
    )
    app = SimpleNamespace(terminate_=lambda _sender: finished.append("terminate"))
    monkeypatch.setitem(
        sys.modules,
        "Foundation",
        SimpleNamespace(
            NSNotificationCenter=SimpleNamespace(defaultCenter=lambda: center)
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "AppKit",
        SimpleNamespace(
            NSApplicationWillTerminateNotification="will-terminate",
            NSApplication=SimpleNamespace(sharedApplication=lambda: app),
        ),
    )
    monkeypatch.setattr("pulsescribe_daemon.atexit.register", exit_handlers.append)
    daemon._install_runloop_shutdown_handlers(
        app=app,
        timer_cls=SimpleNamespace(
            scheduledTimerWithTimeInterval_repeats_block_=lambda *_args: None
        ),
        signal_mod=SimpleNamespace(
            SIGINT=2, signal=lambda _sig, fn: signals.append(fn)
        ),
    )
    assert registration[0][1:3] == (
        None,
        None,
    )  # Synchronous native termination notification.
    run = Mock()
    run.finish.side_effect = lambda _outcome: finished.append("diagnostics")

    def save(_entry):
        entered.set()
        assert release.wait(timeout=5)
        finished.append("saved")
        return True

    monkeypatch.setattr(history, "_save_transcript_entry", save)
    daemon._handle_transcript_result("text", latency_run=run)
    assert entered.wait(timeout=5)
    original_shutdown = history._history_writer._executor.shutdown

    def shutdown(*, wait):
        sealed.set()
        original_shutdown(wait=wait)

    monkeypatch.setattr(history._history_writer._executor, "shutdown", shutdown)
    daemon._updater = SimpleNamespace(
        install=lambda: finished.append("install"), stop=lambda: None
    )
    paths = {
        "native": lambda: registration[0][3](None),
        "signal": lambda: signals[0](2, None),
        "atexit": exit_handlers[0],
        "update": daemon._install_update_if_idle,
    }
    with ThreadPoolExecutor(max_workers=1) as caller:
        exiting = caller.submit(paths[exit_path])
        try:
            assert sealed.wait(timeout=5)
            assert not exiting.done()
            assert daemon._update_shutdown
            assert "terminate" not in finished
            daemon._start_recording_locked = Mock()
            daemon._start_recording()
            daemon._start_recording_locked.assert_not_called()
        finally:
            release.set()
        exiting.result(timeout=5)
    assert finished.index("saved") < finished.index("diagnostics")
    if exit_path in ("signal", "update"):
        assert finished.index("diagnostics") < finished.index("terminate")
    daemon.cleanup()  # Repeated native/signal/atexit cleanup stays safe.


def test_native_notification_observer_runs_synchronously_once(daemon, monkeypatch):
    foundation = pytest.importorskip("Foundation")
    appkit = pytest.importorskip("AppKit")
    center = foundation.NSNotificationCenter.alloc().init()
    notification = appkit.NSApplicationWillTerminateNotification
    monkeypatch.setattr(
        foundation,
        "NSNotificationCenter",
        SimpleNamespace(defaultCenter=lambda: center),
    )
    monkeypatch.setattr("pulsescribe_daemon.atexit.register", lambda _callback: None)
    calls = []
    daemon._shutdown_history_before_exit = lambda: calls.append(
        threading.current_thread()
    )
    options = dict(
        app=SimpleNamespace(terminate_=Mock()),
        timer_cls=SimpleNamespace(scheduledTimerWithTimeInterval_repeats_block_=Mock()),
        signal_mod=SimpleNamespace(SIGINT=2, signal=Mock()),
    )
    try:
        daemon._install_runloop_shutdown_handlers(**options)
        daemon._install_runloop_shutdown_handlers(**options)
        center.postNotificationName_object_(notification, None)
        assert calls == [threading.current_thread()]
    finally:
        if daemon._termination_observer is not None:
            center.removeObserver_(daemon._termination_observer)


def test_shutdown_rejects_unclaimed_results_without_pasting(daemon):
    daemon._shutdown_history_before_exit()
    run = Mock()
    daemon._active_run_id = 1
    result = DaemonMessage(type=MessageType.TRANSCRIPT_RESULT, payload="late")

    assert not daemon._claim_and_handle_worker_terminal(
        run_id=1,
        result_queue_ref=daemon._result_queue,
        terminal=result,
        latency_run=run,
    )
    daemon._handle_transcript_result("late", latency_run=run)

    daemon._paste_result.assert_not_called()
    assert not history.HISTORY_FILE.exists()
