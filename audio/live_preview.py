"""Live text preview for local recordings.

Local Whisper has no streaming mode: it transcribes a finished recording. To
show text while the user still speaks, ``LivePreview`` transcribes the recording
so far again and again and publishes the result. The final transcript stays a
normal pass over the whole recording.

Two kinds of passes keep the preview calm and the final transcript fast:

- While the user speaks, a *draft* pass decodes once, so its duration is
  bounded, and its last word is held back because audio cut mid-word makes
  Whisper guess that word.
- At a speech pause, a *settled* pass runs exactly like the final pass. If the
  recording then ends without new sound, its text is the final transcript and
  the caller skips its own pass (``LivePreview.final_text_for``).
"""

from __future__ import annotations

import logging
import threading
import time
from bisect import bisect_right
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("pulsescribe.audio")

# Seconds. Measured with turbo (MLX, 8 bit) on an M6: a pass over up to 20 s of
# audio takes 0.3-0.4 s.
SPEECH_BEFORE_FIRST_DRAFT_S = 1.0  # earlier passes mostly hallucinate
MIN_SPEECH_S = 0.5  # sound above the speech threshold before any pass
MIN_DRAFT_INTERVAL_S = 0.7  # start to start
DRAFT_INTERVAL_FACTOR = 2.0  # ... and at least this multiple of the last pass
PAUSE_MARGIN_S = 0.1  # quiet beyond the trim padding before a settled pass
WINDOW_MAX_S = 20.0  # longer recordings preview only their newest part
WINDOW_KEEP_S = 10.0
WINDOW_SEARCH_S = 4.0  # range before WINDOW_KEEP_S searched for the quietest cut
SLOW_DRAFT_S = 1.0  # a slower draft would delay the final pass too much
POLL_S = 0.05


@dataclass(frozen=True)
class PreviewPass:
    """One planned pass over ``chunks[start_chunk:end_chunk]``."""

    start_chunk: int
    end_chunk: int
    draft: bool
    max_level: float


class PreviewPlanner:
    """Decides when the next pass runs and over which chunks.

    Feed every recorded chunk once through ``observe``, ask ``next_pass`` and
    report each pass you ran through ``completed``. Holds no audio and starts no
    thread; not thread-safe.

    ``speech_threshold`` is the chunk level that counts as speech; drafts follow
    speech. ``quiet_threshold(max_level)`` and ``quiet_pad_s`` describe how the
    final pass trims trailing silence: a settled pass waits until more than
    ``quiet_pad_s`` of quieter audio trails the recording, so it sees the same
    end the final pass will see, and runs again after any louder sound.
    """

    def __init__(
        self,
        *,
        sample_rate: int,
        speech_threshold: float,
        quiet_threshold: Callable[[float], float],
        quiet_pad_s: float,
    ) -> None:
        self._sample_rate = sample_rate
        self._speech_threshold = speech_threshold
        self._quiet_threshold = quiet_threshold
        self._pause_samples = int((quiet_pad_s + PAUSE_MARGIN_S) * sample_rate)
        self._ends: list[int] = []  # sample offset after each chunk
        self._levels: list[float] = []
        self._max_level = 0.0
        self._speech_start: int | None = None
        self._speech_end = 0
        self._speech_samples = 0
        self._sound_end = 0  # after the last chunk the final pass would not trim
        self._covered_end = 0  # recording length the last pass saw
        self._covered_by_draft = False
        self._anchor = 0
        self._next_draft_at = 0.0

    def observe(self, chunk_sizes: Iterable[int], levels: Iterable[float]) -> None:
        """Add newly recorded chunks: their sample counts and RMS levels."""
        total = self._ends[-1] if self._ends else 0
        for size, level in zip(chunk_sizes, levels):
            start, total = total, total + size
            self._ends.append(total)
            self._levels.append(level)
            self._max_level = max(self._max_level, level)
            if level >= self._quiet_threshold(self._max_level):
                self._sound_end = total
            if level > self._speech_threshold:
                if self._speech_start is None:
                    self._speech_start = start
                self._speech_end = total
                self._speech_samples += size

    def next_pass(self, now: float) -> PreviewPass | None:
        """Return the pass to run at ``now`` (monotonic seconds), if one is due."""
        if self._speech_start is None:
            return None
        if self._speech_samples < MIN_SPEECH_S * self._sample_rate:
            return None
        if self._ends[-1] - self._sound_end >= self._pause_samples:
            # A draft held its last word back; the pause now shows it.
            if self._sound_end > self._covered_end or self._covered_by_draft:
                return self._plan(draft=False)
            return None
        if self._speech_end <= self._covered_end or now < self._next_draft_at:
            return None
        since_speech_start = self._ends[-1] - self._speech_start
        if since_speech_start < SPEECH_BEFORE_FIRST_DRAFT_S * self._sample_rate:
            return None
        return self._plan(draft=True)

    def completed(self, preview_pass: PreviewPass, started_at: float, duration: float) -> None:
        """Record a finished pass; draft passes space out the following drafts."""
        if preview_pass.draft:
            self._next_draft_at = started_at + max(
                MIN_DRAFT_INTERVAL_S, DRAFT_INTERVAL_FACTOR * duration
            )

    def _plan(self, *, draft: bool) -> PreviewPass:
        self._covered_end = self._ends[-1]
        self._covered_by_draft = draft
        self._move_anchor()
        return PreviewPass(self._anchor, len(self._ends), draft, self._max_level)

    def _move_anchor(self) -> None:
        """Cut old audio off at the quietest chunk once the window grows too long."""
        total = self._ends[-1]
        rate = self._sample_rate
        if total - self._chunk_start(self._anchor) <= WINDOW_MAX_S * rate:
            return
        first = self._chunk_at(total - int((WINDOW_KEEP_S + WINDOW_SEARCH_S) * rate))
        last = max(self._chunk_at(total - int(WINDOW_KEEP_S * rate)), first + 1)
        levels = self._levels[first:last]
        self._anchor = first + levels.index(min(levels))

    def _chunk_start(self, index: int) -> int:
        return self._ends[index - 1] if index > 0 else 0

    def _chunk_at(self, sample: int) -> int:
        """Index of the chunk containing ``sample``."""
        return min(bisect_right(self._ends, max(sample, 0)), len(self._ends) - 1)


