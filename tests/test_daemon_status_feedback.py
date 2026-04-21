from utils.state import AppState

from ui.daemon_status_feedback import (
    build_daemon_status_hint,
    build_daemon_status_label,
    build_daemon_tray_title,
    normalize_daemon_status_text,
)


def test_normalize_daemon_status_text_collapses_whitespace() -> None:
    assert normalize_daemon_status_text("alpha\n  beta\t gamma") == "alpha beta gamma"


def test_build_daemon_status_label_prefers_loading_detail_when_helpful() -> None:
    assert (
        build_daemon_status_label(AppState.LOADING, "Loading large-v3...")
        == "Loading large-v3"
    )
    assert (
        build_daemon_status_label(AppState.LOADING, "Warming up...")
        == "Warming up local model"
    )


def test_build_daemon_status_label_classifies_common_error_types() -> None:
    assert build_daemon_status_label(AppState.ERROR, "OPENAI_API_KEY not set") == "Missing API key"
    assert build_daemon_status_label(AppState.ERROR, "Already recording") == "PulseScribe is busy"
    assert (
        build_daemon_status_label(AppState.ERROR, "Watchdog timeout while transcribing")
        == "Transcription timed out"
    )


def test_build_daemon_status_hint_returns_targeted_recovery_guidance() -> None:
    assert (
        build_daemon_status_hint(AppState.ERROR, "OPENAI_API_KEY not set")
        == "Add the required key in Setup & Settings, then try again."
    )
    assert "wait a moment" in build_daemon_status_hint(
        AppState.ERROR,
        "PulseScribe is busy",
    ).lower()
    assert "microphone access" in build_daemon_status_hint(
        AppState.ERROR,
        "Microphone unavailable",
    ).lower()


def test_build_daemon_status_helpers_cover_no_speech_retry_guidance() -> None:
    assert build_daemon_status_label(AppState.NO_SPEECH) == "No speech detected"
    hint = build_daemon_status_hint(AppState.NO_SPEECH, "empty transcript")
    assert "try again" in hint.lower()
    assert "microphone" in hint.lower()


def test_build_daemon_tray_title_combines_status_with_recovery_help() -> None:
    title = build_daemon_tray_title(AppState.ERROR, "OPENAI_API_KEY not set")

    assert title.startswith("PulseScribe — Missing API key")
    assert "try again" in title.lower()


def test_build_daemon_tray_title_includes_no_speech_hint() -> None:
    title = build_daemon_tray_title(AppState.NO_SPEECH)

    assert title.startswith("PulseScribe — No speech detected")
    assert "try again" in title.lower()
