"""Deterministic lifecycle contracts for the optional FIFO history writer."""

import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

import utils.history as history


@pytest.fixture
def writer(tmp_path, monkeypatch):
    instance = history._HistoryWriter()
    monkeypatch.setattr(history, "HISTORY_FILE", tmp_path / "history.jsonl")
    monkeypatch.setattr(history, "_history_writer", instance)
    yield instance
    instance.shutdown()


@pytest.fixture
def blocked_save(writer, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    original = history._save_transcript_entry

    def save(entry):
        if entry["text"] == "A":
            entered.set()
            assert release.wait(timeout=5)
        return original(entry)

    monkeypatch.setattr(history, "_save_transcript_entry", save)
    yield entered, release
    release.set()


def entries():
    return [json.loads(line) for line in history.HISTORY_FILE.read_text().splitlines()]


def test_nonblocking_fifo_snapshot_and_worker_callbacks(writer, blocked_save, mocker):
    entered, release = blocked_save
    assert writer._executor is None
    clock = mocker.patch.object(history, "datetime")
    clock.now.return_value.isoformat.side_effect = ["accepted-A", "accepted-B"]
    calls = []
    caller = threading.current_thread()

    def record(value):
        assert threading.current_thread() is not caller
        calls.append(value)

    a = history.enqueue_transcript(
        " A ",
        mode="local",
        on_start=lambda: record("start-A"),
        on_done=lambda ok: record(("done-A", ok)),
    )
    assert entered.wait(timeout=5)
    b = history.enqueue_transcript(
        " B ",
        language="de",
        refined=True,
        app_context="Slack",
        on_start=lambda: record("start-B"),
        on_done=lambda ok: record(("done-B", ok)),
    )
    assert not a.done() and not b.done()
    assert not b.cancel()  # Accepted persistence cannot be abandoned by callers.
    clock.now.side_effect = AssertionError("timestamp must already be captured")
    release.set()
    assert a.result(timeout=5) is True and b.result(timeout=5) is True
    assert calls == ["start-A", ("done-A", True), "start-B", ("done-B", True)]
    assert entries() == [
        {"timestamp": "accepted-A", "text": "A", "mode": "local"},
        {
            "timestamp": "accepted-B",
            "text": "B",
            "language": "de",
            "refined": True,
            "app": "Slack",
        },
    ]


def test_failures_and_callback_errors_do_not_poison_queue(writer, monkeypatch, caplog):
    original = history._save_transcript_entry
    completed = []

    def save(entry):
        if entry["text"] == "false":
            return False
        if entry["text"] == "exception":
            raise OSError("injected failure")
        return original(entry)

    def start():
        raise ValueError("start failed")

    def done(ok):
        completed.append(ok)
        raise ValueError("done failed")

    monkeypatch.setattr(history, "_save_transcript_entry", save)
    futures = [
        history.enqueue_transcript(text, on_start=start, on_done=done)
        for text in ("false", "exception", "success")
    ]
    assert futures[0].result(timeout=5) is False
    with pytest.raises(OSError, match="injected failure"):
        futures[1].result(timeout=5)
    assert futures[2].result(timeout=5) is True
    assert completed == [False, False, True]
    assert [entry["text"] for entry in entries()] == ["success"]
    assert "History on_start callback failed" in caplog.text
    assert "History on_done callback failed" in caplog.text


def test_clear_is_fifo_barrier(writer, blocked_save, monkeypatch):
    entered, release = blocked_save
    clear_queued = threading.Event()
    submit = writer._submit

    def observe(operation):
        result = submit(operation)
        if operation is history._clear_history:
            clear_queued.set()
        return result

    monkeypatch.setattr(writer, "_submit", observe)
    a = history.enqueue_transcript("A")
    assert entered.wait(timeout=5)
    b = history.enqueue_transcript("B")
    with ThreadPoolExecutor(max_workers=1) as caller:
        cleared = caller.submit(history.clear_history)
        assert clear_queued.wait(timeout=5)
        c = history.enqueue_transcript("C")
        release.set()
        assert cleared.result(timeout=5) is True
    assert all(f.result(timeout=5) for f in (a, b, c))
    assert [entry["text"] for entry in entries()] == ["C"]


def test_shutdown_seals_and_drains_callbacks_for_all_callers(writer, monkeypatch):
    in_callback, release = threading.Event(), threading.Event()
    first_shutdown, second_shutdown = threading.Event(), threading.Event()
    completed = []

    def done(ok):
        in_callback.set()
        assert release.wait(timeout=5)
        completed.append(ok)

    future = history.enqueue_transcript("A", on_done=done)
    assert in_callback.wait(timeout=5)
    later = history.enqueue_transcript("B", on_done=completed.append)
    original = writer._executor.shutdown
    calls = []

    def observe(*, wait):
        calls.append(wait)
        (first_shutdown if len(calls) == 1 else second_shutdown).set()
        original(wait=wait)
        assert completed == [True, True]

    monkeypatch.setattr(writer._executor, "shutdown", observe)
    try:
        with ThreadPoolExecutor(max_workers=2) as callers:
            first = callers.submit(history.shutdown_history_writer)
            assert first_shutdown.wait(timeout=5)
            with pytest.raises(RuntimeError, match="shut down"):
                history.enqueue_transcript("rejected")
            with pytest.raises(RuntimeError, match="shut down"):
                history.clear_history()
            second = callers.submit(history.shutdown_history_writer)
            assert second_shutdown.wait(timeout=5)
            assert not first.done() and not second.done()
            release.set()
            first.result(timeout=5)
            second.result(timeout=5)
    finally:
        release.set()
    assert future.result(timeout=5) is True
    assert later.result(timeout=5) is True
    history.shutdown_history_writer()
    assert all(calls)


def test_concurrent_admission_racing_shutdown(writer, blocked_save):
    entered, release = blocked_save
    first = history.enqueue_transcript("A")
    assert entered.wait(timeout=5)
    barrier = threading.Barrier(5)

    def enqueue(index):
        barrier.wait(timeout=5)
        try:
            return history.enqueue_transcript(str(index))
        except RuntimeError:
            return None

    def shutdown():
        barrier.wait(timeout=5)
        history.shutdown_history_writer()

    with ThreadPoolExecutor(max_workers=5) as callers:
        submissions = [callers.submit(enqueue, index) for index in range(4)]
        stopped = callers.submit(shutdown)
        accepted = [submission.result(timeout=5) for submission in submissions]
        release.set()
        stopped.result(timeout=5)
    assert first.result(timeout=5) is True
    expected = {"A"}
    for index, future in enumerate(accepted):
        if future is not None:
            assert future.result(timeout=5) is True
            expected.add(str(index))
    assert {entry["text"] for entry in entries()} == expected
    with pytest.raises(RuntimeError, match="shut down"):
        history.enqueue_transcript("late")


def test_reentrant_enqueue_allowed_but_blocking_lifecycle_rejected(writer):
    children = []
    boundaries_checked = []

    def check_boundaries():
        for operation in (history.clear_history, history.shutdown_history_writer):
            with pytest.raises(RuntimeError, match="from its worker"):
                operation()
            boundaries_checked.append(operation)

    def start():
        check_boundaries()
        children.append(history.enqueue_transcript("child"))

    parent = history.enqueue_transcript(
        "parent",
        on_start=start,
        on_done=lambda ok: check_boundaries(),
    )
    assert parent.result(timeout=5) is True
    history.shutdown_history_writer()
    assert len(boundaries_checked) == 4
    assert children[0].result(timeout=5) is True
    assert [entry["text"] for entry in entries()] == ["parent", "child"]


def test_unused_shutdown_is_lazy_idempotent_and_final(writer):
    history.shutdown_history_writer()
    history.shutdown_history_writer()
    assert writer._executor is None
    with pytest.raises(RuntimeError, match="shut down"):
        history.enqueue_transcript("late")
    assert writer._executor is None
