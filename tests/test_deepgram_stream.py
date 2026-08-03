from __future__ import annotations

import asyncio
import logging
import queue
import sys
import threading
import time
from types import SimpleNamespace
from typing import Any, cast

import pytest

import providers.deepgram_stream as deepgram_stream


def _response(
    transcript: str,
    *,
    is_final: bool = False,
    from_finalize: bool = False,
) -> SimpleNamespace:
    return SimpleNamespace(
        channel=SimpleNamespace(
            alternatives=[SimpleNamespace(transcript=transcript)],
        ),
        is_final=is_final,
        from_finalize=from_finalize,
    )


def test_message_handler_writes_interim_text_as_utf8(tmp_path, monkeypatch) -> None:
    interim_file = tmp_path / "interim.txt"
    state = deepgram_stream.StreamState()
    handler = deepgram_stream._create_message_handler(state, "sess")

    monkeypatch.setattr(deepgram_stream, "INTERIM_FILE", interim_file)
    monkeypatch.setattr(deepgram_stream.time, "perf_counter", lambda: 1.0)

    handler(_response("Grüße 你好"))

    assert interim_file.read_text(encoding="utf-8") == "Grüße 你好"
    assert state.last_interim_text == "Grüße 你好"


def test_message_handler_calls_direct_interim_callback(tmp_path, monkeypatch) -> None:
    interim_file = tmp_path / "interim.txt"
    state = deepgram_stream.StreamState()
    callbacks: list[str] = []
    handler = deepgram_stream._create_message_handler(
        state,
        "sess",
        interim_text_callback=callbacks.append,
    )

    monkeypatch.setattr(deepgram_stream, "INTERIM_FILE", interim_file)
    monkeypatch.setattr(deepgram_stream.time, "perf_counter", lambda: 1.0)

    handler(_response("direct interim"))

    assert callbacks == ["direct interim"]
    assert interim_file.read_text(encoding="utf-8") == "direct interim"


def test_message_handler_skips_duplicate_interim_writes(monkeypatch) -> None:
    state = deepgram_stream.StreamState()
    handler = deepgram_stream._create_message_handler(state, "sess")
    write_calls: list[tuple[str, str]] = []

    class _FakeInterimFile:
        def write_text(self, text: str, *, encoding: str) -> None:
            write_calls.append((text, encoding))

    perf_counter_values = iter((1.0, 1.2))

    monkeypatch.setattr(deepgram_stream, "INTERIM_FILE", _FakeInterimFile())
    monkeypatch.setattr(
        deepgram_stream.time,
        "perf_counter",
        lambda: next(perf_counter_values),
    )

    handler(_response("same interim"))
    handler(_response("same interim"))

    assert write_calls == [("same interim", "utf-8")]


def test_message_handler_marks_finalize_for_duplicate_interim(monkeypatch) -> None:
    state = deepgram_stream.StreamState()
    handler = deepgram_stream._create_message_handler(state, "sess")
    write_calls: list[tuple[str, str]] = []

    class _FakeInterimFile:
        def write_text(self, text: str, *, encoding: str) -> None:
            write_calls.append((text, encoding))

    monkeypatch.setattr(deepgram_stream, "INTERIM_FILE", _FakeInterimFile())
    monkeypatch.setattr(deepgram_stream.time, "perf_counter", lambda: 1.0)

    handler(_response("same interim"))
    handler(_response("same interim", from_finalize=True))

    assert write_calls == [("same interim", "utf-8")]
    assert state.finalize_transcript_received is True
    assert state.finalize_done.is_set()


def test_message_handler_keeps_latest_interim_for_fallback_when_write_is_throttled(
    monkeypatch,
) -> None:
    state = deepgram_stream.StreamState()
    handler = deepgram_stream._create_message_handler(state, "sess")
    write_calls: list[tuple[str, str]] = []

    class _FakeInterimFile:
        def write_text(self, text: str, *, encoding: str) -> None:
            write_calls.append((text, encoding))

    perf_counter_values = iter((1.0, 1.05))

    monkeypatch.setattr(deepgram_stream, "INTERIM_FILE", _FakeInterimFile())
    monkeypatch.setattr(
        deepgram_stream.time,
        "perf_counter",
        lambda: next(perf_counter_values),
    )

    handler(_response("hello"))
    handler(_response("hello world"))

    assert write_calls == [("hello", "utf-8")]
    assert state.last_interim_text == "hello world"
    assert state.last_interim_written_text == "hello"
    assert deepgram_stream._resolve_stream_result(state, "sess") == "hello world"


def test_message_handler_writes_pending_interim_once_throttle_window_passes(
    monkeypatch,
) -> None:
    state = deepgram_stream.StreamState()
    handler = deepgram_stream._create_message_handler(state, "sess")
    write_calls: list[tuple[str, str]] = []

    class _FakeInterimFile:
        def write_text(self, text: str, *, encoding: str) -> None:
            write_calls.append((text, encoding))

    perf_counter_values = iter((1.0, 1.05, 1.2))

    monkeypatch.setattr(deepgram_stream, "INTERIM_FILE", _FakeInterimFile())
    monkeypatch.setattr(
        deepgram_stream.time,
        "perf_counter",
        lambda: next(perf_counter_values),
    )

    handler(_response("hello"))
    handler(_response("hello world"))
    handler(_response("hello world"))

    assert write_calls == [("hello", "utf-8"), ("hello world", "utf-8")]
    assert state.last_interim_written_text == "hello world"


def test_message_handler_keeps_final_transcripts_out_of_interim_file(
    monkeypatch,
) -> None:
    state = deepgram_stream.StreamState()
    handler = deepgram_stream._create_message_handler(state, "sess")
    write_calls: list[tuple[str, str]] = []

    class _FakeInterimFile:
        def write_text(self, text: str, *, encoding: str) -> None:
            write_calls.append((text, encoding))

    monkeypatch.setattr(deepgram_stream, "INTERIM_FILE", _FakeInterimFile())

    handler(_response("final transcript", is_final=True, from_finalize=True))

    assert state.final_transcripts == ["final transcript"]
    assert state.finalize_done.is_set()
    assert write_calls == []


def test_message_handler_tracks_empty_finalize_ack_without_transcript() -> None:
    state = deepgram_stream.StreamState()
    handler = deepgram_stream._create_message_handler(state, "sess")

    handler(_response("", from_finalize=True))

    assert state.finalize_empty_ack_received is True
    assert state.finalize_transcript_received is False
    assert state.finalize_done.is_set()


def test_message_handler_logs_redacted_final_transcript(caplog) -> None:
    state = deepgram_stream.StreamState()
    handler = deepgram_stream._create_message_handler(state, "sess")

    with caplog.at_level(logging.INFO, logger="pulsescribe"):
        handler(_response("final transcript", is_final=True, from_finalize=True))

    messages = " ".join(record.getMessage() for record in caplog.records)
    assert "[sess] Final: <redacted 16 chars>" in messages
    assert "final transcript" not in messages


