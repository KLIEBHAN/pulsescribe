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


class _FakeCombo:
    def __init__(self, items: list[str], current: str = "default"):
        self._items = list(items)
        self._current_index = 0
        if current in self._items:
            self._current_index = self._items.index(current)

    def findText(self, value: str) -> int:
        try:
            return self._items.index(value)
        except ValueError:
            return -1

    def setCurrentIndex(self, index: int) -> None:
        self._current_index = index

    def currentText(self) -> str:
        return self._items[self._current_index]


class _FakeSignal:
    def __init__(self):
        self.emitted = False

    def emit(self) -> None:
        self.emitted = True


def _make_window() -> SettingsWindow:
    window = SettingsWindow.__new__(SettingsWindow)
    window._mode_combo = None
    window._lang_combo = None
    window._local_backend_combo = None
    window._local_model_combo = None
    window._fp16_combo = _FakeCombo(["default", "true", "false"])
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


def _make_save_window(fp16_value: str) -> SettingsWindow:
    window = SettingsWindow.__new__(SettingsWindow)
    window._mode_combo = None
    window._lang_combo = None
    window._local_backend_combo = None
    window._local_model_combo = None
    window._streaming_checkbox = None
    window._refine_checkbox = None
    window._refine_provider_combo = None
    window._refine_model_field = None
    window._overlay_checkbox = None
    window._rtf_checkbox = None
    window._clipboard_restore_checkbox = None
    window._api_fields = {}
    window._api_status = {}
    window._fp16_combo = _FakeCombo(["default", "true", "false"], current=fp16_value)
    window._validate_hotkeys_for_save = lambda: ("ctrl+alt+r", "")
    window._save_all_prompts = lambda: None
    window._write_reload_signal = lambda: None
    window._show_save_feedback = lambda: None
    window._on_settings_changed_callback = None
    window.settings_changed = _FakeSignal()
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


def test_load_settings_prefers_canonical_fp16_key(monkeypatch):
    values = {
        settings_mod.LOCAL_FP16_ENV_KEY: "false",
        settings_mod.LEGACY_LOCAL_FP16_ENV_KEY: "true",
    }
    monkeypatch.setattr(settings_mod, "get_env_setting", lambda key: values.get(key))
    monkeypatch.setattr(settings_mod, "read_env_file", lambda: {})

    window = _make_window()
    window._load_settings()

    assert window._fp16_combo.currentText() == "false"


def test_load_settings_falls_back_to_legacy_fp16_key(monkeypatch):
    values = {
        settings_mod.LOCAL_FP16_ENV_KEY: None,
        settings_mod.LEGACY_LOCAL_FP16_ENV_KEY: "true",
    }
    monkeypatch.setattr(settings_mod, "get_env_setting", lambda key: values.get(key))
    monkeypatch.setattr(settings_mod, "read_env_file", lambda: {})

    window = _make_window()
    window._load_settings()

    assert window._fp16_combo.currentText() == "true"


def test_save_settings_uses_canonical_fp16_key_and_removes_legacy(monkeypatch):
    save_calls: list[tuple[str, str]] = []
    remove_calls: list[str] = []

    monkeypatch.setattr(
        settings_mod,
        "save_env_setting",
        lambda key, value: save_calls.append((key, value)),
    )
    monkeypatch.setattr(
        settings_mod,
        "remove_env_setting",
        lambda key: remove_calls.append(key),
    )
    monkeypatch.setattr(settings_mod, "set_api_key", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(settings_mod, "is_onboarding_complete", lambda: True)

    window = _make_save_window("true")
    window._save_settings()

    assert (settings_mod.LOCAL_FP16_ENV_KEY, "true") in save_calls
    assert settings_mod.LEGACY_LOCAL_FP16_ENV_KEY in remove_calls
    assert not any(
        key == settings_mod.LEGACY_LOCAL_FP16_ENV_KEY for key, _ in save_calls
    )
