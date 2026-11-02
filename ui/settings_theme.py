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


def hex_rgb(hex_color: str) -> tuple[int, int, int]:
    """Split a token such as ``"#181818"`` into ``(24, 24, 24)``."""
    value = int(hex_color[1:], 16)
    return value >> 16, (value >> 8) & 0xFF, value & 0xFF


def ns_color(hex_color: str):
    """Return the ``NSColor`` for a token, e.g. ``ns_color(BG)`` (macOS only)."""
    from AppKit import NSColor  # type: ignore[import-not-found]

    red, green, blue = hex_rgb(hex_color)
    return NSColor.colorWithSRGBRed_green_blue_alpha_(red / 255, green / 255, blue / 255, 1.0)


def ns_rule(x: float, y: float, width: float):
    """Return a 1 px ``LINE`` rule at ``(x, y)`` for section and row separators (macOS only)."""
    from AppKit import NSBox  # type: ignore[import-not-found]
    from Foundation import NSMakeRect  # type: ignore[import-not-found]

    rule = NSBox.alloc().initWithFrame_(NSMakeRect(x, y, width, 1))
    rule.setBoxType_(4)  # Custom
    rule.setBorderType_(0)  # None
    rule.setFillColor_(ns_color(LINE))
    rule.setContentViewMargins_((0, 0))
    return rule


def set_ns_badge(field, text: str, tone: str, *, right_edge: float | None = None) -> None:
    """Render an AppKit text field as a tinted badge sized to ``text`` (macOS only).

    The badge keeps its left edge, or its right edge when ``right_edge`` is given,
    and stays vertically centered on the field's previous frame.
    """
    import math

    from AppKit import (  # type: ignore[import-not-found]
        NSAttributedString,
        NSBaselineOffsetAttributeName,
        NSFont,
        NSFontAttributeName,
        NSFontWeightSemibold,
        NSForegroundColorAttributeName,
        NSMutableParagraphStyle,
        NSParagraphStyleAttributeName,
        NSTextAlignmentCenter,
    )
    from Foundation import NSMakeRect  # type: ignore[import-not-found]

    background, foreground = BADGE_TONES.get(tone, BADGE_TONES["muted"])
    if field.drawsBackground() and field.stringValue() == text:
        if field.backgroundColor().isEqual_(ns_color(background)):
            return  # already showing this badge
    font = NSFont.systemFontOfSize_weight_(BADGE_FONT_SIZE, NSFontWeightSemibold)
    text_height = math.ceil(font.ascender() - font.descender() + font.leading())
    height = text_height + 2 * BADGE_PADDING_V
    # A fixed line height puts the text at the bottom; the offset centers it.
    paragraph = NSMutableParagraphStyle.alloc().init()
    paragraph.setAlignment_(NSTextAlignmentCenter)
    paragraph.setMinimumLineHeight_(height)
    paragraph.setMaximumLineHeight_(height)
    value = NSAttributedString.alloc().initWithString_attributes_(
        text,
        {
            NSFontAttributeName: font,
            NSForegroundColorAttributeName: ns_color(foreground),
            NSParagraphStyleAttributeName: paragraph,
            NSBaselineOffsetAttributeName: BADGE_PADDING_V,
        },
    )
    field.setAttributedStringValue_(value)
    field.setDrawsBackground_(True)
    field.setBackgroundColor_(ns_color(background))
    field.setWantsLayer_(True)
    field.layer().setCornerRadius_(RADIUS)
    field.layer().setMasksToBounds_(True)
    width = math.ceil(value.size().width) + 2 * BADGE_PADDING_H + 4  # 2 px cell inset per side
    frame = field.frame()
    x = frame.origin.x if right_edge is None else right_edge - width
    center_y = frame.origin.y + frame.size.height / 2
    field.setFrame_(NSMakeRect(x, round(center_y - height / 2), width, height))
