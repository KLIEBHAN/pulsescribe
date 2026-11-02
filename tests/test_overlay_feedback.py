import pytest

from ui.overlay_feedback import (
    OverlayContent,
    describe_overlay_content,
    transcribing_status,
)
from ui.overlay_text import split_recording_interim_text
from utils.transcript_delivery import (
    COPIED,
    COPY_FAILED,
    PASTE_FAILED,
    PASTED,
    TEST_COMPLETE,
    done_status,
)


@pytest.mark.parametrize(
    ("state", "bars", "text"),
    [
        ("LISTENING", "low", "Listening…"),
        ("LOADING", "line", "Preparing…"),
        ("TRANSCRIBING", "line", "Transcribing…"),
    ],
)
def test_progress_states_use_static_bars_and_muted_copy(state, bars, text) -> None:
    assert describe_overlay_content(state) == OverlayContent(bars, text)


def test_recording_is_the_only_level_driven_state() -> None:
    assert describe_overlay_content("RECORDING") == OverlayContent(
        "level", "Recording", emphasized=True
    )


def test_loading_explains_warmup() -> None:
    assert describe_overlay_content("LOADING", "Warming up...").text == (
        "Warming up local model…"
    )


@pytest.mark.parametrize("detail", [None, "Finishing..."])
def test_transcribing_shows_two_steps_only_when_refine_follows(detail) -> None:
    refine = transcribing_status(detail, refine=True)

    assert transcribing_status(detail, refine=False) == detail
    assert describe_overlay_content("TRANSCRIBING", detail) == OverlayContent(
        "line", "Transcribing…"
    )
    assert describe_overlay_content("TRANSCRIBING", refine) == OverlayContent(
        "phase1", "Transcribing…", meta="1 of 2"
    )
    assert describe_overlay_content("REFINING") == OverlayContent(
        "phase2", "Refining…", meta="2 of 2"
    )


def test_streaming_refine_payloads_stay_distinct_for_the_windows_watchdog() -> None:
    assert transcribing_status("Finishing...", refine=True) != transcribing_status(
        refine=True
    )


def test_done_shows_badge_target_app_and_time_only_when_reported() -> None:
    assert describe_overlay_content("DONE", "some transcript") == OverlayContent(
        badge="Pasted", tone="success"
    )
    assert describe_overlay_content(
        "DONE", done_status(PASTED, app="Visual Studio Code", seconds=1.26)
    ) == OverlayContent(
        text="into Visual Studio Code", badge="Pasted", tone="success", meta="1.3 s"
    )
    assert describe_overlay_content("DONE", done_status(PASTED)) == OverlayContent(
        badge="Pasted", tone="success"
    )
    # A copy has no target app; the time still shows when reported.
    assert describe_overlay_content(
        "DONE", done_status(COPIED, app="Mail", seconds=0.4)
    ) == OverlayContent(badge="Copied", tone="success", meta="0.4 s")


def test_test_runs_do_not_claim_a_paste() -> None:
    assert describe_overlay_content("DONE", TEST_COMPLETE) == OverlayContent(
        text="Test complete", badge="Done", tone="success"
    )


def test_only_errors_are_dismissible() -> None:
    for state in ("LISTENING", "RECORDING", "TRANSCRIBING", "DONE", "NO_SPEECH"):
        assert describe_overlay_content(state).dismissible is False
    assert describe_overlay_content("ERROR", "boom").dismissible is True


def test_no_speech_uses_warn_badge() -> None:
    assert describe_overlay_content("NO_SPEECH", "empty transcript") == OverlayContent(
        text="Nothing was pasted", badge="No speech", tone="warn"
    )


@pytest.mark.parametrize("message", [PASTE_FAILED, COPY_FAILED])
def test_delivery_failures_use_not_pasted_badge(message) -> None:
    assert describe_overlay_content("ERROR", message) == OverlayContent(
        text=message, badge="Not pasted", tone="warn", dismissible=True
    )


@pytest.mark.parametrize(
    ("detail", "text"),
    [
        ("OPENAI_API_KEY not set", "Missing API key"),
        (" microphone\nmissing ", "Microphone unavailable"),
        ("Transcription timed out", "Transcription timed out"),
        ("boom", "boom"),
        ("Error: boom", "boom"),
        (None, "Something went wrong"),
    ],
)
def test_errors_show_friendly_detail_next_to_error_badge(detail, text) -> None:
    assert describe_overlay_content("ERROR", detail) == OverlayContent(
        text=text, badge="Error", tone="error", dismissible=True
    )


@pytest.mark.parametrize(
    ("previous", "current", "expected"),
    [
        ("", "hello world", ("", "hello world")),
        ("hello", "hello world", ("hello ", "world")),
        ("hello wor", "hello world", ("hello ", "world")),
        ("hello world", "hello world", ("hello world", "")),
        ("hello there", "hello world", ("hello ", "world")),
    ],
)
def test_split_recording_interim_text_marks_newest_words(previous, current, expected):
    assert split_recording_interim_text(current, previous) == expected


def test_split_recording_interim_text_keeps_tail_truncation() -> None:
    earlier, newest = split_recording_interim_text(
        "alpha beta gamma delta epsilon", "alpha beta gamma delta", max_chars=16
    )
    assert earlier + newest == "...delta epsilon"
    assert newest == "epsilon"
