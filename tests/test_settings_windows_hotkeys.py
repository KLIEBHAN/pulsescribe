import pytest
import threading

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt

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


class _FakeKeyEvent:
    def __init__(
        self,
        key: int,
        modifiers: Qt.KeyboardModifier,
        *,
        auto_repeat: bool = False,
    ):
        self._key = key
        self._modifiers = modifiers
        self._auto_repeat = auto_repeat
        self.accepted = False

    def key(self) -> int:
        return self._key

    def modifiers(self) -> Qt.KeyboardModifier:
        return self._modifiers

    def isAutoRepeat(self) -> bool:
        return self._auto_repeat

    def accept(self) -> None:
        self.accepted = True


def _make_window(toggle: str, hold: str) -> SettingsWindow:
    window = SettingsWindow.__new__(SettingsWindow)
    window._toggle_hotkey_field = _FakeField(toggle)
    window._hold_hotkey_field = _FakeField(hold)
    window._hotkey_status = _FakeLabel()
    window._is_closed = False
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


def test_start_hotkey_recording_restores_previous_field_when_switching_kind():
    window = _make_window("ctrl+alt+r", "ctrl+alt+space")
    window._recording_hotkey_for = "toggle"
    window._hotkey_recording_previous = {
        "toggle": "ctrl+alt+r",
        "hold": "ctrl+alt+space",
    }
    window._pressed_keys = set()
    window._pressed_keys_lock = threading.Lock()
    window._toggle_record_btn = _FakeButton()
    window._hold_record_btn = _FakeButton()

    window._stop_pynput_listener = lambda: None
    window._start_pynput_listener = lambda: None
    window._set_hotkey_status = lambda *_args, **_kwargs: None
    window.setFocus = lambda: None

    SettingsWindow._start_hotkey_recording(window, "hold")

    assert window._toggle_hotkey_field.text() == "ctrl+alt+r"
    assert window._hold_hotkey_field.text() == ""
    assert window._recording_hotkey_for == "hold"


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


def test_start_hotkey_recording_clears_field_and_keeps_previous_value():
    window = _make_window("ctrl+alt+r", "")
    window._pressed_keys = set()
    window._pressed_keys_lock = threading.Lock()
    window._toggle_record_btn = _FakeButton()
    window._hold_record_btn = _FakeButton()

    window._stop_pynput_listener = lambda: None
    window._start_pynput_listener = lambda: None
    window._set_hotkey_status = lambda *_args, **_kwargs: None
    window.setFocus = lambda: None

    SettingsWindow._start_hotkey_recording(window, "toggle")

    assert window._toggle_hotkey_field.text() == ""
    assert window._hotkey_recording_previous["toggle"] == "ctrl+alt+r"


def test_stop_hotkey_recording_restores_previous_value_when_no_key_recorded():
    window = _make_window("ctrl+alt+r", "")
    window._recording_hotkey_for = "toggle"
    window._hotkey_recording_previous = {"toggle": "ctrl+alt+r", "hold": ""}
    window._toggle_record_btn = _FakeButton()
    window._hold_record_btn = _FakeButton()

    window._stop_pynput_listener = lambda: None

    SettingsWindow._stop_hotkey_recording(window, "")

    assert window._toggle_hotkey_field.text() == "ctrl+alt+r"
    assert "previous hotkey kept" in window._hotkey_status.text.lower()


def test_set_hotkey_field_text_noop_when_window_is_closed():
    window = _make_window("ctrl+alt+r", "")
    window._recording_hotkey_for = "toggle"
    window._is_closed = True

    SettingsWindow._set_hotkey_field_text(window, "ctrl+shift+r")

    assert window._toggle_hotkey_field.text() == "ctrl+alt+r"


def test_keypress_qt_fallback_ignores_auto_repeat_events():
    window = _make_window("ctrl+alt+r", "")
    window._recording_hotkey_for = "toggle"
    window._using_qt_grab = True

    event = _FakeKeyEvent(
        Qt.Key.Key_R,
        Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier,
        auto_repeat=True,
    )

    SettingsWindow.keyPressEvent(window, event)

    assert window._toggle_hotkey_field.text() == "ctrl+alt+r"
    assert event.accepted is True
