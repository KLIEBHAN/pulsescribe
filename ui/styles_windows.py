"""Shared UI styles and constants for Windows PySide6 components.

This module centralizes styling to avoid duplication between
settings_windows.py and onboarding_wizard_windows.py.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ui import settings_theme as theme

if TYPE_CHECKING:
    from PySide6.QtGui import QFont

# =============================================================================
# Window Constants
# =============================================================================

CARD_PADDING = 16
CARD_CORNER_RADIUS = 12
CARD_SPACING = 12

# The ui package is bundled as data in the Windows builds, so these files ship
# next to the modules both from source and from the PyInstaller bundle.
_ASSET_DIR = Path(__file__).resolve().parent / "assets"
CHECK_ICON = (_ASSET_DIR / "check.svg").as_posix()
CHEVRON_DOWN_ICON = (_ASSET_DIR / "chevron-down.svg").as_posix()

# =============================================================================
# Typography
# =============================================================================

DEFAULT_FONT_FAMILY = "Segoe UI"
MONOSPACE_FONT_FAMILIES = ["Cascadia Mono", "Consolas", "Menlo", "Courier New"]

# Type scale in logical pixels, mirroring the macOS settings hierarchy
# (page title 24, sidebar 14-15, card title 13 semibold, body 11-12). Windows keeps
# its 12 px system body size so text stays as readable as native dialogs.
FONT_ROLES: dict[str, tuple[int, str]] = {
    "page_title": (24, "bold"),
    "sidebar_title": (15, "bold"),
    "sidebar_caption": (13, "normal"),
    "sidebar_item": (14, "normal"),
    "card_title": (theme.SECTION_TITLE_SIZE, "semibold"),
    "badge": (12, "semibold"),  # Qt pixel sizes are integers; macOS uses 12.5
    "section": (12, "semibold"),
    "body": (12, "normal"),
    "caption": (11, "normal"),
    "mono": (12, "normal"),
}


def ui_font(role: str) -> "QFont":
    """Return the font for a type-scale role, e.g. ``label.setFont(ui_font("caption"))``.

    Sizes are logical pixels so Windows display scaling applies once via the
    device pixel ratio and the hierarchy matches the macOS window.
    """
    from PySide6.QtGui import QFont

    pixel_size, weight = FONT_ROLES[role]
    font = QFont(DEFAULT_FONT_FAMILY)
    if role == "mono":
        font.setFamilies(MONOSPACE_FONT_FAMILIES)
        font.setStyleHint(QFont.StyleHint.Monospace)
    font.setPixelSize(pixel_size)
    if weight == "bold":
        font.setWeight(QFont.Weight.Bold)
    elif weight == "semibold":
        font.setWeight(QFont.Weight.DemiBold)
    return font

# =============================================================================
# Dropdown Options (shared across UI components)
# =============================================================================

LANGUAGE_OPTIONS = ["auto", "de", "en", "es", "fr", "it", "pt", "nl", "pl", "ru", "zh"]

# =============================================================================
# Colors (Dark Theme)
# =============================================================================

COLORS = {
    "bg_window": "#1a1a1a",
    "bg_card": "rgba(255, 255, 255, 0.06)",
    "bg_input": "rgba(255, 255, 255, 0.08)",
    "border": "rgba(255, 255, 255, 0.15)",
    "text": "#ffffff",
    "text_secondary": "rgba(255, 255, 255, 0.6)",
    "text_hint": "rgba(255, 255, 255, 0.4)",
    "accent": "#007AFF",
    "accent_hover": "#0066DD",
    "success": "#4CAF50",
    "error": "#FF5252",
    "warning": "#FFC107",
}

# =============================================================================
# Base Stylesheet
# =============================================================================

BASE_STYLESHEET = f"""
QDialog {{
    background-color: {COLORS["bg_window"]};
}}

QLabel {{
    color: {COLORS["text"]};
    background: transparent;
}}

QLineEdit {{
    background-color: {COLORS["bg_input"]};
    border: 1px solid {COLORS["border"]};
    border-radius: 6px;
    padding: 5px 10px;
    color: {COLORS["text"]};
    selection-background-color: {COLORS["accent"]};
    placeholder-text-color: {COLORS["text_hint"]};
}}

