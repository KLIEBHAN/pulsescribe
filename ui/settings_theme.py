"""Dark design tokens shared by the macOS and Windows settings windows.

Qt stylesheets use the hex strings directly; AppKit code converts them to
``NSColor`` once. Keep new settings colors here instead of inline hex values.
"""

BG = "#181818"
SIDEBAR_BG = "#121212"
BORDER = "#2a2a2a"
LINE = "#272727"
TEXT = "#ececec"
MUTED = "#949494"
NAV = "#adadad"
ACTIVE_BG = "#2a2a2a"
ACTIVE_TEXT = "#ffffff"
PRIMARY_BG = "#ececec"
PRIMARY_TEXT = "#181818"
SECONDARY_BORDER = "#3a3a3a"
INPUT_BG = "#1c1c1c"
INPUT_BORDER = "#363636"
PLACEHOLDER = "#6f6f6f"
TOAST_BG = "#242424"
TOAST_BORDER = "#383838"

RADIUS = 3
SECTION_TITLE_SIZE = 14
BADGE_FONT_SIZE = 12.5
BADGE_PADDING_V = 3
BADGE_PADDING_H = 8

# Badge tone -> (background, text).
BADGE_TONES = {
    "ok": ("#173326", "#6fd39b"),
    "warn": ("#3a2c12", "#f0b955"),
    "err": ("#3a1a1a", "#f08a8a"),
    "muted": (ACTIVE_BG, MUTED),
}

# Status color keys used by the feedback builders -> badge tone.
STATUS_BADGE_TONES = {"success": "ok", "warning": "warn", "error": "err"}


def badge_tone(status_color: str) -> str:
    """Map a feedback color key such as ``"success"`` to a badge tone."""
    return STATUS_BADGE_TONES.get(status_color, status_color if status_color in BADGE_TONES else "muted")