def draft_display_text(text: str, shown: str = "") -> str:
    """Return what to show for a draft transcript, given the text ``shown`` so far.

    The last word of a draft is often a guess and is dropped. If that leaves
    only the beginning of ``shown``, ``shown`` stays, so text never shrinks
    right after a speech pause showed the full sentence.
    """
    words = text.split()[:-1]
    shown_words = shown.split()
    if len(words) <= len(shown_words) and shown_words[: len(words)] == words:
        return shown
    return " ".join(words)


class LivePreview:
    """Runs preview passes on a thread while a local recording is running.

    ``transcribe(audio, draft)`` returns the text for prepared audio; with
    ``draft=False`` it must be the same call the final pass uses.
    ``prepare(audio, max_level)`` shapes raw audio exactly like the final pass.
    ``publish(text)`` receives each new preview text on the preview thread.
    ``ready()`` tells whether the model is loaded; passes wait until it is.
    The remaining arguments are those of ``PreviewPlanner``.

    Call ``start`` once the audio callback fills ``chunks`` and ``levels`` (one
    RMS level per chunk, appended after its chunk), ``finish`` after the
    recording stopped, then ``final_text_for`` with the prepared final audio.
    """

    def __init__(
        self,
        *,
        transcribe: Callable[[Any, bool], str],
        prepare: Callable[[Any, float], Any],
        publish: Callable[[str], None],
        sample_rate: int,
        speech_threshold: float,
        quiet_threshold: Callable[[float], float],
        quiet_pad_s: float,
        ready: Callable[[], bool] = lambda: True,
    ) -> None:
        self._transcribe = transcribe
        self._prepare = prepare
        self._publish = publish
        self._ready = ready
        self._planner = PreviewPlanner(
            sample_rate=sample_rate,
            speech_threshold=speech_threshold,
            quiet_threshold=quiet_threshold,
            quiet_pad_s=quiet_pad_s,
        )
        self._finished = threading.Event()
        self._thread: threading.Thread | None = None
        self._observed = 0
        self._shown = ""
        self._settled: tuple[Any, str] | None = None

    def start(
        self,
        chunks: Sequence[Any],
        levels: Sequence[float],
        stop_event: threading.Event,
    ) -> None:
        """Start previewing; no new pass starts once ``stop_event`` is set."""
        self._thread = threading.Thread(
            target=self._run,
            args=(chunks, levels, stop_event),
            daemon=True,
            name="LocalLivePreview",
        )
        self._thread.start()

    def finish(self) -> None:
        """Stop previewing and wait for a running pass (the model is busy until then)."""
        self._finished.set()
        if self._thread is not None:
            self._thread.join()
            self._thread = None

    def final_text_for(self, audio: Any) -> str | None:
        """Return the transcript if a settled pass already transcribed exactly ``audio``."""
        import numpy as np

        if self._settled is None:
            return None
        settled_audio, transcript = self._settled
        if getattr(audio, "shape", None) != settled_audio.shape:
            return None
        return transcript if np.array_equal(audio, settled_audio) else None

    def _run(
        self,
        chunks: Sequence[Any],
        levels: Sequence[float],
        stop_event: threading.Event,
    ) -> None:
        try:
            while not self._finished.is_set() and not stop_event.wait(POLL_S):
                # levels trails chunks, so every observed level has its chunk.
                count = len(levels)
                if count > self._observed:
                    self._planner.observe(
                        (len(chunks[i]) for i in range(self._observed, count)),
                        levels[self._observed : count],
                    )
                    self._observed = count
                if not self._ready():
                    continue
                preview_pass = self._planner.next_pass(time.perf_counter())
                if preview_pass is not None and not self._run_pass(chunks, preview_pass):
                    return
        except Exception:
            # The preview is optional; the final pass still transcribes everything.
            logger.exception("Live-Vorschau abgebrochen")

    def _run_pass(self, chunks: Sequence[Any], preview_pass: PreviewPass) -> bool:
        """Run one pass; ``False`` ends the preview for this recording."""
        import numpy as np

        raw = np.concatenate(chunks[preview_pass.start_chunk : preview_pass.end_chunk])
        audio = self._prepare(raw, preview_pass.max_level)
        started_at = time.perf_counter()
        transcript = self._transcribe(audio, preview_pass.draft)
        duration = time.perf_counter() - started_at
        self._planner.completed(preview_pass, started_at, duration)

        text = transcript.strip()
        if preview_pass.draft:
            text = draft_display_text(text, self._shown)
        elif preview_pass.start_chunk == 0:
            self._settled = (audio, transcript)
        if text and text != self._shown:
            self._shown = text
            self._publish(text)

        if preview_pass.draft and duration > SLOW_DRAFT_S:
            logger.info(
                "Live-Vorschau pausiert bis zur nächsten Aufnahme: "
                f"Durchlauf dauerte {duration:.2f}s"
            )
            return False
        return True


__all__ = ["LivePreview", "PreviewPlanner", "PreviewPass", "draft_display_text"]
