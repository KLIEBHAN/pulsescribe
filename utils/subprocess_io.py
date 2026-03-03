"""Helpers for safe subprocess stream handling."""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import BinaryIO


def start_stream_drain_thread(
    stream: BinaryIO | None,
    *,
    chunk_size: int = 4096,
    thread_name: str = "pulsescribe-stream-drain",
    on_error: Callable[[Exception], None] | None = None,
) -> threading.Thread | None:
    """Continuously read from a binary stream in a daemon thread.

    This prevents child processes from blocking when writing to stdout/stderr
    pipes that are otherwise never consumed by the parent process.
    """
    if stream is None:
        return None
    if chunk_size <= 0:
        raise ValueError("chunk_size must be > 0")

    def _drain() -> None:
        try:
            while True:
                chunk = stream.read(chunk_size)
                if not chunk:
                    break
        except Exception as exc:
            if on_error:
                on_error(exc)
        finally:
            try:
                stream.close()
            except Exception:
                pass

    thread = threading.Thread(target=_drain, daemon=True, name=thread_name)
    thread.start()
    return thread
