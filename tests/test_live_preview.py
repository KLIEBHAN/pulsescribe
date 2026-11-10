"""Live preview of local recordings (audio/live_preview.py)."""

import threading

import numpy as np
import pytest

from audio import live_preview
from audio.live_preview import LivePreview, PreviewPlanner, draft_display_text

RATE = 16000
CHUNK = 320  # 20 ms
SPEECH, SOFT, QUIET = 0.1, 0.004, 0.0005
QUIET_PAD_S = 0.25


def _quiet_threshold(max_level: float) -> float:
    return min(0.005, max_level * 0.03)


def _planner() -> PreviewPlanner:
    return PreviewPlanner(
        sample_rate=RATE,
        speech_threshold=0.01,
        quiet_threshold=_quiet_threshold,
        quiet_pad_s=QUIET_PAD_S,
    )


def _feed(planner: PreviewPlanner, level: float, seconds: float) -> None:
    count = round(seconds * RATE / CHUNK)
    planner.observe([CHUNK] * count, [level] * count)


class TestPreviewPlanner:
    def test_no_pass_before_enough_speech(self):
        planner = _planner()
        _feed(planner, QUIET, 3.0)
        assert planner.next_pass(10.0) is None
        _feed(planner, SPEECH, 0.2)  # a click, then quiet
        _feed(planner, QUIET, 1.0)
        assert planner.next_pass(10.0) is None

    def test_first_draft_waits_one_second_after_speech_starts(self):
        planner = _planner()
        _feed(planner, QUIET, 0.5)
        _feed(planner, SPEECH, 0.9)
        assert planner.next_pass(10.0) is None
        _feed(planner, SPEECH, 0.2)

        preview_pass = planner.next_pass(10.0)

        assert preview_pass is not None and preview_pass.draft
        assert (preview_pass.start_chunk, preview_pass.end_chunk) == (0, 80)
        assert preview_pass.max_level == SPEECH

    def test_drafts_are_spaced_by_interval_and_pass_duration(self):
        planner = _planner()
        _feed(planner, SPEECH, 1.5)
        first = planner.next_pass(10.0)
        assert first is not None
        planner.completed(first, started_at=10.0, duration=0.3)
        _feed(planner, SPEECH, 0.5)
        assert planner.next_pass(10.65) is None  # 0.7 s from start to start
        assert planner.next_pass(10.75) is not None

        _feed(planner, SPEECH, 0.5)
        planner.completed(first, started_at=20.0, duration=0.6)
        assert planner.next_pass(21.15) is None  # twice the last pass
        assert planner.next_pass(21.25) is not None

    def test_no_draft_without_new_speech(self):
        planner = _planner()
        _feed(planner, SPEECH, 1.5)
        assert planner.next_pass(10.0) is not None
        _feed(planner, QUIET, 0.2)  # shorter than a pause
        assert planner.next_pass(99.0) is None

    def test_no_draft_once_speech_is_over(self):
        planner = _planner()
        _feed(planner, SPEECH, 1.5)
        assert planner.next_pass(10.0).draft
        _feed(planner, SPEECH, 0.5)
        _feed(planner, QUIET, 0.08)  # a gap between words
        assert planner.next_pass(99.0) is not None

        _feed(planner, SPEECH, 0.5)
        _feed(planner, QUIET, 0.2)  # the speaker may be done; a stop is likely
        assert planner.next_pass(99.0) is None
        _feed(planner, SPEECH, 0.1)  # ... but went on
        assert planner.next_pass(99.0).draft

    def test_pause_runs_one_settled_pass(self):
        planner = _planner()
        _feed(planner, SPEECH, 1.5)
        assert planner.next_pass(10.0).draft
        _feed(planner, QUIET, QUIET_PAD_S + 0.05)
        assert planner.next_pass(10.1) is None  # trim padding not passed yet
        _feed(planner, QUIET, 0.1)

        settled = planner.next_pass(10.1)  # ignores the draft interval

        assert settled is not None and not settled.draft
        _feed(planner, QUIET, 2.0)
        assert planner.next_pass(99.0) is None

    def test_sound_the_final_pass_keeps_triggers_another_settled_pass(self):
        planner = _planner()
        _feed(planner, SPEECH, 1.5)
        _feed(planner, QUIET, 0.5)
        assert not planner.next_pass(10.0).draft
        _feed(planner, SOFT, 0.1)  # below speech level, above the trim threshold
        _feed(planner, QUIET, 0.2)
        assert planner.next_pass(99.0) is None
        _feed(planner, QUIET, 0.2)

        again = planner.next_pass(99.0)

        assert again is not None and not again.draft

    def test_long_recording_previews_only_its_newest_part(self):
        planner = _planner()
        _feed(planner, SPEECH, 9.0)
        _feed(planner, QUIET, 0.1)  # quietest spot inside the search range
        _feed(planner, SPEECH, 11.5)

        preview_pass = planner.next_pass(10.0)

        assert preview_pass is not None
        assert preview_pass.start_chunk == round(9.0 * RATE / CHUNK)
        assert preview_pass.end_chunk == round(20.6 * RATE / CHUNK)

    def test_recording_up_to_window_limit_is_previewed_whole(self):
        planner = _planner()
        _feed(planner, SPEECH, 19.9)
        assert planner.next_pass(10.0).start_chunk == 0


