"""Tests for CLI buffer overflow behavior in deepgram_stream."""

from __future__ import annotations

import asyncio

import pytest

import providers.deepgram_stream as deepgram_stream


@pytest.fixture()
def buffer_env(monkeypatch):
    """Provide a minimal buffer environment for _handle_buffered_audio tests."""
    monkeypatch.setattr(deepgram_stream, "CLI_BUFFER_LIMIT", 2)
    loop = asyncio.new_event_loop()
    try:
        yield {
            "buffer_state": deepgram_stream.BufferState(),
            "state": deepgram_stream.StreamState(),
            "loop": loop,
            "audio_queue": asyncio.Queue(),
            "session_id": "test",
        }
    finally:
        loop.close()


def _send(env, chunk: bytes) -> None:
    """Shorthand for calling _handle_buffered_audio with the test environment."""
    deepgram_stream._handle_buffered_audio(
        env["buffer_state"],
        chunk,
        env["state"],
        env["session_id"],
        env["loop"],
        env["audio_queue"],
    )


def test_buffer_overflow_discards_chunks_instead_of_bypassing(buffer_env) -> None:
    """Chunks must be discarded when buffer is full, not sent to asyncio queue.

    Regression test: Previously, after the overflow warning was logged,
    subsequent chunks fell through to loop.call_soon_threadsafe() and
    bypassed the buffer, causing out-of-order audio delivery.
    """
    # Fill buffer to limit
    _send(buffer_env, b"chunk1")
    _send(buffer_env, b"chunk2")
    assert len(buffer_env["buffer_state"].buffer) == 2

    # Overflow: first extra chunk triggers warning and is discarded
    _send(buffer_env, b"overflow1")
    assert buffer_env["state"].buffer_overflow_logged is True
    assert len(buffer_env["buffer_state"].buffer) == 2

    # Second overflow: must also be discarded (not bypass to queue)
    _send(buffer_env, b"overflow2")
    assert len(buffer_env["buffer_state"].buffer) == 2

    # asyncio queue must be empty (no chunks bypassed the buffer)
    assert buffer_env["audio_queue"].empty()


def test_buffer_sends_directly_after_deactivation(buffer_env) -> None:
    """After buffer is deactivated, chunks go directly to asyncio queue."""
    buffer_env["buffer_state"].active = False

    _send(buffer_env, b"direct")

    # Process the call_soon_threadsafe callback
    buffer_env["loop"].run_until_complete(asyncio.sleep(0))
    assert not buffer_env["audio_queue"].empty()
    chunk = buffer_env["audio_queue"].get_nowait()
    assert chunk == b"direct"
