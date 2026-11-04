"""Shared text for Settings → About → What's new."""

from ui.update_window_model import reflow_release_notes
from utils.release_notes import load_recent_release_notes


def build_release_history_text() -> str:
    """Load the five bundled releases when About is first opened."""
    return reflow_release_notes(load_recent_release_notes()) or (
        "Release notes are unavailable in this installation.\n"
        "You can find the release history on GitHub: "
        "https://github.com/KLIEBHAN/pulsescribe/releases"
    )
