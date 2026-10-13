"""Shared UI styles and constants for Windows PySide6 components.

This module centralizes styling to avoid duplication between
settings_windows.py and onboarding_wizard_windows.py.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

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
# (title 28, subtitle 13, card title 13 semibold, body 11-12). Windows keeps
# its 12 px system body size so text stays as readable as native dialogs.
FONT_ROLES: dict[str, tuple[int, str]] = {
    "window_title": (28, "bold"),
    "window_subtitle": (13, "normal"),
    "card_title": (14, "semibold"),
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

# Settings-specific additions: a centered, compact segmented tab bar like the
# macOS NSTabView so all tabs fit without scroll arrows, plus segmented buttons.
SETTINGS_STYLESHEET_ADDITIONS = f"""
QTabWidget::tab-bar {{
    alignment: center;
}}

QTabWidget::pane {{
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: 10px;
    background-color: transparent;
    top: -1px;
}}

QTabBar::tab {{
    background-color: transparent;
    color: {COLORS["text_secondary"]};
    font-size: 12px;
    padding: 4px 8px;
    margin: 0 1px 6px 1px;
    border-radius: 5px;
}}

QTabBar::tab:selected {{
    background-color: rgba(255, 255, 255, 0.16);
    color: {COLORS["text"]};
}}

QTabBar::tab:hover:!selected {{
    background-color: rgba(255, 255, 255, 0.06);
}}

QFrame#segmentGroup {{
    background-color: rgba(255, 255, 255, 0.06);
    border-radius: 7px;
}}

QPushButton#segment {{
    background-color: transparent;
    border: none;
    border-radius: 5px;
    padding: 4px 16px;
    min-width: 0;
    color: {COLORS["text_secondary"]};
}}

QPushButton#segment:hover:!checked {{
    background-color: rgba(255, 255, 255, 0.06);
}}

QPushButton#segment:checked {{
    background-color: rgba(255, 255, 255, 0.16);
    color: {COLORS["text"]};
}}
"""

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
