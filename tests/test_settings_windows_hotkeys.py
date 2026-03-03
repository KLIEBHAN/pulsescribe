import pytest
import threading

pytest.importorskip("PySide6")

from ui.settings_windows import SettingsWindow


class _FakeField:
    def __init__(self, value: str):
        self._value = value

    def text(self) -> str:
        return self._value

    def setText(self, value: str) -> None:
        self._value = value


class _FakeLabel:
    def __init__(self):
        self.text = ""
        self.style = ""

    def setText(self, text: str) -> None:
        self.text = text

    def setStyleSheet(self, style: str) -> None:
        self.style = style


class _FakeButton:
    def __init__(self):
        self.text = ""
        self.style = ""

    def setText(self, text: str) -> None:
        self.text = text

    def setStyleSheet(self, style: str) -> None:
        self.style = style


def _make_window(toggle: str, hold: str) -> SettingsWindow:
    window = SettingsWindow.__new__(SettingsWindow)
    window._toggle_hotkey_field = _FakeField(toggle)
    window._hold_hotkey_field = _FakeField(hold)
    window._hotkey_status = _FakeLabel()
    return window


def test_validate_hotkeys_for_save_normalizes_fields():
    window = _make_window("ALT+CTRL+Return", "ctrl+shift+space")

    result = window._validate_hotkeys_for_save()

    assert result == ("ctrl+alt+enter", "ctrl+shift+space")
    assert window._toggle_hotkey_field.text() == "ctrl+alt+enter"
    assert window._hold_hotkey_field.text() == "ctrl+shift+space"


def test_validate_hotkeys_for_save_rejects_duplicates():
    window = _make_window("ctrl+alt+r", "alt+ctrl+r")

    result = window._validate_hotkeys_for_save()

    assert result is None
    assert "same hotkey" in window._hotkey_status.text.lower()


def test_validate_hotkeys_for_save_rejects_invalid_tokens():
    window = _make_window("ctrl+invalid", "")

    result = window._validate_hotkeys_for_save()

    assert result is None
    assert "invalid" in window._hotkey_status.text.lower()


def test_validate_hotkeys_for_save_rejects_modifier_only_hotkeys():
    window = _make_window("ctrl+alt", "")

    result = window._validate_hotkeys_for_save()

    assert result is None
    assert "non-modifier" in window._hotkey_status.text.lower()


def test_start_hotkey_recording_stops_previous_capture():
    window = SettingsWindow.__new__(SettingsWindow)
    window._recording_hotkey_for = "toggle"
    window._pressed_keys = {"ctrl", "r"}
    window._pressed_keys_lock = threading.Lock()
    window._toggle_record_btn = _FakeButton()
    window._hold_record_btn = _FakeButton()

    stop_calls: list[bool] = []
    start_calls: list[bool] = []

    window._stop_pynput_listener = lambda: stop_calls.append(True)
    window._start_pynput_listener = lambda: start_calls.append(True)
    window._set_hotkey_status = lambda *_args, **_kwargs: None
    window.setFocus = lambda: None

    SettingsWindow._start_hotkey_recording(window, "hold")

    assert stop_calls == [True]
    assert start_calls == [True]
    assert window._recording_hotkey_for == "hold"
    assert window._pressed_keys == set()
    assert window._toggle_record_btn.text == "Record"
    assert window._hold_record_btn.text == "Press key..."


def test_clear_hotkey_field_clears_toggle_and_updates_status():
    window = _make_window("ctrl+alt+r", "ctrl+win")
    window._recording_hotkey_for = None
    window._stop_hotkey_recording = lambda *_args, **_kwargs: None

    SettingsWindow._clear_hotkey_field(window, "toggle")

    assert window._toggle_hotkey_field.text() == ""
    assert window._hold_hotkey_field.text() == "ctrl+win"
    assert "cleared" in window._hotkey_status.text.lower()


def test_clear_hotkey_field_stops_active_recording_before_clearing():
    window = _make_window("ctrl+alt+r", "")
    window._recording_hotkey_for = "toggle"

    stop_calls: list[object] = []
    window._stop_hotkey_recording = (
        lambda hotkey=None: stop_calls.append(hotkey)
    )

    SettingsWindow._clear_hotkey_field(window, "toggle")

    assert stop_calls == [None]
    assert window._toggle_hotkey_field.text() == ""
