"""Live text preview for local recordings.

Local Whisper has no streaming mode: it transcribes a finished recording. To
show text while the user still speaks, ``LivePreview`` transcribes the recording
so far again and again and publishes the result. The final transcript stays a
normal pass over the whole recording.

Two kinds of passes keep the preview calm and the final transcript fast:

- While the user speaks, a *draft* pass decodes once, so its duration is
  bounded, and its last word is held back because audio cut mid-word makes
  Whisper guess that word. Drafts start only while speech is going on, so a
  stop shortly after the last word rarely has to wait for one. Drafts that
  take too long end the preview for the recording: a stop would wait for them.
- At a speech pause, a *settled* pass runs exactly like the final pass and
  shows every word. If the recording then ends without new sound, its text is
  the final transcript and the caller skips its own pass
  (``LivePreview.final_text_for``).
"""

from __future__ import annotations

import logging
import statistics
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
MIN_SPEECH_S = 0.5  # speech before any pass
# A chunk at this share of the loudest chunk is speech. Measured on 80 German
# recordings: 64 % of chunks lie above it, background noise mostly below 5 %.
SPEECH_LEVEL_RATIO = 0.1
MIN_DRAFT_INTERVAL_S = 0.7  # start to start
DRAFT_INTERVAL_FACTOR = 2.0  # ... and at least this multiple of the last pass
DRAFT_MAX_GAP_S = 0.1  # no draft once speech has been over for longer
PAUSE_MARGIN_S = 0.1  # quiet beyond the trim padding before a settled pass
NOISY_PAUSE_S = 0.7  # speech over for this long: settled pass even if never quiet
WINDOW_MAX_S = 20.0  # longer recordings preview only their newest part
WINDOW_KEEP_S = 10.0
WINDOW_SEARCH_S = 4.0  # range before WINDOW_KEEP_S searched for the quietest cut
SLOW_DRAFT_S = 1.0  # a slower draft would delay the final pass too much
SLOW_DRAFTS_BEFORE_END = 2  # in a row; one slow draft can be an outlier
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

    ``speech_threshold`` is the chunk level a recording must exceed once to be
    transcribed at all; no pass runs before. Which chunks then count as speech
    follows the loudest chunk (``SPEECH_LEVEL_RATIO``), capped at
    ``speech_threshold``, so a quiet microphone previews like a loud one; drafts
    follow speech. ``quiet_threshold(max_level)`` and ``quiet_pad_s`` describe
    how the final pass trims trailing silence: a settled pass waits until more
    than ``quiet_pad_s`` of quieter audio trails the recording, so it sees the
    same end the final pass will see, and runs again after any louder sound.
    With ``drafts=False`` only those settled passes are planned.
    """

    def __init__(
        self,
        *,
        sample_rate: int,
        speech_threshold: float,
        quiet_threshold: Callable[[float], float],
        quiet_pad_s: float,
        drafts: bool = True,
    ) -> None:
        self._drafts = drafts
        self._sample_rate = sample_rate
        self._speech_threshold = speech_threshold
        self._quiet_threshold = quiet_threshold
        self._pause_samples = int((quiet_pad_s + PAUSE_MARGIN_S) * sample_rate)
        self._ends: list[int] = []  # sample offset after each chunk
        self._levels: list[float] = []
        self._max_level = 0.0
        self._levels_for = (0.0, 0.0)  # (speech, quiet) levels the fields below use
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
        first_new = len(self._ends)
        total = self._ends[-1] if self._ends else 0
        for size, level in zip(chunk_sizes, levels):
            total += size
            self._ends.append(total)
            self._levels.append(level)
            self._max_level = max(self._max_level, level)

        levels_now = (
            min(self._speech_threshold, SPEECH_LEVEL_RATIO * self._max_level),
            self._quiet_threshold(self._max_level),
        )
        if levels_now != self._levels_for:
            # A louder chunk moved the levels: judge the whole recording again.
            self._levels_for = levels_now
            self._speech_start = None
            self._speech_end = self._speech_samples = self._sound_end = 0
            first_new = 0
        speech_level, quiet_level = levels_now
        for index in range(first_new, len(self._ends)):
            level, end = self._levels[index], self._ends[index]
            if level >= quiet_level:
                self._sound_end = end
            if level > speech_level:
                start = self._chunk_start(index)
                if self._speech_start is None:
                    self._speech_start = start
                self._speech_end = end
                self._speech_samples += end - start

    @property
    def speech_seconds(self) -> float:
        """Seconds of the recording so far that count as speech."""
        return self._speech_samples / self._sample_rate

    @property
    def max_level(self) -> float:
        """Level of the loudest chunk so far."""
        return self._max_level

    def next_pass(self, now: float) -> PreviewPass | None:
        """Return the pass to run at ``now`` (monotonic seconds), if one is due."""
        if self._speech_start is None or self._max_level <= self._speech_threshold:
            return None
        if self._speech_samples < MIN_SPEECH_S * self._sample_rate:
            return None
        if self._ends[-1] - self._sound_end >= self._pause_samples:
            # A draft held its last word back; the pause now shows it.
            if self._sound_end > self._covered_end or self._covered_by_draft:
                return self._plan(draft=False)
            return None
        if not self._drafts:
            return None  # the passes below only serve the shown text
        new_speech = self._speech_end > self._covered_end
        if self._ends[-1] - self._speech_end >= NOISY_PAUSE_S * self._sample_rate:
            # Background noise above the trim level hides the pause from the
            # check above; the last word must not stay held back until the stop.
            if new_speech or self._covered_by_draft:
                return self._plan(draft=False)
            return None
        if not new_speech or now < self._next_draft_at:
            return None
        if self._ends[-1] - self._speech_end > DRAFT_MAX_GAP_S * self._sample_rate:
            # The speaker may be done: a draft now would delay the settled pass
            # or, after a stop, the final pass.
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
        if self._drafts:
            # Settled passes alone stay on the whole recording: only then can
            # their text be the transcript.
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


@dataclass(frozen=True)
class PreviewSummary:
    """What a preview did during one recording, for the log."""

    drafts: int
    fast_drafts: int  # drafts within SLOW_DRAFT_S
    settled: int
    draft_median_s: float
    slowest_s: float  # longest pass
    too_slow: bool  # ended early: drafts in a row took longer than SLOW_DRAFT_S
    speech_s: float  # part of the recording that counted as speech
    max_level: float  # loudest chunk
    model_ready: bool  # False: ``ready()`` never was true
    failed: bool  # a pass raised; the error is in the log


class LivePreview:
    """Runs preview passes on a thread while a local recording is running.

    ``transcribe(audio, draft)`` returns the text for prepared audio; with
    ``draft=False`` it must be the same call the final pass uses.
    ``prepare(audio, max_level)`` shapes raw audio exactly like the final pass.
    ``publish(text)`` receives each new preview text on the preview thread; an
    empty text means the preview ended early and its text must disappear.
    ``ready()`` tells whether the model is loaded; passes wait until it is.
    The remaining arguments are those of ``PreviewPlanner``.

    With ``drafts=False`` it shows nothing and only runs the settled passes, so
    that a recording that ends after a pause has its transcript already
    (another preview may show the text meanwhile). A settled pass slower than
    ``SLOW_DRAFT_S`` then ends it for the recording: a stop would wait for it.

    Call ``start`` once the audio callback fills ``chunks`` and ``levels`` (one
    RMS level per chunk, appended after its chunk), ``finish`` after the
    recording stopped, then ``final_text_for`` with the prepared final audio.
    ``finish`` returns a ``PreviewSummary``.
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
        drafts: bool = True,
    ) -> None:
        self._transcribe = transcribe
        self._prepare = prepare
        self._publish = publish
        self._ready = ready
        self._drafts = drafts
        self._planner = PreviewPlanner(
            sample_rate=sample_rate,
            speech_threshold=speech_threshold,
            quiet_threshold=quiet_threshold,
            quiet_pad_s=quiet_pad_s,
            drafts=drafts,
        )
        self._finished = threading.Event()
        self._thread: threading.Thread | None = None
        self._observed = 0
        self._shown = ""
        self._settled: tuple[Any, str] | None = None
        self._draft_seconds: list[float] = []
        self._settled_seconds: list[float] = []
        self._slow_drafts = 0  # in a row
        self._fast_drafts = 0
        self._too_slow = False
        self._model_ready = False
        self._failed = False

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

    def finish(self) -> PreviewSummary:
        """Stop previewing and wait for a running pass (the model is busy until then)."""
        self._finished.set()
        if self._thread is not None:
            self._thread.join()
            self._thread = None
        drafts, settled = self._draft_seconds, self._settled_seconds
        return PreviewSummary(
            drafts=len(drafts),
            fast_drafts=self._fast_drafts,
            settled=len(settled),
            draft_median_s=statistics.median(drafts) if drafts else 0.0,
            slowest_s=max(drafts + settled, default=0.0),
            too_slow=self._too_slow,
            speech_s=self._planner.speech_seconds,
            max_level=self._planner.max_level,
            model_ready=self._model_ready,
            failed=self._failed,
        )

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
                self._observe_new(chunks, levels)
                if not self._ready():
                    continue
                self._model_ready = True
                preview_pass = self._planner.next_pass(time.perf_counter())
                if preview_pass is None or stop_event.is_set():
                    continue
                if not self._run_pass(chunks, preview_pass):
                    return
            self._observe_new(chunks, levels)  # the summary covers the whole recording
        except Exception:
            # The preview is optional; the final pass still transcribes everything.
            self._failed = True
            logger.exception("Live-Vorschau abgebrochen")

    def _observe_new(self, chunks: Sequence[Any], levels: Sequence[float]) -> None:
        """Hand the chunks recorded since the last call to the planner."""
        # levels trails chunks, so every observed level has its chunk.
        count = len(levels)
        if count > self._observed:
            self._planner.observe(
                (len(chunks[i]) for i in range(self._observed, count)),
                levels[self._observed : count],
            )
            self._observed = count

    def _run_pass(self, chunks: Sequence[Any], preview_pass: PreviewPass) -> bool:
        """Run one pass; ``False`` ends the preview for this recording."""
        import numpy as np

        raw = np.concatenate(chunks[preview_pass.start_chunk : preview_pass.end_chunk])
        audio = self._prepare(raw, preview_pass.max_level)
        started_at = time.perf_counter()
        transcript = self._transcribe(audio, preview_pass.draft)
        duration = time.perf_counter() - started_at
        self._planner.completed(preview_pass, started_at, duration)
        (self._draft_seconds if preview_pass.draft else self._settled_seconds).append(duration)

        if preview_pass.draft and duration <= SLOW_DRAFT_S:
            self._slow_drafts = 0
            self._fast_drafts += 1
        elif preview_pass.draft:
            self._slow_drafts += 1
            if self._slow_drafts >= SLOW_DRAFTS_BEFORE_END:
                # Text that stops following the speaker misleads; show none.
                self._too_slow = True
                if self._shown:
                    self._shown = ""
                    self._publish("")
                return False

        text = transcript.strip()
        if preview_pass.draft:
            text = draft_display_text(text, self._shown)
        elif preview_pass.start_chunk == 0:
            self._settled = (audio, transcript)
        if not self._drafts:
            # Nothing is shown; a slow pass only risks holding up the final one.
            self._too_slow = duration > SLOW_DRAFT_S
            return not self._too_slow
        if text and text != self._shown:
            self._shown = text
            self._publish(text)
        return True


__all__ = [
    "LivePreview",
    "PreviewPass",
    "PreviewPlanner",
    "PreviewSummary",
    "draft_display_text",
]