def test_close_handler_marks_finalize_done_and_stop_event() -> None:
    state = deepgram_stream.StreamState()
    handler = deepgram_stream._create_close_handler(state, "sess")

    handler(None)

    assert state.finalize_done.is_set()
    assert state.stop_event.is_set()


def test_resolve_stream_result_prefers_final_transcripts() -> None:
    state = deepgram_stream.StreamState(
        final_transcripts=["first final", "second final"],
        last_interim_text="interim fallback",
    )

    assert (
        deepgram_stream._resolve_stream_result(state, "sess")
        == "first final second final"
    )


def test_resolve_stream_result_falls_back_to_last_interim(caplog) -> None:
    state = deepgram_stream.StreamState(last_interim_text="short dictation")

    with caplog.at_level(logging.INFO, logger="pulsescribe"):
        result = deepgram_stream._resolve_stream_result(state, "sess")

    assert result == "short dictation"
    messages = " ".join(record.getMessage() for record in caplog.records)
    assert "Kein Final-Transkript erhalten" in messages
    assert "short dictation" not in messages


def test_graceful_shutdown_returns_quickly_after_connection_close(monkeypatch) -> None:
    class _FakeControlMessage:
        def __init__(self, type: str) -> None:
            self.type = type

    monkeypatch.setitem(
        sys.modules,
        "deepgram.extensions.types.sockets",
        SimpleNamespace(ListenV1ControlMessage=_FakeControlMessage),
    )

    async def _run() -> float:
        state = deepgram_stream.StreamState()
        audio_queue: asyncio.Queue[bytes | None] = asyncio.Queue()

        async def _send_worker() -> None:
            while True:
                item = await audio_queue.get()
                if item is None:
                    return

        async def _listen_worker() -> None:
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                return

        send_task = asyncio.create_task(_send_worker())
        listen_task = asyncio.create_task(_listen_worker())
        close_handler = deepgram_stream._create_close_handler(state, "sess")

        class _FakeConnection:
            async def send_control(self, message) -> None:
                if getattr(message, "type", "") == "Finalize":
                    close_handler(None)

        start = time.perf_counter()
        await deepgram_stream._graceful_shutdown(
            connection=cast(Any, _FakeConnection()),
            state=state,
            audio_queue=audio_queue,
            send_task=send_task,
            listen_task=listen_task,
            session_id="sess",
            sample_rate=16000,
        )
        return time.perf_counter() - start

    elapsed = asyncio.run(_run())

    assert elapsed < 0.2


def test_graceful_shutdown_emits_latency_events(monkeypatch) -> None:
    class _FakeControlMessage:
        def __init__(self, type: str) -> None:
            self.type = type

    monkeypatch.setitem(
        sys.modules,
        "deepgram.extensions.types.sockets",
        SimpleNamespace(ListenV1ControlMessage=_FakeControlMessage),
    )
    monkeypatch.setattr(deepgram_stream, "DEEPGRAM_TAIL_PADDING_SECONDS", 0.1)
    monkeypatch.setattr(deepgram_stream, "DEEPGRAM_EMPTY_FINALIZE_GRACE_SECONDS", 0.0)

    async def _run() -> list[str]:
        state = deepgram_stream.StreamState()
        audio_queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        latency_events: list[str] = []

        async def _send_worker() -> None:
            while True:
                item = await audio_queue.get()
                if item is None:
                    return

        async def _listen_worker() -> None:
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                return

        send_task = asyncio.create_task(_send_worker())
        listen_task = asyncio.create_task(_listen_worker())

        class _FakeConnection:
            async def send_control(self, message) -> None:
                if getattr(message, "type", "") == "Finalize":
                    state.finalize_done.set()

        await deepgram_stream._graceful_shutdown(
            connection=cast(Any, _FakeConnection()),
            state=state,
            audio_queue=audio_queue,
            send_task=send_task,
            listen_task=listen_task,
            session_id="sess",
            sample_rate=10,
            latency_event_callback=lambda name, _fields=None: latency_events.append(
                name
            ),
        )
        return latency_events

    events = asyncio.run(_run())

    assert events == [
        "deepgram_tail_padding",
        "deepgram_finalize_send",
        "deepgram_finalize_done",
        "deepgram_close_send",
    ]


def test_graceful_shutdown_sends_tail_padding_before_sentinel(monkeypatch) -> None:
    class _FakeControlMessage:
        def __init__(self, type: str) -> None:
            self.type = type

    monkeypatch.setitem(
        sys.modules,
        "deepgram.extensions.types.sockets",
        SimpleNamespace(ListenV1ControlMessage=_FakeControlMessage),
    )
    monkeypatch.setattr(deepgram_stream, "DEEPGRAM_TAIL_PADDING_SECONDS", 0.1)
    monkeypatch.setattr(deepgram_stream, "DEEPGRAM_EMPTY_FINALIZE_GRACE_SECONDS", 0.0)

    async def _run() -> list[bytes | None]:
        state = deepgram_stream.StreamState()
        audio_queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        sent_items: list[bytes | None] = []

        async def _send_worker() -> None:
            while True:
                item = await audio_queue.get()
                sent_items.append(item)
                if item is None:
                    return

        async def _listen_worker() -> None:
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                return

        send_task = asyncio.create_task(_send_worker())
        listen_task = asyncio.create_task(_listen_worker())
        asyncio.get_running_loop().call_soon_threadsafe(
            audio_queue.put_nowait, b"last-audio"
        )

        class _FakeConnection:
            async def send_control(self, message) -> None:
                if getattr(message, "type", "") == "Finalize":
                    state.finalize_done.set()

        await deepgram_stream._graceful_shutdown(
            connection=cast(Any, _FakeConnection()),
            state=state,
            audio_queue=audio_queue,
            send_task=send_task,
            listen_task=listen_task,
            session_id="sess",
            sample_rate=10,
        )
        return sent_items

    sent_items = asyncio.run(_run())

    assert sent_items == [b"last-audio", b"\x00\x00", None]


def test_stop_mechanism_applies_configured_grace_before_stop() -> None:
    async def _run() -> float:
        state = deepgram_stream.StreamState()
        external_stop = threading.Event()
        loop = asyncio.get_running_loop()
        mechanism = deepgram_stream._setup_stop_mechanism(
            state,
            loop,
            external_stop,
            "sess",
            stop_grace_seconds=0.05,
        )

        started = time.monotonic()
        external_stop.set()
        await asyncio.wait_for(state.stop_event.wait(), timeout=1.0)
        elapsed = time.monotonic() - started
        deepgram_stream._cleanup_stop_mechanism(loop, external_stop, mechanism)
        return elapsed

    assert asyncio.run(_run()) >= 0.045


def test_stop_mechanism_cleanup_wakes_watcher_without_external_stop() -> None:
    async def _run() -> bool:
        state = deepgram_stream.StreamState()
        external_stop = threading.Event()
        loop = asyncio.get_running_loop()
        mechanism = deepgram_stream._setup_stop_mechanism(
            state,
            loop,
            external_stop,
            "sess",
        )
        watcher = mechanism.watcher_thread
        assert watcher is not None and watcher.is_alive()

        deepgram_stream._cleanup_stop_mechanism(loop, external_stop, mechanism)
        return watcher.is_alive()

    assert asyncio.run(_run()) is False


