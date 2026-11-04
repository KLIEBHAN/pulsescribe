"""Shared text for Settings → About → What's new."""

from ui.update_window_model import reflow_release_notes
from utils.release_notes import load_recent_release_notes

_UPDATE_HINT = (
    "Use **Check for Updates…** in the macOS menu bar or Windows tray to install this version."
)


def build_release_history_text() -> str:
    """Load the five bundled releases when About is first opened.

    Older notes put the install prompt before the install-notes marker. Hide
    that standalone paragraph here without changing the published update notes.
    """
    text = reflow_release_notes(load_recent_release_notes())
    text = "\n\n".join(
        paragraph for paragraph in text.split("\n\n") if paragraph.strip() != _UPDATE_HINT
    )
    return text or (
        "Release notes are unavailable in this installation.\n"
        "You can find the release history on GitHub: "
        "https://github.com/KLIEBHAN/pulsescribe/releases"
    )
