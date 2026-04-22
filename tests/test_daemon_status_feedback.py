from utils.state import AppState, DaemonErrorCode, DaemonStatusError

from ui.daemon_status_feedback import (
    build_daemon_status_hint,
    build_daemon_status_label,
    build_daemon_tray_title,
    infer_daemon_status_error,
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
    assert (
        build_daemon_status_label(AppState.LOADING, "Starting up...")
        == "Starting up PulseScribe"
    )


def test_build_daemon_status_hint_explains_startup_loading_and_permission_recovery() -> None:
    startup_hint = build_daemon_status_hint(AppState.LOADING, "Starting up...")
    permission_hint = build_daemon_status_hint(
        AppState.ERROR,
        "Input monitoring permission missing",
    )

    assert "hotkeys" in startup_hint.lower()
    assert "audio" in startup_hint.lower()
    assert "grant the missing permission" in permission_hint.lower()


def test_build_daemon_status_label_classifies_common_error_types() -> None:
    assert build_daemon_status_label(AppState.ERROR, "OPENAI_API_KEY not set") == "Missing API key"
    assert build_daemon_status_label(AppState.ERROR, "Already recording") == "PulseScribe is busy"
    assert (
        build_daemon_status_label(AppState.ERROR, "Watchdog timeout while transcribing")
        == "Transcription timed out"
    )
    assert (
        build_daemon_status_label(AppState.ERROR, "No module named 'faster_whisper'")
        == "Missing dependency"
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


def test_build_daemon_status_helpers_classify_invalid_provider_configuration() -> None:
    assert (
        build_daemon_status_label(AppState.ERROR, "Unbekannter Provider: foo")
        == "Invalid provider setting"
    )
    assert (
        build_daemon_status_hint(
            AppState.ERROR,
            "Unbekannter Refine-Provider 'bar'. Unterstützt: groq, openai",
        )
        == "Open Setup & Settings, choose a supported provider, then try again."
    )



def test_build_daemon_status_helpers_accept_structured_error_codes() -> None:
    error = DaemonStatusError(DaemonErrorCode.INVALID_PROVIDER, "Unbekannter Provider: foo")

    assert build_daemon_status_label(AppState.ERROR, error) == "Invalid provider setting"
    assert (
        build_daemon_status_hint(AppState.ERROR, error)
        == "Open Setup & Settings, choose a supported provider, then try again."
    )
    assert infer_daemon_status_error(error) == error



def test_build_daemon_status_helpers_keep_unclassified_module_attribute_errors_verbatim() -> None:
    detail = "module 'x' has no attribute 'y'"

    assert build_daemon_status_label(AppState.ERROR, detail) == detail
    assert build_daemon_status_hint(AppState.ERROR, detail) == (
        "Try again. PulseScribe will return to ready automatically. "
        "Export diagnostics or open Setup if it keeps happening."
    )


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