def test_finish_warm_forwarder_flushes_threadsafe_audio_before_sentinel() -> None:
    class _ForwarderThread:
        def __init__(self) -> None:
            self.join_timeout = None

        def join(self, timeout=None) -> None:
            self.join_timeout = timeout

        def is_alive(self) -> bool:
            return False

    async def _run() -> tuple[list[bytes | None], float | None]:
        loop = asyncio.get_running_loop()
        audio_queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        forwarder = _ForwarderThread()
        audio_result = deepgram_stream.AudioSourceResult(
            sample_rate=16000,
            mic_stream=None,
            buffer_state=None,
            forwarder_thread=cast(Any, forwarder),
        )

        loop.call_soon_threadsafe(audio_queue.put_nowait, b"last-audio")
        await deepgram_stream._finish_warm_forwarder(audio_result, "sess")
        await audio_queue.put(None)

        items: list[bytes | None] = []
        while not audio_queue.empty():
            items.append(await audio_queue.get())
        return items, forwarder.join_timeout

    items, join_timeout = asyncio.run(_run())

    assert items == [b"last-audio", None]
    assert join_timeout == deepgram_stream.FORWARDER_THREAD_JOIN_TIMEOUT


def test_create_mic_stream_closes_native_stream_when_start_fails(monkeypatch) -> None:
    class _FailingStream:
        def __init__(self) -> None:
            self.close_calls = 0

        def start(self) -> None:
            raise RuntimeError("start failed")

        def close(self) -> None:
            self.close_calls += 1

    stream = _FailingStream()
    monkeypatch.setattr(deepgram_stream, "get_input_device", lambda: (0, 16000))
    monkeypatch.setattr(
        deepgram_stream,
        "create_low_latency_input_stream",
        lambda *_args, **_kwargs: stream,
    )

    with pytest.raises(RuntimeError, match="start failed"):
        deepgram_stream._create_mic_stream(lambda *_args: None, "sess", 0.0)

    assert stream.close_calls == 1


def test_start_failure_with_stuck_close_blocks_additional_mic_streams(
    monkeypatch,
) -> None:
    release = threading.Event()
    close_started = threading.Event()
    created_streams = 0

    class _BlockingCloseStream:
        def start(self) -> None:
            raise RuntimeError("start failed")

        def close(self) -> None:
            close_started.set()
            release.wait(timeout=1.0)

    def create_stream(*_args, **_kwargs):
        nonlocal created_streams
        created_streams += 1
        return _BlockingCloseStream()

    monkeypatch.setattr(deepgram_stream, "get_input_device", lambda: (0, 16000))
    monkeypatch.setattr(
        deepgram_stream,
        "create_low_latency_input_stream",
        create_stream,
    )
    monkeypatch.setattr(deepgram_stream, "MIC_STREAM_CLOSE_TIMEOUT", 0.01)

    try:
        with pytest.raises(RuntimeError, match="start failed"):
            deepgram_stream._create_mic_stream(lambda *_args: None, "sess", 0.0)
        assert close_started.wait(timeout=1.0)
        with pytest.raises(RuntimeError, match="Mikrofon-Cleanup hängt"):
            deepgram_stream._create_mic_stream(lambda *_args: None, "sess", 0.0)
        assert created_streams == 1
    finally:
        release.set()
        close_thread = deepgram_stream._MIC_STREAM_CLOSE_THREAD
        if close_thread is not None:
            close_thread.join(timeout=1.0)
        assert deepgram_stream._mic_stream_cleanup_is_stuck() is False


def test_close_exception_blocks_additional_mic_streams(monkeypatch) -> None:
    created_streams = 0

    class _CloseFailingStream:
        def start(self) -> None:
            raise RuntimeError("start failed")

        def close(self) -> None:
            raise OSError("close failed")

    def create_stream(*_args, **_kwargs):
        nonlocal created_streams
        created_streams += 1
        return _CloseFailingStream()

    monkeypatch.setattr(deepgram_stream, "get_input_device", lambda: (0, 16000))
    monkeypatch.setattr(
        deepgram_stream,
        "create_low_latency_input_stream",
        create_stream,
    )

    try:
        with pytest.raises(RuntimeError, match="start failed"):
            deepgram_stream._create_mic_stream(lambda *_args: None, "sess", 0.0)
        assert deepgram_stream._mic_stream_cleanup_is_stuck() is True
        with pytest.raises(RuntimeError, match="Mikrofon-Cleanup hängt"):
            deepgram_stream._create_mic_stream(lambda *_args: None, "sess", 0.0)
        assert created_streams == 1
    finally:
        with deepgram_stream._MIC_STREAM_CLOSE_LOCK:
            deepgram_stream._MIC_STREAM_CLOSE_THREAD = None
            deepgram_stream._MIC_STREAM_CLOSE_ERROR = None


def test_mic_close_thread_is_started_before_tracker_becomes_observable(
    monkeypatch,
) -> None:
    start_entered = threading.Event()
    allow_start = threading.Event()

    class _DelayedStartThread:
        def __init__(self, *_args, **_kwargs) -> None:
            self._alive = False

        def start(self) -> None:
            start_entered.set()
            allow_start.wait(timeout=1.0)
            self._alive = True

        def join(self, timeout=None) -> None:
            self._alive = False

        def is_alive(self) -> bool:
            return self._alive

    monkeypatch.setattr(
        deepgram_stream,
        "threading",
        SimpleNamespace(Thread=_DelayedStartThread),
    )
    owner = threading.Thread(
        target=lambda: deepgram_stream._close_mic_stream_bounded(object(), "sess")
    )

    try:
        owner.start()
        assert start_entered.wait(timeout=1.0)
        # Readers cannot observe the published-but-not-started Thread object.
        assert not deepgram_stream._MIC_STREAM_CLOSE_LOCK.acquire(timeout=0.02)
    finally:
        allow_start.set()
        owner.join(timeout=1.0)
        with deepgram_stream._MIC_STREAM_CLOSE_LOCK:
            deepgram_stream._MIC_STREAM_CLOSE_THREAD = None
            deepgram_stream._MIC_STREAM_CLOSE_ERROR = None


def test_warm_stream_thread_start_failure_disarms_source(monkeypatch) -> None:
    warm_source = _make_warm_source([])

    class _FailingThread:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def start(self) -> None:
            raise RuntimeError("thread start failed")

    monkeypatch.setattr(
        deepgram_stream,
        "threading",
        SimpleNamespace(Thread=_FailingThread),
    )

    async def _run() -> None:
        with pytest.raises(RuntimeError, match="thread start failed"):
            deepgram_stream._init_warm_stream(
                warm_source,
                deepgram_stream.StreamState(),
                asyncio.get_running_loop(),
                asyncio.Queue(),
                "sess",
                False,
                time.perf_counter(),
            )

    asyncio.run(_run())

    assert warm_source.arm_event.is_set() is False
    assert warm_source.drain_event is not None
    assert warm_source.drain_event.is_set() is False


