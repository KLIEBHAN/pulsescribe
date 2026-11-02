"""Best-effort paste with a session-only copy of the last normal dictation."""

import logging

logger = logging.getLogger(__name__)

PASTED = "Transcript pasted"
COPIED = "Copied to clipboard"
TEST_COMPLETE = "Test complete"  # DONE payload of onboarding test runs (nothing pasted)
PASTE_FAILED = "Paste failed — transcript copied"
COPY_FAILED = "Copy failed — use Copy Last Transcript"
DELIVERY_HINTS = {
    PASTE_FAILED: "Paste manually, or choose Copy Last Transcript to copy it again.",
    COPY_FAILED: "Choose Copy Last Transcript in the menu to try copying again.",
}


def done_status(
    feedback: str, *, app: str | None = None, seconds: float | None = None
) -> str:
    """DONE payload: delivery feedback, target app and transcription time.

    Reads as a sentence on the menu bar and tray ("Transcript pasted into
    Mail · 1.2 s"); the overlay splits it again. The app is only named
    for a paste.
    """
    text = feedback
    if app and feedback == PASTED:
        text += f" into {app}"
    if seconds is not None and seconds >= 0:
        text += f" · {seconds:.1f} s"
    return text


def transcription_time_enabled() -> bool:
    """Whether DONE shows the transcription time (env key ``PULSESCRIBE_SHOW_RTF``)."""
    from utils.preferences import get_env_setting

    value = get_env_setting("PULSESCRIBE_SHOW_RTF")
    return value is not None and value.lower() in ("true", "1", "yes", "on")


class TranscriptDelivery:
    """Keep recovery independent of clipboard restoration and history writes."""

    def __init__(self):
        self.text = ""

    def deliver(self, text: str, *, auto_paste: bool = True) -> tuple[bool, str]:
        """Return success and feedback only after the requested operation finishes."""
        from utils.hotkey import paste_transcript

        self.text = text
        if auto_paste:
            try:
                if paste_transcript(text):
                    return True, PASTED
            except Exception as exc:
                logger.warning("Auto-paste failed (%s)", type(exc).__name__)
        if self._copy(text):
            return (False, PASTE_FAILED) if auto_paste else (True, COPIED)
        return False, COPY_FAILED

    def copy_last(self) -> str:
        """Copy explicitly, without auto-paste or restoring the old clipboard."""
        text = self.text
        if not text:
            return "No transcript to copy yet."
        return "Last transcript copied." if self._copy(text) else "Could not copy. Try again."

    @staticmethod
    def _copy(text: str) -> bool:
        from utils.hotkey import copy_transcript

        try:
            return copy_transcript(text)
        except Exception as exc:
            logger.warning("Clipboard copy failed (%s)", type(exc).__name__)
            return False
