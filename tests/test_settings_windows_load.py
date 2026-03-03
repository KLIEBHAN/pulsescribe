import pytest

pytest.importorskip("PySide6")

import ui.settings_windows as settings_mod
from ui.settings_windows import SettingsWindow


class _FakeCheckbox:
    def __init__(self):
        self.checked: bool | None = None

    def setChecked(self, value: bool) -> None:
        self.checked = value


class _FakeField:
    def __init__(self):
        self.value = ""

    def setText(self, value: str) -> None:
        self.value = value


def _make_window() -> SettingsWindow:
    window = SettingsWindow.__new__(SettingsWindow)
    window._mode_combo = None
    window._lang_combo = None
    window._local_backend_combo = None
    window._local_model_combo = None
    window._streaming_checkbox = _FakeCheckbox()
    window._refine_checkbox = _FakeCheckbox()
    window._refine_provider_combo = None
    window._refine_model_field = None
    window._overlay_checkbox = _FakeCheckbox()
    window._rtf_checkbox = _FakeCheckbox()
    window._clipboard_restore_checkbox = _FakeCheckbox()
    window._toggle_hotkey_field = _FakeField()
    window._hold_hotkey_field = _FakeField()
    window._api_fields = {}
    window._api_status = {}
    window._on_mode_changed = lambda _mode: None
    return window


def test_load_settings_parses_common_bool_variants(monkeypatch):
    values = {
        "PULSESCRIBE_STREAMING": "FALSE",
        "PULSESCRIBE_REFINE": "YES",
        "PULSESCRIBE_OVERLAY": "0",
        "PULSESCRIBE_SHOW_RTF": "ON",
        "PULSESCRIBE_CLIPBOARD_RESTORE": "off",
    }
    monkeypatch.setattr(settings_mod, "get_env_setting", lambda key: values.get(key))

    window = _make_window()
    window._load_settings()

    assert window._streaming_checkbox.checked is False
    assert window._refine_checkbox.checked is True
    assert window._overlay_checkbox.checked is False
    assert window._rtf_checkbox.checked is True
    assert window._clipboard_restore_checkbox.checked is False


def test_load_settings_uses_defaults_for_missing_or_invalid_bool_values(monkeypatch):
    values = {
        "PULSESCRIBE_STREAMING": "invalid",
        "PULSESCRIBE_REFINE": None,
        "PULSESCRIBE_OVERLAY": "",
        "PULSESCRIBE_SHOW_RTF": "maybe",
        "PULSESCRIBE_CLIPBOARD_RESTORE": None,
    }
    monkeypatch.setattr(settings_mod, "get_env_setting", lambda key: values.get(key))

    window = _make_window()
    window._load_settings()

    assert window._streaming_checkbox.checked is True
    assert window._refine_checkbox.checked is False
    assert window._overlay_checkbox.checked is True
    assert window._rtf_checkbox.checked is False
    assert window._clipboard_restore_checkbox.checked is False


def test_load_settings_uses_default_hotkeys_when_none_are_configured(monkeypatch):
    monkeypatch.setattr(settings_mod, "get_env_setting", lambda _key: None)
    monkeypatch.setattr(settings_mod, "read_env_file", lambda: {})

    window = _make_window()
    window._load_settings()

    assert (
        window._toggle_hotkey_field.value
        == settings_mod.DEFAULT_WINDOWS_TOGGLE_HOTKEY
    )
    assert window._hold_hotkey_field.value == settings_mod.DEFAULT_WINDOWS_HOLD_HOTKEY