def test_audio_cleanup_closes_stream_even_when_stop_fails() -> None:
    class _StopFailingStream:
        active = True

        def __init__(self) -> None:
            self.close_calls = 0

        def stop(self) -> None:
            raise RuntimeError("stop failed")

        def close(self) -> None:
            self.close_calls += 1

    stream = _StopFailingStream()
    deepgram_stream._cleanup_deepgram_audio_source(
        audio_result=deepgram_stream.AudioSourceResult(
            sample_rate=16000,
            mic_stream=cast(Any, stream),
            buffer_state=None,
        ),
        warm_stream_source=None,
        session_id="sess",
    )

    assert stream.close_calls == 1


def test_listener_failure_stops_session() -> None:
    class _FailingConnection:
        async def start_listening(self) -> None:
            raise OSError("listener failed")

    async def _run() -> deepgram_stream.StreamState:
        state = deepgram_stream.StreamState()
        await deepgram_stream._listen_for_deepgram_messages(
            connection=cast(Any, _FailingConnection()),
            state=state,
            session_id="sess",
        )
        return state

    state = asyncio.run(_run())

    assert isinstance(state.stream_error, OSError)
    assert state.stop_event.is_set()


def test_write_interim_text_replaces_file_atomically(tmp_path) -> None:
    interim_file = tmp_path / "interim.txt"
    interim_file.write_text("old interim", encoding="utf-8")

    deepgram_stream._write_interim_text(interim_file, "new interim")

    assert interim_file.read_text(encoding="utf-8") == "new interim"
    assert not interim_file.with_name("interim.txt.tmp").exists()


def test_write_interim_text_preserves_existing_file_on_temp_write_error(
    tmp_path, monkeypatch
) -> None:
    interim_file = tmp_path / "interim.txt"
    interim_file.write_text("stable interim", encoding="utf-8")
    tmp_file = interim_file.with_name("interim.txt.tmp")

    path_cls = type(interim_file)
    original_write_text = path_cls.write_text

    def failing_write_text(self, text: str, *args, **kwargs):
        if self == tmp_file:
            raise OSError("disk full")
        return original_write_text(self, text, *args, **kwargs)

    monkeypatch.setattr(path_cls, "write_text", failing_write_text)

    with pytest.raises(OSError, match="disk full"):
        deepgram_stream._write_interim_text(interim_file, "new interim")

    assert interim_file.read_text(encoding="utf-8") == "stable interim"
    assert not tmp_file.exists()


# =============================================================================
# Adaptive Pre-Drain (snappier Stop->Text Latenz)
# =============================================================================


class _ImmediateLoop:
    """Loop-Stub: führt call_soon_threadsafe synchron aus (für Pre-Drain-Tests)."""

    def call_soon_threadsafe(self, fn, *args) -> None:
        fn(*args)


def _make_warm_source(chunks: list[bytes]) -> deepgram_stream.WarmStreamSource:
    src_queue: queue.Queue[bytes] = queue.Queue()
    for chunk in chunks:
        src_queue.put_nowait(chunk)
    return deepgram_stream.WarmStreamSource(
        audio_queue=src_queue,
        sample_rate=16000,
        arm_event=threading.Event(),
        stream=cast(Any, object()),
        drain_event=threading.Event(),
    )


class _TimeoutQueue:
    """Queue stub that advances fake monotonic time instead of sleeping."""

    def __init__(self, clock: list[float]):
        self._clock = clock

    def get(self, timeout: float):
        self._clock[0] += timeout
        raise queue.Empty