class TestDraftDisplayText:
    def test_drops_last_word(self):
        assert draft_display_text("Heute ist ein schöner") == "Heute ist ein"

    def test_single_word_shows_nothing_new(self):
        assert draft_display_text("Heute") == ""
        assert draft_display_text("Heute", "Guten Tag.") == "Guten Tag."

    def test_keeps_shown_text_instead_of_shrinking(self):
        shown = "Heute ist ein schöner Tag."
        assert draft_display_text("Heute ist ein schöner", shown) == shown
        assert draft_display_text("Heute ist ein schöner Tag.", shown) == shown

    def test_grows_and_revises(self):
        shown = "Heute ist ein schöner Tag."
        assert (
            draft_display_text("Heute ist ein schöner Tag, und morgen wird", shown)
            == "Heute ist ein schöner Tag, und morgen"
        )
        assert draft_display_text("Heute war ein toller", shown) == "Heute war ein"
        # The sentence goes on: the full stop a pause showed is revised away.
        assert draft_display_text("Heute ist ein schöner Tag und", shown) == (
            "Heute ist ein schöner Tag"
        )


def _recording(*parts: tuple[float, float]) -> tuple[list[np.ndarray], list[float]]:
    """Chunks and levels for ``(level, seconds)`` parts; every chunk is unique."""
    rng = np.random.default_rng(0)
    chunks, levels = [], []
    for level, seconds in parts:
        for _ in range(round(seconds * RATE / CHUNK)):
            chunks.append((level * rng.standard_normal((CHUNK, 1))).astype(np.float32))
            levels.append(level)
    return chunks, levels


class _Harness:
    """A LivePreview with recorded calls; ``wait_for_passes`` blocks until n passes ran."""

    def __init__(self, texts, *, ready=lambda: True):
        self.texts = iter(texts)
        self.calls: list[tuple[np.ndarray, bool]] = []
        self.published: list[str] = []
        self.stop_event = threading.Event()
        self._passes = threading.Semaphore(0)
        self.preview = LivePreview(
            transcribe=self._transcribe,
            prepare=lambda audio, max_level: audio.reshape(-1),
            publish=self.published.append,
            sample_rate=RATE,
            speech_threshold=0.01,
            quiet_threshold=_quiet_threshold,
            quiet_pad_s=QUIET_PAD_S,
            ready=ready,
        )

    def _transcribe(self, audio, draft):
        self.calls.append((audio, draft))
        try:
            text = next(self.texts)
            if isinstance(text, Exception):
                raise text
            return text
        finally:
            self._passes.release()

    def wait_for_passes(self, count: int) -> None:
        for _ in range(count):
            assert self._passes.acquire(timeout=5), "preview pass did not run"


@pytest.fixture(autouse=True)
def _fast_poll(monkeypatch):
    monkeypatch.setattr(live_preview, "POLL_S", 0.005)


