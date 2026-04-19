"""Shared UI styles and constants for Windows PySide6 components.

This module centralizes styling to avoid duplication between
settings_windows.py and onboarding_wizard_windows.py.
"""

from __future__ import annotations

# =============================================================================
# Window Constants
# =============================================================================

CARD_PADDING = 16
CARD_CORNER_RADIUS = 12
CARD_SPACING = 12

# =============================================================================
# Typography
# =============================================================================

DEFAULT_FONT_FAMILY = "Segoe UI"

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
    padding: 8px 12px;
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
    padding: 8px 12px;
    color: {COLORS["text"]};
    min-width: 120px;
}}

QComboBox:focus {{
    border-color: {COLORS["accent"]};
}}

QComboBox::drop-down {{
    border: none;
    width: 24px;
}}

QComboBox::down-arrow {{
    image: none;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 6px solid {COLORS["text_secondary"]};
    margin-right: 8px;
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
    width: 18px;
    height: 18px;
    border-radius: 4px;
    border: 1px solid {COLORS["border"]};
    background-color: {COLORS["bg_input"]};
}}

QCheckBox::indicator:checked {{
    background-color: {COLORS["accent"]};
    border-color: {COLORS["accent"]};
}}

QPushButton {{
    background-color: {COLORS["bg_input"]};
    border: 1px solid {COLORS["border"]};
    border-radius: 6px;
    padding: 8px 16px;
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

# Settings-specific additions (tabs)
SETTINGS_STYLESHEET_ADDITIONS = f"""
QTabWidget::pane {{
    border: 1px solid {COLORS["border"]};
    border-radius: 8px;
    background-color: transparent;
    padding: 8px;
}}

QTabBar::tab {{
    background-color: transparent;
    color: {COLORS["text_secondary"]};
    padding: 8px 16px;
    margin-right: 4px;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
}}

QTabBar::tab:selected {{
    background-color: {COLORS["bg_card"]};
    color: {COLORS["text"]};
}}

QTabBar::tab:hover:!selected {{
    background-color: rgba(255, 255, 255, 0.03);
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
