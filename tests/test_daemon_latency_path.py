import queue
import sys
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from pulsescribe_daemon import PulseScribeDaemon
from utils.macos_latency_diagnostics import start_macos_latency_run
from utils.state import DaemonMessage, MessageType


def _disabled_latency_run():
    return start_macos_latency_run(enabled=False)


def test_audio_dependency_prewarm_is_idempotent_and_import_only():
    daemon = PulseScribeDaemon(mode="local")
    imported: list[str] = []

    class _ImmediateThread:
        def __init__(self, *, target, **_kwargs):
            self.target = target

        def start(self):
            self.target()

    with (
        patch("pulsescribe_daemon.threading.Thread", _ImmediateThread),
        patch(
            "importlib.import_module",
            side_effect=lambda name: imported.append(name) or SimpleNamespace(),
        ),
    ):
        assert daemon._prewarm_audio_dependencies_async() is True
        assert daemon._prewarm_audio_dependencies_async() is False

    assert imported == ["numpy", "sounddevice", "soundfile"]
    assert daemon._audio_prewarm_complete.is_set()


def test_ready_sound_follows_successful_stream_start():
    daemon = PulseScribeDaemon(mode="local")
    stop_event = threading.Event()
    stop_event.set()
    calls: list[str] = []
    stream = MagicMock()

    def input_stream(**kwargs):
        calls.append("construct")
        stream.start.side_effect = lambda: calls.append("start")
        stream.close.side_effect = lambda: calls.append("close")
        kwargs["finished_callback"]()
        return stream

    sounddevice = SimpleNamespace(
        InputStream=input_stream,
        CallbackAbort=RuntimeError,
        sleep=lambda _ms: None,
    )
    player = MagicMock()
    player.play.side_effect = lambda name: calls.append(name)

    with (
        patch.dict(sys.modules, {"sounddevice": sounddevice}),
        patch("pulsescribe_daemon.get_sound_player", return_value=player),
    ):
        daemon._capture_recording_audio(
            run_id=1,
            result_queue_ref=queue.Queue(),
            stop_event=stop_event,
            latency_run=_disabled_latency_run(),
        )

    assert calls.index("construct") < calls.index("start") < calls.index("ready")
    assert calls[-1] == "stop"


def test_ready_sound_failure_still_closes_started_stream():
    daemon = PulseScribeDaemon(mode="local")
    stop_event = threading.Event()
    stop_event.set()
    stream = MagicMock()

    def input_stream(**kwargs):
        stream.start.side_effect = kwargs["finished_callback"]
        return stream

    sounddevice = SimpleNamespace(
        InputStream=input_stream,
        CallbackAbort=RuntimeError,
        sleep=lambda _ms: None,
    )
    player = MagicMock()
    player.play.side_effect = RuntimeError("sound unavailable")

    with (
        patch.dict(sys.modules, {"sounddevice": sounddevice}),
        patch("pulsescribe_daemon.get_sound_player", return_value=player),
        pytest.raises(RuntimeError, match="sound unavailable"),
    ):
        daemon._capture_recording_audio(
            run_id=1,
            result_queue_ref=queue.Queue(),
            stop_event=stop_event,
            latency_run=_disabled_latency_run(),
        )

    stream.close.assert_called_once_with()


def test_failed_stream_start_closes_stream_without_ready_sound():
    daemon = PulseScribeDaemon(mode="local")
    stream = MagicMock()
    stream.start.side_effect = RuntimeError("device unavailable")
    sounddevice = SimpleNamespace(
        InputStream=lambda **_kwargs: stream,
        CallbackAbort=RuntimeError,
        sleep=lambda _ms: None,
    )
    player = MagicMock()

    with (
        patch.dict(sys.modules, {"sounddevice": sounddevice}),
        patch("pulsescribe_daemon.get_sound_player", return_value=player),
        pytest.raises(RuntimeError, match="device unavailable"),
    ):
        daemon._capture_recording_audio(
            run_id=1,
            result_queue_ref=queue.Queue(),
            stop_event=threading.Event(),
            latency_run=_disabled_latency_run(),
        )

    stream.close.assert_called_once_with()
    player.play.assert_not_called()


