"""Tests for CLI buffer overflow behavior in deepgram_stream."""

from __future__ import annotations

import asyncio

import providers.deepgram_stream as deepgram_stream


def test_buffer_overflow_discards_chunks_instead_of_bypassing(monkeypatch) -> None:
    """Chunks must be discarded when buffer is full, not sent to asyncio queue.

    Regression test: Previously, after the overflow warning was logged,
    subsequent chunks fell through to loop.call_soon_threadsafe() and
    bypassed the buffer, causing out-of-order audio delivery.
    """
    monkeypatch.setattr(deepgram_stream, "CLI_BUFFER_LIMIT", 2)

    buffer_state = deepgram_stream.BufferState()
    state = deepgram_stream.StreamState()
    loop = asyncio.new_event_loop()
    audio_queue: asyncio.Queue[bytes | None] = asyncio.Queue()

    try:
        session_id = "test"

        # Fill buffer to limit
        deepgram_stream._handle_buffered_audio(
            buffer_state, b"chunk1", state, session_id, loop, audio_queue
        )
        deepgram_stream._handle_buffered_audio(
            buffer_state, b"chunk2", state, session_id, loop, audio_queue
        )
        assert len(buffer_state.buffer) == 2

        # Overflow: first extra chunk triggers warning and is discarded
        deepgram_stream._handle_buffered_audio(
            buffer_state, b"overflow1", state, session_id, loop, audio_queue
        )
        assert state.buffer_overflow_logged is True
        assert len(buffer_state.buffer) == 2  # Not added to buffer

        # Second overflow: must also be discarded (not bypass to queue)
        deepgram_stream._handle_buffered_audio(
            buffer_state, b"overflow2", state, session_id, loop, audio_queue
        )
        assert len(buffer_state.buffer) == 2  # Still 2

        # asyncio queue must be empty (no chunks bypassed the buffer)
        assert audio_queue.empty()
    finally:
        loop.close()


def test_buffer_sends_directly_after_deactivation(monkeypatch) -> None:
    """After buffer is deactivated, chunks go directly to asyncio queue."""
    monkeypatch.setattr(deepgram_stream, "CLI_BUFFER_LIMIT", 2)

    buffer_state = deepgram_stream.BufferState()
    state = deepgram_stream.StreamState()
    loop = asyncio.new_event_loop()
    audio_queue: asyncio.Queue[bytes | None] = asyncio.Queue()

    try:
        session_id = "test"

        # Deactivate buffer (simulates WebSocket connected)
        buffer_state.active = False

        deepgram_stream._handle_buffered_audio(
            buffer_state, b"direct", state, session_id, loop, audio_queue
        )

        # Process the call_soon_threadsafe callback
        loop.run_until_complete(asyncio.sleep(0))
        assert not audio_queue.empty()
        chunk = audio_queue.get_nowait()
        assert chunk == b"direct"
    finally:
        loop.close()
