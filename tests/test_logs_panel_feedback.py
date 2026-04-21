from ui.logs_panel_feedback import (
    build_logs_empty_state_text,
    build_logs_load_error_text,
    build_logs_manual_refresh_feedback,
    build_logs_open_feedback,
    build_transcripts_clear_feedback,
    build_transcripts_count_text,
    build_transcripts_hint_text,
    build_transcripts_load_error_text,
    build_transcripts_load_feedback,
)



def test_build_logs_empty_state_text_mentions_target_path(tmp_path) -> None:
    log_file = tmp_path / "pulsescribe.log"

    assert build_logs_empty_state_text(log_file) == (
        "No logs yet.\n\nPulseScribe will create a log file here:\n" + str(log_file)
    )



def test_build_logs_load_error_text_includes_details() -> None:
    assert build_logs_load_error_text("disk busy") == (
        "Could not read the log file.\n\nDetails: disk busy"
    )



def test_build_logs_manual_refresh_feedback_varies_by_view() -> None:
    assert build_logs_manual_refresh_feedback(changed=True, view="logs") == (
        "Logs refreshed.",
        "success",
    )
    assert build_logs_manual_refresh_feedback(changed=False, view="logs") == (
        "Logs are already up to date.",
        "text_secondary",
    )
    assert build_logs_manual_refresh_feedback(changed=False, view="transcripts") == (
        "Transcript history is already up to date.",
        "text_secondary",
    )



def test_build_logs_open_feedback_handles_file_and_folder() -> None:
    assert build_logs_open_feedback(found_log=True, destination="Finder") == (
        "Opened log file in Finder.",
        "success",
    )
    assert build_logs_open_feedback(found_log=False, destination="Explorer") == (
        "Opened logs folder in Explorer.",
        "success",
    )



def test_build_transcripts_copy_helpers_cover_count_load_and_clear_states() -> None:
    assert build_transcripts_count_text(0) == "No transcript history yet"
    assert build_transcripts_count_text(1) == "1 recent transcription"
    assert build_transcripts_count_text(3) == "3 recent transcriptions"
    assert build_transcripts_hint_text(0) == (
        "Stored locally on this device. Your next dictation will appear here automatically."
    )
    assert build_transcripts_hint_text(2) == (
        "Stored locally on this device. Clear History permanently removes these entries from this device."
    )
    assert build_transcripts_load_error_text("permission denied") == (
        "Could not load transcript history.\n\nDetails: permission denied"
    )
    assert build_transcripts_load_feedback() == (
        "Could not load transcript history. Try Refresh or Clear History.",
        "error",
    )
    assert build_transcripts_clear_feedback(success=True) == (
        "Transcript history cleared.",
        "success",
    )
    assert build_transcripts_clear_feedback(success=False) == (
        "Could not clear transcript history. Try again.",
        "error",
    )
