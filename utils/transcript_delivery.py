"""Best-effort paste with a session-only copy of the last normal dictation."""

import logging

logger = logging.getLogger(__name__)

COPIED = "Copied to clipboard"
PASTE_FAILED = "Paste failed — transcript copied"
COPY_FAILED = "Copy failed — use Copy Last Transcript"
DELIVERY_HINTS = {
    PASTE_FAILED: "Paste manually, or choose Copy Last Transcript to copy it again.",
    COPY_FAILED: "Choose Copy Last Transcript in the menu to try copying again.",
}


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
                    return True, "Transcript pasted"
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