def test_worker_terminal_is_dispatched_and_claimed_once_on_main():
    daemon = PulseScribeDaemon(mode="local")
    result_queue = queue.Queue()
    daemon._active_run_id = 7
    daemon._result_queue = result_queue
    daemon._worker_abandoned = False
    run = MagicMock()
    daemon._latency_run = run
    terminal = DaemonMessage(type=MessageType.TRANSCRIPT_RESULT, payload="hello")
    scheduled: list[object] = []
    fake_queue = MagicMock()
    fake_queue.addOperationWithBlock_.side_effect = scheduled.append
    foundation = SimpleNamespace(
        NSOperationQueue=SimpleNamespace(mainQueue=lambda: fake_queue)
    )

    with (
        patch.dict(sys.modules, {"Foundation": foundation}),
        patch.object(daemon, "_handle_transcript_result") as handle_result,
        patch.object(daemon, "_stop_result_polling") as stop_polling,
    ):
        worker = threading.Thread(
            target=lambda: daemon._publish_worker_terminal(
                run_id=7,
                result_queue_ref=result_queue,
                terminal=terminal,
                latency_run=run,
            )
        )
        worker.start()
        worker.join()

        assert result_queue.empty()
        assert len(scheduled) == 1
        scheduled[0]()
        scheduled[0]()

    stop_polling.assert_called_once_with()
    handle_result.assert_called_once_with("hello", latency_run=run)
    assert daemon._terminal_claimed_run_id == 7
    assert daemon._latency_run is None


def test_worker_error_terminal_finishes_latency_run_on_main():
    daemon = PulseScribeDaemon(mode="local")
    result_queue = queue.Queue()
    daemon._active_run_id = 8
    daemon._result_queue = result_queue
    daemon._worker_abandoned = False
    run = MagicMock()
    daemon._latency_run = run
    terminal = RuntimeError("boom")
    scheduled: list[object] = []
    fake_queue = MagicMock()
    fake_queue.addOperationWithBlock_.side_effect = scheduled.append
    foundation = SimpleNamespace(
        NSOperationQueue=SimpleNamespace(mainQueue=lambda: fake_queue)
    )

    with (
        patch.dict(sys.modules, {"Foundation": foundation}),
        patch.object(daemon, "_enter_error_state") as enter_error,
        patch.object(daemon, "_stop_result_polling"),
        patch("pulsescribe_daemon.emergency_log"),
    ):
        worker = threading.Thread(
            target=lambda: daemon._publish_worker_terminal(
                run_id=8,
                result_queue_ref=result_queue,
                terminal=terminal,
                latency_run=run,
            )
        )
        worker.start()
        worker.join(timeout=1.0)
        assert not worker.is_alive()
        assert len(scheduled) == 1
        scheduled[0]()

    run.finish.assert_called_once_with("error", error_type="RuntimeError")
    enter_error.assert_called_once()


def test_publish_worker_terminal_falls_back_to_polling_without_foundation():
    daemon = PulseScribeDaemon(mode="local")
    result_queue = queue.Queue()
    run = MagicMock()
    terminal = DaemonMessage(type=MessageType.TRANSCRIPT_RESULT, payload="fallback")
    foundation = SimpleNamespace()

    with patch.dict(sys.modules, {"Foundation": foundation}):
        worker = threading.Thread(
            target=lambda: daemon._publish_worker_terminal(
                run_id=1,
                result_queue_ref=result_queue,
                terminal=terminal,
                latency_run=run,
            )
        )
        worker.start()
        worker.join(timeout=1.0)

    assert not worker.is_alive()
    assert result_queue.get_nowait() is terminal


def test_stale_worker_terminal_cannot_update_current_run():
    daemon = PulseScribeDaemon(mode="local")
    old_queue = queue.Queue()
    daemon._active_run_id = 2
    daemon._result_queue = queue.Queue()
    run = MagicMock()
    terminal = DaemonMessage(type=MessageType.TRANSCRIPT_RESULT, payload="stale")

    with patch.object(daemon, "_handle_transcript_result") as handle_result:
        assert (
            daemon._claim_and_handle_worker_terminal(
                run_id=1,
                result_queue_ref=old_queue,
                terminal=terminal,
                latency_run=run,
            )
            is False
        )

    handle_result.assert_not_called()
    run.finish.assert_called_once_with("stale")
