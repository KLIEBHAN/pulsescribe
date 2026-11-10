"""Live text preview from a streaming speech model.

``StreamingPreview`` feeds a running recording into a streaming model chunk by
chunk and publishes the words as the model emits them, a fraction of a second
after they are spoken. The text is a preview only: the transcript still comes
from a normal pass over the whole recording.

It has the calls of ``audio.live_preview.LivePreview`` (``start``, ``finish``,
``final_text_for``), so a recording can run either; ``finish`` returns this
module's own ``StreamingSummary``.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

logger = logging.getLogger("pulsescribe.audio")

POLL_S = 0.05
# A model that falls this far behind the microphone is too slow for this Mac.
MAX_BACKLOG_S = 2.0
# Longest text published; the overlay shows about 120 characters.
MAX_TEXT_CHARS = 600
# Longest wait for the running step when the recording ends. A step takes about
# 0.1 s and loading the model 0.5 s; the transcript must not wait for a stall.
FINISH_TIMEOUT_S = 1.0


class PreviewSession(Protocol):
    """What one recording needs from the model (``StreamingSession`` fits)."""

    def feed(self, samples: Any) -> None: ...

    def text(self) -> str: ...


@dataclass(frozen=True)
class StreamingSummary:
    """What a streaming preview did during one recording, for the log."""

    updates: int  # texts published
    compute_share: float  # computing time per second of recording (1.0 = real time)
    step_max_s: float  # longest single step
    too_slow: bool  # ended early: fell MAX_BACKLOG_S behind the microphone
    failed: bool  # the model raised; the error is in the log


class StreamingPreview:
    """Runs the streaming model on a thread while a recording is running.

    ``open_session()`` returns the model session for this recording; it runs on
    the preview thread and may take a moment (loading the model).
    ``publish(text)`` receives each new preview text on that thread; an empty
    text means the preview ended early and its text must disappear.

    Call ``start`` once the audio callback fills ``chunks`` (float32 mono
    arrays at ``sample_rate``, the rate the session expects), then ``finish``
    after the recording stopped.
    """

    def __init__(
        self,
        *,
        open_session: Callable[[], PreviewSession],
        publish: Callable[[str], None],
        sample_rate: int,
    ) -> None:
        self._open_session = open_session
        self._publish = publish
        self._sample_rate = sample_rate
        self._finished = threading.Event()
        self._thread: threading.Thread | None = None
        self._steps: list[float] = []
        self._fed_s = 0.0
        self._updates = 0
        self._shown = ""
        self._too_slow = False
        self._failed = False

    def start(
        self,
        chunks: Sequence[Any],
        levels: Sequence[float],
        stop_event: threading.Event,
    ) -> None:
        """Start previewing ``chunks``; ``levels`` is unused. Ends once ``stop_event`` is set."""
        self._thread = threading.Thread(
            target=self._run,
            args=(chunks, stop_event),
            daemon=True,
            name="StreamingPreview",
        )
        self._thread.start()

    def finish(self) -> StreamingSummary:
        """Stop previewing; waits at most ``FINISH_TIMEOUT_S`` for the running step."""
        self._finished.set()
        if self._thread is not None:
            self._thread.join(timeout=FINISH_TIMEOUT_S)
            if self._thread.is_alive():
                # The thread ends by itself once the model call returns.
                self._failed = True
                logger.warning(
                    "Live-Vorschau (Streaming) reagiert nicht, Aufnahme läuft ohne sie weiter"
                )
            self._thread = None
        return StreamingSummary(
            updates=self._updates,
            compute_share=sum(self._steps) / self._fed_s if self._fed_s else 0.0,
            step_max_s=max(self._steps, default=0.0),
            too_slow=self._too_slow,
            failed=self._failed,
        )

    def final_text_for(self, audio: Any) -> None:
        """The preview text is never the transcript."""
        return None

    def _run(self, chunks: Sequence[Any], stop_event: threading.Event) -> None:
        import numpy as np

        try:
            if self._finished.is_set() or stop_event.is_set():
                return  # nothing to show any more; do not load the model for it
            session = self._open_session()
            fed = 0
            while not self._finished.is_set() and not stop_event.wait(POLL_S):
                count = len(chunks)
                if count == fed:
                    continue
                samples = np.concatenate(chunks[fed:count]).reshape(-1)
                fed = count
                started_at = time.perf_counter()
                session.feed(samples)
                self._steps.append(time.perf_counter() - started_at)
                self._fed_s += len(samples) / self._sample_rate

                behind = sum(len(chunk) for chunk in chunks[fed:]) / self._sample_rate
                if behind > MAX_BACKLOG_S:
                    self._too_slow = True
                    self._show("")
                    return
                self._show(_tail(" ".join(session.text().split())))
        except Exception:
            # The preview is optional; the final pass still transcribes everything.
            self._failed = True
            self._show("")
            logger.exception("Live-Vorschau (Streaming) abgebrochen")

    def _show(self, text: str) -> None:
        if text == self._shown:
            return
        self._shown = text
        if text:
            self._updates += 1
        self._publish(text)


def _tail(text: str) -> str:
    """The last ``MAX_TEXT_CHARS`` of ``text``, cut at a word."""
    if len(text) <= MAX_TEXT_CHARS:
        return text
    tail = text[-MAX_TEXT_CHARS:]
    return tail[tail.find(" ") + 1 :]  # no space: text in a script without spaces


__all__ = ["PreviewSession", "StreamingPreview", "StreamingSummary"]