class TestLivePreview:
    def test_draft_shows_text_without_last_word(self):
        harness = _Harness([" Heute ist ein schöner"])
        chunks, levels = _recording((SPEECH, 1.5))

        harness.preview.start(chunks, levels, harness.stop_event)
        harness.wait_for_passes(1)
        harness.preview.finish()

        assert harness.published == ["Heute ist ein"]
        audio, draft = harness.calls[0]
        assert draft and audio.shape == (1.5 * RATE,)
        assert harness.preview.final_text_for(audio) is None  # drafts are never final

    def test_settled_pass_is_reused_for_identical_final_audio(self):
        harness = _Harness([" Heute ist ein schöner Tag."])
        chunks, levels = _recording((SPEECH, 1.5), (QUIET, 0.5))

        harness.preview.start(chunks, levels, harness.stop_event)
        harness.wait_for_passes(1)
        harness.preview.finish()

        assert harness.published == ["Heute ist ein schöner Tag."]
        audio, draft = harness.calls[0]
        assert not draft
        assert harness.preview.final_text_for(audio.copy()) == " Heute ist ein schöner Tag."
        changed = audio.copy()
        changed[-1] += 0.5
        assert harness.preview.final_text_for(changed) is None
        assert harness.preview.final_text_for(audio[:-1]) is None

    def test_draft_then_pause_publishes_both(self):
        harness = _Harness([" Heute ist ein", " Heute ist ein Tag."])
        chunks, levels = _recording((SPEECH, 1.5))

        harness.preview.start(chunks, levels, harness.stop_event)
        harness.wait_for_passes(1)
        more_chunks, more_levels = _recording((QUIET, 0.5))
        for chunk, level in zip(more_chunks, more_levels):
            chunks.append(chunk)
            levels.append(level)
        harness.wait_for_passes(1)
        harness.preview.finish()

        assert harness.published == ["Heute ist", "Heute ist ein Tag."]
        assert [draft for _audio, draft in harness.calls] == [True, False]

    def test_waits_until_model_is_ready(self):
        ready = threading.Event()
        harness = _Harness([" Heute ist ein Tag."], ready=ready.is_set)
        chunks, levels = _recording((SPEECH, 1.5), (QUIET, 0.5))

        harness.preview.start(chunks, levels, harness.stop_event)
        threading.Event().wait(0.05)
        assert harness.calls == []
        ready.set()
        harness.wait_for_passes(1)
        harness.preview.finish()

        assert harness.published == ["Heute ist ein Tag."]

    def test_no_pass_after_stop(self):
        harness = _Harness([" nie"])
        chunks, levels = _recording((SPEECH, 1.5))
        harness.stop_event.set()

        harness.preview.start(chunks, levels, harness.stop_event)
        harness.preview.finish()

        assert harness.calls == []
        assert harness.preview.final_text_for(np.zeros(3, dtype=np.float32)) is None

    def test_slow_draft_ends_preview_for_this_recording(self, monkeypatch):
        monkeypatch.setattr(live_preview, "SLOW_DRAFT_S", 0.0)
        harness = _Harness([" Heute ist ein", " Heute ist ein Tag."])
        chunks, levels = _recording((SPEECH, 1.5))

        harness.preview.start(chunks, levels, harness.stop_event)
        harness.wait_for_passes(1)
        thread = harness.preview._thread
        thread.join(timeout=5)  # the preview ends by itself, before any pause
        ended = not thread.is_alive()
        harness.preview.finish()

        assert ended
        assert len(harness.calls) == 1

    def test_failing_pass_is_logged_and_ends_the_preview(self, caplog):
        harness = _Harness([RuntimeError("model gone")])
        chunks, levels = _recording((SPEECH, 1.5), (QUIET, 0.5))

        with caplog.at_level("ERROR", logger="pulsescribe.audio"):
            harness.preview.start(chunks, levels, harness.stop_event)
            harness.wait_for_passes(1)
            harness.preview.finish()

        # Logged by the preview itself, so the error did not escape its thread.
        assert [record.getMessage() for record in caplog.records] == ["Live-Vorschau abgebrochen"]
        assert harness.published == []
        assert harness.preview.final_text_for(np.zeros(3, dtype=np.float32)) is None
