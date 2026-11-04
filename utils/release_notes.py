"""Read the release history shipped with the app; no network access is needed."""

from __future__ import annotations

import logging
import re
from pathlib import Path

from utils.paths import get_resource_path

logger = logging.getLogger(__name__)
_RELEASE_FILE = re.compile(r"v(\d+)\.(\d+)\.(\d+)\.md")


def load_recent_release_notes(limit: int = 5) -> str:
    """Read the newest ``limit`` stable releases, without installation notes.

    Files live in ``docs/releases`` in both source checkouts and app bundles.
    Missing or unreadable notes are skipped so About remains usable.
    """
    if limit <= 0:
        return ""
    directory = Path(get_resource_path("docs/releases"))
    try:
        files = []
        for path in directory.iterdir():
            match = _RELEASE_FILE.fullmatch(path.name)
            if match and path.is_file():
                files.append((tuple(int(part) for part in match.groups()), path))
    except OSError:
        logger.warning("Release notes directory is unavailable: %s", directory)
        return ""

    notes = []
    for _, path in sorted(files, reverse=True):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            logger.warning("Could not read release notes: %s", path)
            continue
        text = text.split("<!-- install-notes -->", 1)[0].strip()
        if text:
            notes.append(text)
        if len(notes) == limit:
            break
    return "\n\n────────────\n\n".join(notes)
