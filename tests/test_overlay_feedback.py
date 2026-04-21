from ui.overlay_feedback import (
    build_overlay_feedback_text,
    format_overlay_status_text,
)


def test_format_overlay_status_text_uses_clear_recording_fallback() -> None:
    assert format_overlay_status_text("RECORDING") == "Recording — keep speaking"


def test_build_overlay_feedback_text_explains_loading_warmup() -> None:
    assert (
        build_overlay_feedback_text("LOADING", "Warming up...")
        == "Warming up local model…"
    )


def test_build_overlay_feedback_text_classifies_common_errors() -> None:
    assert (
        build_overlay_feedback_text("ERROR", "OPENAI_API_KEY not set")
        == "Missing API key"
    )
    assert (
        build_overlay_feedback_text("ERROR", " microphone\nmissing ")
        == "Microphone unavailable"
    )
    assert build_overlay_feedback_text("ERROR", "boom") == "Error: boom"


def test_build_overlay_feedback_text_makes_rtf_done_copy_human_readable() -> None:
    assert (
        build_overlay_feedback_text("DONE", "✓ (0.3x)")
        == "Transcript pasted • 0.3x realtime"
    )