QLineEdit:focus {{
    border-color: {COLORS["accent"]};
}}

QLineEdit:disabled {{
    color: {COLORS["text_hint"]};
}}

QComboBox {{
    background-color: {COLORS["bg_input"]};
    border: 1px solid {COLORS["border"]};
    border-radius: 6px;
    padding: 5px 28px 5px 10px;
    color: {COLORS["text"]};
    min-width: 120px;
}}

QComboBox:focus {{
    border-color: {COLORS["accent"]};
}}

QComboBox::drop-down {{
    subcontrol-origin: padding;
    subcontrol-position: center right;
    border: none;
    width: 24px;
}}

QComboBox::down-arrow {{
    image: url("{CHEVRON_DOWN_ICON}");
    width: 10px;
    height: 6px;
}}

QComboBox QAbstractItemView {{
    background-color: #2a2a2a;
    border: 1px solid {COLORS["border"]};
    selection-background-color: {COLORS["accent"]};
    color: {COLORS["text"]};
}}

QCheckBox {{
    color: {COLORS["text"]};
    spacing: 8px;
}}

QCheckBox::indicator {{
    width: 16px;
    height: 16px;
    border-radius: 4px;
    border: 1px solid {COLORS["border"]};
    background-color: {COLORS["bg_input"]};
}}

QCheckBox::indicator:checked {{
    background-color: {COLORS["accent"]};
    border-color: {COLORS["accent"]};
    image: url("{CHECK_ICON}");
}}

QCheckBox::indicator:disabled {{
    background-color: rgba(255, 255, 255, 0.03);
}}

QPushButton {{
    background-color: {COLORS["bg_input"]};
    border: 1px solid {COLORS["border"]};
    border-radius: 6px;
    padding: 5px 14px;
    color: {COLORS["text"]};
    min-width: 80px;
}}

QPushButton:hover {{
    background-color: rgba(255, 255, 255, 0.12);
}}

QPushButton:focus {{
    border-color: {COLORS["accent"]};
    background-color: rgba(0, 122, 255, 0.10);
}}

QPushButton:pressed {{
    background-color: rgba(255, 255, 255, 0.08);
}}

QPushButton:disabled {{
    color: {COLORS["text_hint"]};
    background-color: rgba(255, 255, 255, 0.03);
}}

QPushButton#primary {{
    background-color: {COLORS["accent"]};
    border-color: {COLORS["accent"]};
}}

QPushButton#primary:hover {{
    background-color: {COLORS["accent_hover"]};
}}

QPushButton#primary:disabled {{
    background-color: rgba(0, 122, 255, 0.4);
}}

QPlainTextEdit {{
    background-color: {COLORS["bg_input"]};
    border: 1px solid {COLORS["border"]};
    border-radius: 6px;
    padding: 8px;
    color: {COLORS["text"]};
    selection-background-color: {COLORS["accent"]};
    placeholder-text-color: {COLORS["text_hint"]};
}}

QPlainTextEdit:focus {{
    border-color: {COLORS["accent"]};
}}

QScrollArea {{
    background: transparent;
    border: none;
}}

QScrollBar:vertical {{
    background-color: transparent;
    width: 8px;
    margin: 0;
}}

QScrollBar::handle:vertical {{
    background-color: rgba(255, 255, 255, 0.2);
    border-radius: 4px;
    min-height: 30px;
}}

QScrollBar::handle:vertical:hover {{
    background-color: rgba(255, 255, 255, 0.3);
}}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}

QSlider::groove:horizontal {{
    background-color: {COLORS["bg_input"]};
    height: 4px;
    border-radius: 2px;
}}

QSlider::handle:horizontal {{
    background-color: {COLORS["accent"]};
    width: 16px;
    height: 16px;
    margin: -6px 0;
    border-radius: 8px;
}}

QSlider::handle:horizontal:hover {{
    background-color: {COLORS["accent_hover"]};
}}

