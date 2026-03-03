import io
import subprocess
import sys

import pytest

from utils.subprocess_io import start_stream_drain_thread


def test_start_stream_drain_thread_returns_none_for_missing_stream() -> None:
    assert start_stream_drain_thread(None) is None


def test_start_stream_drain_thread_rejects_invalid_chunk_size() -> None:
    with pytest.raises(ValueError):
        start_stream_drain_thread(io.BytesIO(b""), chunk_size=0)


def test_start_stream_drain_thread_prevents_pipe_backpressure() -> None:
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            (
                "import sys, time; "
                "sys.stderr.write('x' * 200000); "
                "sys.stderr.flush(); "
                "time.sleep(0.05)"
            ),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )

    thread = start_stream_drain_thread(
        process.stderr,
        thread_name="test-subprocess-stderr-drain",
    )

    try:
        assert thread is not None
        assert process.wait(timeout=3) == 0
        thread.join(timeout=1)
        assert not thread.is_alive()
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=1)