def test_pre_drain_exits_early_when_queue_empty_but_respects_floor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Empty queue: stop after the minimum floor instead of the full duration."""
    warm_source = _make_warm_source([])
    fake_time = [1000.0]
    warm_source.audio_queue = cast(Any, _TimeoutQueue(fake_time))
    out_queue: queue.Queue[bytes | None] = queue.Queue()
    monkeypatch.setattr(deepgram_stream.time, "monotonic", lambda: fake_time[0])

    start = time.monotonic()
    drained = deepgram_stream._pre_drain_warm_stream(
        warm_source=warm_source,
        loop=cast(Any, _ImmediateLoop()),
        audio_queue=cast(Any, out_queue),
    )
    elapsed = time.monotonic() - start

    assert drained == 0
    # Floor honoured so a late callback block can still arrive ...
    assert elapsed >= deepgram_stream.PRE_DRAIN_MIN_DURATION - 0.005
    # ... but it must not burn the full (safe) PRE_DRAIN_DURATION anymore.
    assert elapsed < deepgram_stream.PRE_DRAIN_DURATION


def test_pre_drain_forwards_all_pending_chunks() -> None:
    """Pending warm chunks are forwarded to the async queue before disarming."""
    warm_source = _make_warm_source([b"a", b"b", b"c"])
    out_queue: queue.Queue[bytes | None] = queue.Queue()

    drained = deepgram_stream._pre_drain_warm_stream(
        warm_source=warm_source,
        loop=cast(Any, _ImmediateLoop()),
        audio_queue=cast(Any, out_queue),
    )

    forwarded: list[bytes] = []
    while not out_queue.empty():
        forwarded.append(out_queue.get_nowait())

    assert drained == 3
    assert forwarded == [b"a", b"b", b"c"]


# =============================================================================
# Deepgram Warm-WebSocket
# =============================================================================


class _FakeWebSocket:
    def __init__(self) -> None:
        self.closed = False


class _FakeWarmConnection:
    def __init__(self, name: str) -> None:
        self.name = name
        self._websocket = _FakeWebSocket()
        self.controls: list[str] = []
        self.enter_thread: int | None = None
        self.used_thread: int | None = None

    async def send_control(self, message) -> None:
        self.controls.append(message.type)

    async def start_listening(self) -> None:
        await asyncio.Future()


class _FakeConnectionContext:
    def __init__(
        self, connection: _FakeWarmConnection, *, fail_enter: bool = False
    ) -> None:
        self.connection = connection
        self.fail_enter = fail_enter
        self.exit_calls = 0

    async def __aenter__(self):
        if self.fail_enter:
            raise OSError("connect failed")
        self.connection.enter_thread = threading.get_ident()
        return self.connection

    async def __aexit__(self, _exc_type, _exc, _tb):
        self.exit_calls += 1
        self.connection._websocket.closed = True
        return False


def _install_fake_connection_factory(
    monkeypatch: pytest.MonkeyPatch,
    *,
    fail_enters: set[int] | None = None,
) -> list[_FakeConnectionContext]:
    contexts: list[_FakeConnectionContext] = []
    failures = fail_enters or set()

    def fake_create(_api_key: str, **_kwargs):
        index = len(contexts)
        context = _FakeConnectionContext(
            _FakeWarmConnection(f"connection-{index}"),
            fail_enter=index in failures,
        )
        contexts.append(context)
        return context

    monkeypatch.setattr(deepgram_stream, "_create_deepgram_connection", fake_create)
    return contexts


def _install_fake_stream_core(
    monkeypatch: pytest.MonkeyPatch,
    used_connections: list[_FakeWarmConnection],
    *,
    entered: threading.Event | None = None,
    release: threading.Event | None = None,
) -> None:
    async def fake_core(model: str, language: str | None, **kwargs) -> str:
        connection_factory = kwargs["connection_factory"]
        async with connection_factory(
            "test-key",
            model=model,
            language=language,
            sample_rate=16000,
            channels=1,
        ) as connection:
            connection.used_thread = threading.get_ident()
            used_connections.append(connection)
            if entered is not None:
                entered.set()
            while release is not None and not release.is_set():
                await asyncio.sleep(0.005)
        return "warm result"

    monkeypatch.setattr(deepgram_stream, "deepgram_stream_core", fake_core)


def test_core_connection_failure_reaps_stop_and_warm_forwarder_threads(
    monkeypatch,
) -> None:
    monkeypatch.setenv("DEEPGRAM_API_KEY", "test-key")
    warm_source = _make_warm_source([])
    external_stop = threading.Event()
    baseline_thread_ids = {id(thread) for thread in threading.enumerate()}

    @deepgram_stream.asynccontextmanager
    async def failing_connection_factory(_api_key: str, **_kwargs):
        raise OSError("connect failed")
        yield  # pragma: no cover

    with pytest.raises(OSError, match="connect failed"):
        asyncio.run(
            deepgram_stream.deepgram_stream_core(
                "nova-3",
                "de",
                play_ready=False,
                external_stop_event=external_stop,
                warm_stream_source=warm_source,
                connection_factory=failing_connection_factory,
            )
        )

    leaked_lifecycle_threads = [
        thread
        for thread in threading.enumerate()
        if id(thread) not in baseline_thread_ids
        and thread.name in {"StopWatcher", "WarmStreamForwarder"}
        and thread.is_alive()
    ]
    assert leaked_lifecycle_threads == []


def test_deepgram_stream_core_uses_injected_connection_factory(monkeypatch) -> None:
    monkeypatch.setenv("DEEPGRAM_API_KEY", "test-key")
    connection = _FakeWarmConnection("injected")
    context = _FakeConnectionContext(connection)
    factory_calls: list[dict[str, object]] = []
    shutdown_connections: list[object] = []

    @deepgram_stream.asynccontextmanager
    async def connection_factory(api_key: str, **kwargs):
        factory_calls.append({"api_key": api_key, **kwargs})
        async with context as client:
            yield client

    def fake_setup_stop(state, *_args, **_kwargs):
        state.stop_event.set()

    monkeypatch.setattr(deepgram_stream, "_setup_stop_mechanism", fake_setup_stop)
    monkeypatch.setattr(
        deepgram_stream,
        "_init_audio_source",
        lambda **_kwargs: deepgram_stream.AudioSourceResult(
            sample_rate=16000,
            mic_stream=None,
            buffer_state=None,
        ),
    )
    monkeypatch.setattr(
        deepgram_stream, "_register_deepgram_handlers", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        deepgram_stream,
        "_stop_audio_source_before_shutdown",
        lambda **_kwargs: asyncio.sleep(0),
    )

    async def fake_shutdown(*, connection, send_task, listen_task, **_kwargs):
        shutdown_connections.append(connection)
        send_task.cancel()
        listen_task.cancel()
        await asyncio.gather(send_task, listen_task, return_exceptions=True)

    monkeypatch.setattr(deepgram_stream, "_graceful_shutdown", fake_shutdown)

    result = asyncio.run(
        deepgram_stream.deepgram_stream_core(
            "nova-3",
            "de",
            connection_factory=connection_factory,
        )
    )

    assert result == ""
    assert factory_calls == [
        {
            "api_key": "test-key",
            "model": "nova-3",
            "language": "de",
            "sample_rate": 16000,
            "channels": 1,
        }
    ]
    assert shutdown_connections == [connection]
    assert context.exit_calls == 1


def _wait_until(predicate, *, timeout: float = 1.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return bool(predicate())


def test_warm_connection_manager_claims_once_and_replenishes(monkeypatch) -> None:
    monkeypatch.setenv("DEEPGRAM_API_KEY", "test-key")
    contexts = _install_fake_connection_factory(monkeypatch)
    used_connections: list[_FakeWarmConnection] = []
    _install_fake_stream_core(monkeypatch, used_connections)
    latency_events: list[str] = []
    manager = deepgram_stream.DeepgramWarmConnectionManager()

    try:
        assert manager.prewarm(model="nova-3", language="de", sample_rate=16000)
        assert manager.wait_until_ready(timeout=1.0)

        result = manager.transcribe(
            "nova-3",
            "de",
            latency_event_callback=lambda name, _fields=None: latency_events.append(
                name
            ),
        )

        assert result == "warm result"
        assert used_connections == [contexts[0].connection]
        assert contexts[0].connection.enter_thread == contexts[0].connection.used_thread
        assert contexts[0].exit_calls == 1
        assert "deepgram_warm_ws_claimed" in latency_events
        assert manager.wait_until_ready(timeout=1.0)
        assert len(contexts) == 2
    finally:
        manager.shutdown(timeout=1.0)


def test_warm_connection_manager_falls_back_after_prewarm_failure(monkeypatch) -> None:
    monkeypatch.setenv("DEEPGRAM_API_KEY", "test-key")
    contexts = _install_fake_connection_factory(monkeypatch, fail_enters={0})
    used_connections: list[_FakeWarmConnection] = []
    _install_fake_stream_core(monkeypatch, used_connections)
    latency_events: list[str] = []
    manager = deepgram_stream.DeepgramWarmConnectionManager()

    try:
        assert manager.prewarm(model="nova-3", language="de", sample_rate=16000)
        assert not manager.wait_until_ready(timeout=1.0)

        result = manager.transcribe(
            "nova-3",
            "de",
            latency_event_callback=lambda name, _fields=None: latency_events.append(
                name
            ),
        )

        assert result == "warm result"
        assert used_connections == [contexts[1].connection]
        assert "deepgram_warm_ws_fallback" in latency_events
        assert manager.wait_until_ready(timeout=1.0)
        assert len(contexts) == 3
    finally:
        manager.shutdown(timeout=1.0)


def test_warm_connection_manager_discards_stale_socket_before_claim(
    monkeypatch,
) -> None:
    monkeypatch.setenv("DEEPGRAM_API_KEY", "test-key")
    contexts = _install_fake_connection_factory(monkeypatch)
    used_connections: list[_FakeWarmConnection] = []
    _install_fake_stream_core(monkeypatch, used_connections)
    manager = deepgram_stream.DeepgramWarmConnectionManager()

    try:
        assert manager.prewarm(model="nova-3", language="de", sample_rate=16000)
        assert manager.wait_until_ready(timeout=1.0)
        contexts[0].connection._websocket.closed = True

        assert manager.transcribe("nova-3", "de") == "warm result"

        assert contexts[0].exit_calls == 1
        assert used_connections == [contexts[1].connection]
    finally:
        manager.shutdown(timeout=1.0)


def test_warm_connection_manager_replaces_socket_when_config_changes(
    monkeypatch,
) -> None:
    monkeypatch.setenv("DEEPGRAM_API_KEY", "test-key")
    contexts = _install_fake_connection_factory(monkeypatch)
    manager = deepgram_stream.DeepgramWarmConnectionManager()

    try:
        assert manager.prewarm(model="nova-3", language="de", sample_rate=16000)
        assert manager.wait_until_ready(timeout=1.0)
        assert manager.prewarm(model="nova-2", language="en", sample_rate=16000)
        assert manager.wait_until_ready(timeout=1.0)

        assert contexts[0].exit_calls == 1
        assert len(contexts) == 2
    finally:
        manager.shutdown(timeout=1.0)


def test_cancelled_prewarm_still_closes_previous_socket(monkeypatch) -> None:
    monkeypatch.setenv("DEEPGRAM_API_KEY", "test-key")
    exit_started = threading.Event()
    release_exit = threading.Event()
    exit_completed = threading.Event()
    contexts: list[_FakeConnectionContext] = []

    class _BlockingExitContext(_FakeConnectionContext):
        async def __aexit__(self, _exc_type, _exc, _tb):
            self.exit_calls += 1
            exit_started.set()
            while not release_exit.is_set():
                await asyncio.sleep(0.005)
            self.connection._websocket.closed = True
            exit_completed.set()
            return False

    def fake_create(_api_key: str, **_kwargs):
        index = len(contexts)
        context_cls = _BlockingExitContext if index == 0 else _FakeConnectionContext
        context = context_cls(_FakeWarmConnection(f"connection-{index}"))
        contexts.append(context)
        return context

    monkeypatch.setattr(deepgram_stream, "_create_deepgram_connection", fake_create)
    manager = deepgram_stream.DeepgramWarmConnectionManager()

    try:
        assert manager.prewarm(model="nova-3", language="de", sample_rate=16000)
        assert manager.wait_until_ready(timeout=1.0)
        assert manager.prewarm(model="nova-2", language="de", sample_rate=16000)
        assert exit_started.wait(timeout=1.0)
        assert manager.prewarm(model="nova-1", language="de", sample_rate=16000)
        release_exit.set()

        assert manager.wait_until_ready(timeout=1.0)
        assert exit_completed.is_set()
        assert contexts[0].exit_calls == 1
    finally:
        release_exit.set()
        manager.shutdown(timeout=1.0)


def test_warm_connection_manager_invalidate_disables_replenishment(monkeypatch) -> None:
    monkeypatch.setenv("DEEPGRAM_API_KEY", "test-key")
    contexts = _install_fake_connection_factory(monkeypatch)
    used_connections: list[_FakeWarmConnection] = []
    _install_fake_stream_core(monkeypatch, used_connections)
    latency_events: list[str] = []
    manager = deepgram_stream.DeepgramWarmConnectionManager()

    try:
        assert manager.prewarm(model="nova-3", language="de", sample_rate=16000)
        assert manager.wait_until_ready(timeout=1.0)
        manager.invalidate()
        assert not manager.wait_until_ready(timeout=1.0)

        assert (
            manager.transcribe(
                "nova-3",
                "de",
                latency_event_callback=lambda name, _fields=None: latency_events.append(
                    name
                ),
            )
            == "warm result"
        )

        assert used_connections == [contexts[1].connection]
        assert "deepgram_warm_ws_fallback" in latency_events
        time.sleep(0.02)
        assert len(contexts) == 2
        assert not manager.wait_until_ready(timeout=0.05)
    finally:
        manager.shutdown(timeout=1.0)


def test_warm_connection_manager_sends_keepalive_while_idle(monkeypatch) -> None:
    monkeypatch.setenv("DEEPGRAM_API_KEY", "test-key")
    contexts = _install_fake_connection_factory(monkeypatch)
    manager = deepgram_stream.DeepgramWarmConnectionManager(keepalive_interval=0.01)

    try:
        assert manager.prewarm(model="nova-3", language="de", sample_rate=16000)
        assert manager.wait_until_ready(timeout=1.0)
        assert _wait_until(lambda: "KeepAlive" in contexts[0].connection.controls)
    finally:
        manager.shutdown(timeout=1.0)


def test_warm_connection_manager_rejects_parallel_transcriptions(monkeypatch) -> None:
    monkeypatch.setenv("DEEPGRAM_API_KEY", "test-key")
    _install_fake_connection_factory(monkeypatch)
    entered = threading.Event()
    release = threading.Event()
    _install_fake_stream_core(
        monkeypatch,
        [],
        entered=entered,
        release=release,
    )
    manager = deepgram_stream.DeepgramWarmConnectionManager()
    errors: list[BaseException] = []

    def run_first_transcription() -> None:
        try:
            manager.transcribe("nova-3", "de")
        except BaseException as exc:  # pragma: no cover - assertion below surfaces it
            errors.append(exc)

    thread = threading.Thread(target=run_first_transcription)
    try:
        thread.start()
        assert entered.wait(timeout=1.0)
        with pytest.raises(RuntimeError, match="already in use"):
            manager.transcribe("nova-3", "de")
    finally:
        release.set()
        thread.join(timeout=1.0)
        manager.shutdown(timeout=1.0)

    assert not thread.is_alive()
    assert errors == []


def test_warm_connection_manager_shutdown_stops_active_core_gracefully(
    monkeypatch,
) -> None:
    monkeypatch.setenv("DEEPGRAM_API_KEY", "test-key")
    _install_fake_connection_factory(monkeypatch)
    started = threading.Event()
    finalized = threading.Event()
    external_stop = threading.Event()
    errors: list[BaseException] = []

    async def fake_core(model: str, language: str | None, **kwargs) -> str:
        connection_factory = kwargs["connection_factory"]
        async with connection_factory(
            "test-key",
            model=model,
            language=language,
            sample_rate=16000,
            channels=1,
        ):
            started.set()
            while not kwargs["external_stop_event"].is_set():
                await asyncio.sleep(0.005)
            finalized.set()
        return "done"

    monkeypatch.setattr(deepgram_stream, "deepgram_stream_core", fake_core)
    manager = deepgram_stream.DeepgramWarmConnectionManager()

    def run_transcription() -> None:
        try:
            manager.transcribe(
                "nova-3",
                "de",
                external_stop_event=external_stop,
            )
        except BaseException as exc:  # pragma: no cover - assertion below surfaces it
            errors.append(exc)

    thread = threading.Thread(target=run_transcription)
    try:
        assert manager.prewarm(model="nova-3", language="de", sample_rate=16000)
        assert manager.wait_until_ready(timeout=1.0)
        thread.start()
        assert started.wait(timeout=1.0)

        manager.shutdown(timeout=1.0)
    finally:
        thread.join(timeout=1.0)
        manager.shutdown(timeout=1.0)

    assert external_stop.is_set()
    assert finalized.is_set()
    assert not thread.is_alive()
    assert errors == []


def test_message_handler_sets_final_transcript_event_on_final(monkeypatch) -> None:
    state = deepgram_stream.StreamState()
    handler = deepgram_stream._create_message_handler(state, "sess")
    monkeypatch.setattr(deepgram_stream.time, "perf_counter", lambda: 1.0)

    assert state.final_transcript_event.is_set() is False
    handler(_response("final transcript", is_final=True))

    assert state.final_transcript_event.is_set() is True


def _graceful_shutdown_tasks(audio_queue):
    async def _send_worker() -> None:
        while True:
            item = await audio_queue.get()
            if item is None:
                return

    async def _listen_worker() -> None:
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            return

    return _send_worker, _listen_worker


def test_finalize_send_timeout_skips_ack_wait_and_close_stream(monkeypatch) -> None:
    class _FakeControlMessage:
        def __init__(self, type: str) -> None:
            self.type = type

    monkeypatch.setitem(
        sys.modules,
        "deepgram.extensions.types.sockets",
        SimpleNamespace(ListenV1ControlMessage=_FakeControlMessage),
    )
    monkeypatch.setattr(deepgram_stream, "DEEPGRAM_TAIL_PADDING_SECONDS", 0.0)
    monkeypatch.setattr(deepgram_stream, "DEEPGRAM_FINALIZE_SEND_TIMEOUT", 0.01)
    monkeypatch.setattr(deepgram_stream, "DEEPGRAM_CLOSE_STREAM_SEND_TIMEOUT", 0.01)

    async def _run() -> tuple[list[str], list[str], bool]:
        state = deepgram_stream.StreamState()
        audio_queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        send_worker, listen_worker = _graceful_shutdown_tasks(audio_queue)
        send_task = asyncio.create_task(send_worker())
        listen_task = asyncio.create_task(listen_worker())
        controls: list[str] = []
        events: list[str] = []

        class _Connection:
            async def send_control(self, message) -> None:
                controls.append(message.type)
                if message.type == "Finalize":
                    await asyncio.Event().wait()

        await asyncio.wait_for(
            deepgram_stream._graceful_shutdown(
                connection=cast(Any, _Connection()),
                state=state,
                audio_queue=audio_queue,
                send_task=send_task,
                listen_task=listen_task,
                session_id="sess",
                latency_event_callback=lambda name, _fields=None: events.append(name),
            ),
            timeout=0.5,
        )
        return controls, events, listen_task.done()

    controls, events, listener_done = asyncio.run(_run())
    assert controls == ["Finalize"]
    assert "deepgram_finalize_send_timeout" in events
    assert "deepgram_finalize_timeout" not in events
    assert "deepgram_close_send_skipped" in events
    assert listener_done


def test_close_stream_send_timeout_still_cancels_listener(monkeypatch) -> None:
    class _FakeControlMessage:
        def __init__(self, type: str) -> None:
            self.type = type

    monkeypatch.setitem(
        sys.modules,
        "deepgram.extensions.types.sockets",
        SimpleNamespace(ListenV1ControlMessage=_FakeControlMessage),
    )
    monkeypatch.setattr(deepgram_stream, "DEEPGRAM_TAIL_PADDING_SECONDS", 0.0)
    monkeypatch.setattr(deepgram_stream, "DEEPGRAM_FINALIZE_SEND_TIMEOUT", 0.01)
    monkeypatch.setattr(deepgram_stream, "DEEPGRAM_CLOSE_STREAM_SEND_TIMEOUT", 0.01)

    async def _run() -> tuple[list[str], bool]:
        state = deepgram_stream.StreamState()
        audio_queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        send_worker, listen_worker = _graceful_shutdown_tasks(audio_queue)
        send_task = asyncio.create_task(send_worker())
        listen_task = asyncio.create_task(listen_worker())
        events: list[str] = []

        class _Connection:
            async def send_control(self, message) -> None:
                if message.type == "Finalize":
                    state.finalize_done.set()
                    return
                await asyncio.Event().wait()

        await asyncio.wait_for(
            deepgram_stream._graceful_shutdown(
                connection=cast(Any, _Connection()),
                state=state,
                audio_queue=audio_queue,
                send_task=send_task,
                listen_task=listen_task,
                session_id="sess",
                latency_event_callback=lambda name, _fields=None: events.append(name),
            ),
            timeout=0.5,
        )
        return events, listen_task.done()

    events, listener_done = asyncio.run(_run())
    assert "deepgram_close_send_timeout" in events
    assert listener_done


def test_graceful_shutdown_cancellation_reaps_stream_tasks(monkeypatch) -> None:
    class _FakeControlMessage:
        def __init__(self, type: str) -> None:
            self.type = type

    monkeypatch.setitem(
        sys.modules,
        "deepgram.extensions.types.sockets",
        SimpleNamespace(ListenV1ControlMessage=_FakeControlMessage),
    )
    monkeypatch.setattr(deepgram_stream, "DEEPGRAM_TAIL_PADDING_SECONDS", 0.0)
    monkeypatch.setattr(deepgram_stream, "DEEPGRAM_FINALIZE_SEND_TIMEOUT", 10.0)

    async def _run() -> tuple[bool, bool]:
        state = deepgram_stream.StreamState()
        audio_queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        send_worker, listen_worker = _graceful_shutdown_tasks(audio_queue)
        send_task = asyncio.create_task(send_worker())
        listen_task = asyncio.create_task(listen_worker())
        finalize_started = asyncio.Event()

        class _Connection:
            async def send_control(self, message) -> None:
                if message.type == "Finalize":
                    finalize_started.set()
                    await asyncio.Event().wait()

        shutdown_task = asyncio.create_task(
            deepgram_stream._graceful_shutdown(
                connection=cast(Any, _Connection()),
                state=state,
                audio_queue=audio_queue,
                send_task=send_task,
                listen_task=listen_task,
                session_id="sess",
            )
        )
        await asyncio.wait_for(finalize_started.wait(), timeout=0.5)
        shutdown_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await shutdown_task
        return send_task.done(), listen_task.done()

    assert asyncio.run(_run()) == (True, True)


def test_warm_keepalive_timeout_discards_and_reconnects(
    monkeypatch,
    caplog,
) -> None:
    caplog.set_level(logging.DEBUG, logger=deepgram_stream.logger.name)
    monkeypatch.setattr(deepgram_stream, "DEEPGRAM_KEEPALIVE_SEND_TIMEOUT", 0.01)

    async def _run() -> tuple[int, int]:
        manager = deepgram_stream.DeepgramWarmConnectionManager(keepalive_interval=0.01)
        config = deepgram_stream.DeepgramConnectionConfig(
            api_key="test",
            model="nova-3",
            language="de",
            sample_rate=16000,
        )
        exit_calls = 0
        reconnect_calls = 0

        class _Connection:
            async def send_control(self, _message) -> None:
                await asyncio.Event().wait()

        class _Context:
            async def __aexit__(self, *_args) -> None:
                nonlocal exit_calls
                exit_calls += 1

        prepared = deepgram_stream._PreparedDeepgramConnection(
            config=config,
            context=cast(Any, _Context()),
            connection=cast(Any, _Connection()),
        )
        manager._prepared = prepared
        manager._enabled = True
        manager._desired_config = config

        async def _request_prewarm(requested_config) -> None:
            nonlocal reconnect_calls
            assert requested_config == config
            reconnect_calls += 1

        monkeypatch.setattr(manager, "_request_prewarm", _request_prewarm)
        await asyncio.wait_for(manager._keepalive(prepared), timeout=0.5)
        assert manager._prepared is None
        return exit_calls, reconnect_calls

    assert asyncio.run(_run()) == (1, 1)
    assert "KeepAlive Timeout" in caplog.text


def test_empty_finalize_grace_exits_early_on_late_final(monkeypatch) -> None:
    """Ein spätes Final-Transkript beendet die Empty-Finalize-Grace sofort.

    Kausal statt Wall-Clock: Die Grace ist auf 30s konfiguriert, das späte
    Final kommt rein event-getrieben (Loop-Yields statt Sleep). Nur wenn der
    Early-Exit greift, terminiert der Shutdown innerhalb des großzügigen
    äußeren Hänge-Timeouts - ohne enge elapsed-Assertion, die auf langsamen
    CI-Runnern flaken könnte.
    """

    class _FakeControlMessage:
        def __init__(self, type: str) -> None:
            self.type = type

    monkeypatch.setitem(
        sys.modules,
        "deepgram.extensions.types.sockets",
        SimpleNamespace(ListenV1ControlMessage=_FakeControlMessage),
    )
    monkeypatch.setattr(deepgram_stream, "DEEPGRAM_TAIL_PADDING_SECONDS", 0.0)
    monkeypatch.setattr(deepgram_stream, "DEEPGRAM_EMPTY_FINALIZE_GRACE_SECONDS", 30.0)

    async def _run() -> None:
        state = deepgram_stream.StreamState()
        audio_queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        send_worker, listen_worker = _graceful_shutdown_tasks(audio_queue)
        send_task = asyncio.create_task(send_worker())
        listen_task = asyncio.create_task(listen_worker())

        class _FakeConnection:
            async def send_control(self, message) -> None:
                if getattr(message, "type", "") == "Finalize":
                    # Deepgram sendet nur einen leeren Finalize-Ack ...
                    deepgram_stream._mark_finalize_response(state, has_transcript=False)

                    async def _late_final() -> None:
                        # Rein kausal: ein paar Loop-Yields, dann das Final.
                        for _ in range(5):
                            await asyncio.sleep(0)
                        deepgram_stream._handle_final_transcript(
                            state, session_id="sess", transcript="late final"
                        )

                    # ... und das echte Final-Transkript kommt kurz danach.
                    asyncio.ensure_future(_late_final())

        # Äußerer Hänge-Guard: Ohne Early-Exit würde die 30s-Grace hier in
        # einen TimeoutError laufen und den Test fehlschlagen lassen.
        await asyncio.wait_for(
            deepgram_stream._graceful_shutdown(
                connection=cast(Any, _FakeConnection()),
                state=state,
                audio_queue=audio_queue,
                send_task=send_task,
                listen_task=listen_task,
                session_id="sess",
                sample_rate=16000,
            ),
            timeout=5.0,
        )
        assert state.final_transcripts == ["late final"]

    asyncio.run(_run())


def test_empty_finalize_grace_ignores_finals_from_before_finalize(
    monkeypatch,
) -> None:
    """Finals aus der Aufnahmephase dürfen die Grace nicht vorzeitig beenden."""

    class _FakeControlMessage:
        def __init__(self, type: str) -> None:
            self.type = type

    monkeypatch.setitem(
        sys.modules,
        "deepgram.extensions.types.sockets",
        SimpleNamespace(ListenV1ControlMessage=_FakeControlMessage),
    )
    monkeypatch.setattr(deepgram_stream, "DEEPGRAM_TAIL_PADDING_SECONDS", 0.0)
    monkeypatch.setattr(deepgram_stream, "DEEPGRAM_EMPTY_FINALIZE_GRACE_SECONDS", 0.2)

    async def _run() -> float:
        state = deepgram_stream.StreamState()
        # Final-Transkript aus der Aufnahmephase (vor Finalize).
        deepgram_stream._handle_final_transcript(
            state, session_id="sess", transcript="early final"
        )
        audio_queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        send_worker, listen_worker = _graceful_shutdown_tasks(audio_queue)
        send_task = asyncio.create_task(send_worker())
        listen_task = asyncio.create_task(listen_worker())

        class _FakeConnection:
            async def send_control(self, message) -> None:
                if getattr(message, "type", "") == "Finalize":
                    deepgram_stream._mark_finalize_response(state, has_transcript=False)

        started = time.perf_counter()
        await deepgram_stream._graceful_shutdown(
            connection=cast(Any, _FakeConnection()),
            state=state,
            audio_queue=audio_queue,
            send_task=send_task,
            listen_task=listen_task,
            session_id="sess",
            sample_rate=16000,
        )
        return time.perf_counter() - started

    elapsed = asyncio.run(_run())

    # Volle Grace wurde gewartet, weil kein NEUES Final eintraf.
    assert elapsed >= 0.18


def test_stop_mechanism_resolves_callable_grace_at_stop_time(monkeypatch) -> None:
    """Ein Grace-Callable (adaptiver Stop-Tail) wird erst NACH dem Stop-Signal
    ausgewertet - nicht beim Setup der Session."""
    external_stop = threading.Event()
    resolver_calls: list[bool] = []

    def adaptive_grace() -> float:
        # Beim Aufruf MUSS das Stop-Signal bereits gesetzt sein
        resolver_calls.append(external_stop.is_set())
        return 0.05

    async def _run() -> None:
        state = deepgram_stream.StreamState()
        loop = asyncio.get_running_loop()
        mechanism = deepgram_stream._setup_stop_mechanism(
            state,
            loop,
            external_stop,
            "sess",
            stop_grace_seconds=adaptive_grace,
        )
        # Setup abgeschlossen: Resolver darf noch nicht gelaufen sein
        assert resolver_calls == []

        external_stop.set()
        await asyncio.wait_for(state.stop_event.wait(), timeout=1.0)
        deepgram_stream._cleanup_stop_mechanism(loop, external_stop, mechanism)

    asyncio.run(_run())

    assert resolver_calls == [True]


def test_stop_mechanism_failing_grace_resolver_does_not_block_stop(
    monkeypatch,
) -> None:
    """Ein fehlschlagender Resolver darf den Stop nie verhindern (Grace 0)."""
    sleep_calls: list[float] = []
    monkeypatch.setattr(
        deepgram_stream.time,
        "sleep",
        lambda seconds: sleep_calls.append(seconds),
    )

    def broken_resolver() -> float:
        raise RuntimeError("resolver kaputt")

    async def _run() -> None:
        state = deepgram_stream.StreamState()
        external_stop = threading.Event()
        loop = asyncio.get_running_loop()
        mechanism = deepgram_stream._setup_stop_mechanism(
            state,
            loop,
            external_stop,
            "sess",
            stop_grace_seconds=broken_resolver,
        )
        external_stop.set()
        await asyncio.wait_for(state.stop_event.wait(), timeout=1.0)
        deepgram_stream._cleanup_stop_mechanism(loop, external_stop, mechanism)

    asyncio.run(_run())

    assert sleep_calls == []