QFrame#card {{
    background-color: {COLORS["bg_card"]};
    border: 1px solid {COLORS["border"]};
    border-radius: {CARD_CORNER_RADIUS}px;
}}
"""

# Settings window colors: the shared keys mapped to the settings design tokens.
SETTINGS_COLORS = {
    **COLORS,
    "bg_window": theme.BG,
    "text": theme.TEXT,
    "text_secondary": theme.MUTED,
    "text_hint": theme.PLACEHOLDER,
    "accent": theme.TEXT,
    "success": theme.BADGE_TONES["ok"][1],
    "warning": theme.BADGE_TONES["warn"][1],
    "error": theme.BADGE_TONES["err"][1],
}

# Settings-specific additions: flat dark sections on the window background, the
# left sidebar navigation (like the macOS window), and segmented buttons.
SETTINGS_STYLESHEET_ADDITIONS = f"""
QDialog,
QScrollArea > QWidget#qt_scrollarea_viewport,
QWidget#settingsPage {{
    background-color: {theme.BG};
}}

QLabel, QCheckBox {{
    color: {theme.TEXT};
}}

QLineEdit, QComboBox, QPlainTextEdit {{
    background-color: {theme.INPUT_BG};
    border: 1px solid {theme.INPUT_BORDER};
    border-radius: {theme.RADIUS}px;
    color: {theme.TEXT};
    selection-background-color: {theme.SECONDARY_BORDER};
    placeholder-text-color: {theme.PLACEHOLDER};
}}

QLineEdit:focus, QComboBox:focus, QPlainTextEdit:focus {{
    border-color: {theme.MUTED};
}}

QLineEdit:disabled, QComboBox:disabled, QPlainTextEdit:disabled {{
    color: {theme.PLACEHOLDER};
}}

QComboBox QAbstractItemView {{
    background-color: {theme.INPUT_BG};
    border: 1px solid {theme.INPUT_BORDER};
    selection-background-color: {theme.ACTIVE_BG};
    color: {theme.TEXT};
}}

QCheckBox:disabled {{
    color: {theme.PLACEHOLDER};
}}

QCheckBox::indicator {{
    border-radius: {theme.RADIUS}px;
    border: 1px solid {theme.INPUT_BORDER};
    background-color: {theme.INPUT_BG};
}}

QCheckBox::indicator:checked {{
    background-color: {theme.SECONDARY_BORDER};
    border-color: {theme.MUTED};
}}

QPushButton {{
    background-color: transparent;
    border: 1px solid {theme.SECONDARY_BORDER};
    border-radius: {theme.RADIUS}px;
    color: {theme.TEXT};
}}

QPushButton:hover {{
    background-color: {theme.ACTIVE_BG};
}}

QPushButton:focus {{
    border-color: {theme.MUTED};
    background-color: transparent;
}}

QPushButton:pressed {{
    background-color: {theme.BORDER};
}}

QPushButton:disabled {{
    color: {theme.PLACEHOLDER};
    background-color: transparent;
}}

QPushButton#primary {{
    background-color: {theme.PRIMARY_BG};
    border-color: {theme.PRIMARY_BG};
    color: {theme.PRIMARY_TEXT};
}}

QPushButton#primary:hover, QPushButton#primary:focus {{
    background-color: {theme.ACTIVE_TEXT};
    border-color: {theme.ACTIVE_TEXT};
}}

QPushButton#primary:disabled {{
    background-color: {theme.SECONDARY_BORDER};
    border-color: {theme.SECONDARY_BORDER};
    color: {theme.MUTED};
}}

QSlider::groove:horizontal {{
    background-color: {theme.INPUT_BORDER};
}}

QSlider::handle:horizontal, QSlider::handle:horizontal:hover {{
    background-color: {theme.TEXT};
}}

QFrame#card {{
    background-color: transparent;
    border: none;
    border-top: 1px solid {theme.LINE};
    border-radius: 0;
}}

QWidget#settingsRow, QWidget#saveBar {{
    border-top: 1px solid {theme.LINE};
}}

QFrame#toast {{
    background-color: {theme.TOAST_BG};
    border: 1px solid {theme.TOAST_BORDER};
    border-radius: {theme.RADIUS}px;
}}

QPushButton#textButton {{
    background-color: transparent;
    border: none;
    min-width: 0;
    padding: 5px 10px;
    color: {theme.MUTED};
}}

QPushButton#textButton:hover, QPushButton#textButton:focus {{
    color: {theme.TEXT};
}}

