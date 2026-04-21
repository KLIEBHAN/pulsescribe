"""Shared copy helpers for visible logs / transcript panels.

Keeps manual action feedback, empty states and load-error wording aligned across
Welcome (macOS) and Settings (Windows).
"""

from __future__ import annotations


def _normalize_detail(value: object) -> str:
    return str(value or "").strip()



def build_logs_empty_state_text(log_file: object) -> str:
    return "No logs yet.\n\nPulseScribe will create a log file here:\n" + str(log_file)



def build_logs_load_error_text(error: object) -> str:
    detail = _normalize_detail(error)
    if detail:
        return f"Could not read the log file.\n\nDetails: {detail}"
    return "Could not read the log file."



def build_logs_manual_refresh_feedback(
    *,
    changed: bool,
    view: str,
) -> tuple[str, str]:
    normalized = (view or "logs").strip().lower()
    if normalized == "transcripts":
        if changed:
            return "Transcript history refreshed.", "success"
        return "Transcript history is already up to date.", "text_secondary"
    if changed:
        return "Logs refreshed.", "success"
    return "Logs are already up to date.", "text_secondary"



def build_logs_open_feedback(*, found_log: bool, destination: str) -> tuple[str, str]:
    target = "log file" if found_log else "logs folder"
    place = _normalize_detail(destination) or "file browser"
    return f"Opened {target} in {place}.", "success"



def build_transcripts_load_error_text(error: object) -> str:
    detail = _normalize_detail(error)
    if detail:
        return f"Could not load transcript history.\n\nDetails: {detail}"
    return "Could not load transcript history."



def build_transcripts_count_text(entry_count: int) -> str:
    count = max(0, int(entry_count))
    if count == 0:
        return "No transcript history yet"
    if count == 1:
        return "1 recent transcription"
    return f"{count} recent transcriptions"



def build_transcripts_hint_text(entry_count: int) -> str:
    count = max(0, int(entry_count))
    if count == 0:
        return (
            "Stored locally on this device. Your next dictation will appear here automatically."
        )
    return (
        "Stored locally on this device. Clear History permanently removes these entries from this device."
    )



def build_transcripts_load_feedback() -> tuple[str, str]:
    return "Could not load transcript history. Try Refresh or Clear History.", "error"



def build_transcripts_clear_feedback(*, success: bool) -> tuple[str, str]:
    if success:
        return "Transcript history cleared.", "success"
    return "Could not clear transcript history. Try again.", "error"