QWidget#sidebar {{
    background-color: {theme.SIDEBAR_BG};
    border-right: 1px solid {theme.BORDER};
}}

QListWidget#sidebarNav {{
    background: transparent;
    border: none;
    outline: none;
}}

QListWidget#sidebarNav::item {{
    color: {theme.NAV};
    padding: 9px 10px;
    margin-bottom: 2px;
    border: none;
    border-radius: {theme.RADIUS}px;
}}

QListWidget#sidebarNav::item:selected {{
    background-color: {theme.ACTIVE_BG};
    color: {theme.ACTIVE_TEXT};
}}

QTabWidget::pane {{
    border: none;
    background-color: transparent;
}}

QFrame#segmentGroup {{
    background-color: {theme.INPUT_BG};
    border: 1px solid {theme.BORDER};
    border-radius: {theme.RADIUS}px;
}}

QPushButton#segment {{
    background-color: transparent;
    border: none;
    border-radius: {theme.RADIUS}px;
    padding: 4px 16px;
    min-width: 0;
    color: {theme.MUTED};
}}

QPushButton#segment:hover:!checked {{
    background-color: {theme.BORDER};
}}

QPushButton#segment:checked {{
    background-color: {theme.ACTIVE_BG};
    color: {theme.ACTIVE_TEXT};
}}
"""


def badge_stylesheet(status_color: str) -> str:
    """Stylesheet for a status label shown as a tinted badge, e.g. ``badge_stylesheet("success")``."""
    background, text = theme.BADGE_TONES[theme.badge_tone(status_color)]
    return (
        f"background-color: {background}; color: {text}; "
        f"border-radius: {theme.RADIUS}px; "
        f"padding: {theme.BADGE_PADDING_V}px {theme.BADGE_PADDING_H}px;"
    )


# Wizard-specific additions (choice buttons)
WIZARD_STYLESHEET_ADDITIONS = f"""
QPushButton#choice {{
    padding: 16px 20px;
    text-align: left;
    min-height: 60px;
}}

QPushButton#choice:hover {{
    background-color: rgba(255, 255, 255, 0.10);
}}

QPushButton#choice:checked,
QPushButton#choice:focus {{
    border-color: {COLORS["accent"]};
    background-color: rgba(0, 122, 255, 0.15);
}}
"""


def get_settings_stylesheet() -> str:
    """Returns the complete stylesheet for the Settings window."""
    return BASE_STYLESHEET + SETTINGS_STYLESHEET_ADDITIONS


def get_wizard_stylesheet() -> str:
    """Returns the complete stylesheet for the Onboarding Wizard."""
    return BASE_STYLESHEET + WIZARD_STYLESHEET_ADDITIONS


# =============================================================================
# pynput Key-Mapping (cached for performance)
# =============================================================================

_pynput_available: bool | None = None
_pynput_key_map: dict | None = None


def get_pynput_key_map() -> tuple[bool, dict]:
    """Loads pynput once and returns (available, key_map).

    Returns:
        Tuple of (is_available, key_mapping_dict)
    """
    global _pynput_available, _pynput_key_map

    if _pynput_available is not None:
        return _pynput_available, _pynput_key_map or {}

    try:
        from pynput import keyboard

        _pynput_key_map = {
            keyboard.Key.ctrl: "ctrl",
            keyboard.Key.ctrl_l: "ctrl",
            keyboard.Key.ctrl_r: "ctrl",
            keyboard.Key.alt: "alt",
            keyboard.Key.alt_l: "alt",
            keyboard.Key.alt_r: "alt",
            keyboard.Key.alt_gr: "alt",
            keyboard.Key.shift: "shift",
            keyboard.Key.shift_l: "shift",
            keyboard.Key.shift_r: "shift",
            keyboard.Key.cmd: "win",
            keyboard.Key.cmd_l: "win",
            keyboard.Key.cmd_r: "win",
            keyboard.Key.space: "space",
            keyboard.Key.tab: "tab",
            keyboard.Key.backspace: "backspace",
            keyboard.Key.delete: "delete",
            keyboard.Key.enter: "enter",
            keyboard.Key.esc: "esc",
        }
        _pynput_available = True
    except ImportError:
        _pynput_available = False
        _pynput_key_map = {}

    return _pynput_available, _pynput_key_map
