import pytest
import threading
import types
import sys

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt

from ui.onboarding_wizard_windows import (
    DEFAULT_WINDOWS_HOLD_HOTKEY,
    DEFAULT_WINDOWS_TOGGLE_HOTKEY,
    IPC_CONNECT_MAX_POLLS_BEFORE_TIMEOUT,
    IPC_MAX_POLLS_BEFORE_TIMEOUT,
    IPC_POLL_INTERVAL_MS,
    IPC_RECORDING_IDLE_POLL_INTERVAL_MS,
    IPC_RECORDING_STALE_POLLS_AFTER_STOP,
    OnboardingWizardWindows,
)
from utils.onboarding import OnboardingChoice, OnboardingStep, next_step


class _FakeLabel:
    def __init__(self):
        self.text = ""
        self.style = ""
        self.visible = True

    def setText(self, text: str) -> None:
        self.text = text

    def setStyleSheet(self, style: str) -> None:
        self.style = style

    def setVisible(self, visible: bool) -> None:
        self.visible = visible


class _FakeField:
    def __init__(self, value: str = ""):
        self._value = value
        self.style = ""

    def text(self) -> str:
        return self._value

    def setText(self, value: str) -> None:
        self._value = value

    def setStyleSheet(self, style: str) -> None:
        self.style = style


class _FakeButton:
    def __init__(self):
        self.visible = True
        self.enabled = True

    def setVisible(self, visible: bool) -> None:
        self.visible = visible

    def setEnabled(self, enabled: bool) -> None:
        self.enabled = enabled


class _FakeTimer:
    def __init__(self, interval: int | None = None):
        self.started_with: list[int] = []
        self.intervals: list[int] = []
        self.stopped = False
        self.interval = interval

    def start(self, interval: int) -> None:
        self.interval = interval
        self.started_with.append(interval)

    def stop(self) -> None:
        self.stopped = True

    def setInterval(self, interval: int) -> None:
        self.interval = interval
        self.intervals.append(interval)


class _FakeStack:
    def __init__(self):
        self.widgets = []
        self.current_widget = None

    def addWidget(self, widget) -> None:
        self.widgets.append(widget)

    def setCurrentWidget(self, widget) -> None:
        self.current_widget = widget


class _FakePlainText:
    def __init__(self, value: str = ""):
        self.value = value
        self.clear_calls = 0
        self.set_plain_text_calls = 0
        self.insert_plain_text_calls: list[str] = []
        self.move_cursor_calls = 0

    def clear(self) -> None:
        self.value = ""
        self.clear_calls += 1

    def setPlainText(self, value: str) -> None:
        self.value = value
        self.set_plain_text_calls += 1

    def toPlainText(self) -> str:
        return self.value

    def insertPlainText(self, value: str) -> None:
        self.value += value
        self.insert_plain_text_calls.append(value)

    def moveCursor(self, *_args, **_kwargs) -> None:
        self.move_cursor_calls += 1


class _FakeSignal:
    def __init__(self):
        self.calls: list[tuple[str, str]] = []

    def emit(self, field: str, value: str) -> None:
        self.calls.append((field, value))


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


def test_refresh_test_hotkey_label_shows_toggle_and_hold(monkeypatch):
    import ui.onboarding_wizard_windows as wizard_mod

    wizard = OnboardingWizardWindows.__new__(OnboardingWizardWindows)
    wizard._test_hotkey_label = _FakeLabel()

    monkeypatch.setattr(
        wizard_mod,
        "get_env_setting",
        lambda key: {
            "PULSESCRIBE_TOGGLE_HOTKEY": "ctrl+alt+r",
            "PULSESCRIBE_HOLD_HOTKEY": "ctrl+alt+space",
        }.get(key),
    )

    wizard._refresh_test_hotkey_label()
    assert (
        wizard._test_hotkey_label.text
        == "Toggle: ctrl+alt+r | Hold: ctrl+alt+space"
    )


def test_refresh_test_hotkey_label_handles_missing_hotkeys(monkeypatch):
    import ui.onboarding_wizard_windows as wizard_mod

    wizard = OnboardingWizardWindows.__new__(OnboardingWizardWindows)
    wizard._test_hotkey_label = _FakeLabel()

    monkeypatch.setattr(wizard_mod, "get_env_setting", lambda _key: None)

    wizard._refresh_test_hotkey_label()
    assert wizard._test_hotkey_label.text == "Kein Hotkey konfiguriert"


def test_update_test_transcript_appends_growth_and_skips_duplicates():
    wizard = OnboardingWizardWindows.__new__(OnboardingWizardWindows)
    wizard._test_transcript = _FakePlainText()
    wizard._test_status_label = _FakeLabel()
    wizard._test_successful = False
    wizard._update_navigation_calls = 0
    wizard._update_navigation = lambda: setattr(
        wizard, "_update_navigation_calls", wizard._update_navigation_calls + 1
    )

    wizard.update_test_transcript("Hallo")
    wizard.update_test_transcript("Hallo Welt")
    wizard.update_test_transcript("Hallo Welt")

    assert wizard._test_transcript.value == "Hallo Welt"
    assert wizard._test_transcript.set_plain_text_calls == 1
    assert wizard._test_transcript.insert_plain_text_calls == [" Welt"]
    assert wizard._test_transcript.move_cursor_calls == 1
    assert wizard._test_status_label.text == "Transkription erfolgreich!"
    assert wizard._update_navigation_calls == 2


def test_update_hotkey_field_from_pressed_keys_skips_duplicate_signal():
    wizard = OnboardingWizardWindows.__new__(OnboardingWizardWindows)
    wizard._recording_field = "toggle"
    wizard._pressed_keys = {"ctrl", "alt", "r"}
    wizard._pressed_keys_lock = threading.Lock()
    wizard._hotkey_field_update = _FakeSignal()

    wizard._update_hotkey_field_from_pressed_keys()
    wizard._update_hotkey_field_from_pressed_keys()

    assert wizard._hotkey_field_update.calls == [("toggle", "ctrl+alt+r")]


def test_go_next_fast_reapplies_choice_preset_after_api_key_entry(monkeypatch):
    import ui.onboarding_wizard_windows as wizard_mod

    wizard = OnboardingWizardWindows.__new__(OnboardingWizardWindows)
    wizard._step = OnboardingStep.CHOOSE_GOAL
    wizard._choice = OnboardingChoice.FAST
    wizard._api_key_field = _FakeField("dg-test-key")
    wizard._stop_hotkey_recording = lambda: None

    applied_choices: list[OnboardingChoice] = []
    shown_steps: list[OnboardingStep] = []
    saved_choice: list[OnboardingChoice] = []
    emitted: list[bool] = []

    wizard.settings_changed = types.SimpleNamespace(emit=lambda: emitted.append(True))
    wizard._apply_choice_preset = lambda choice: applied_choices.append(choice) or True
    wizard._show_step = lambda step: shown_steps.append(step)
    wizard._complete = lambda: (_ for _ in ()).throw(
        AssertionError("should not complete on CHOOSE_GOAL")
    )

    monkeypatch.setattr(
        wizard_mod,
        "set_onboarding_choice",
        lambda choice: saved_choice.append(choice),
    )

    wizard._go_next()

    assert applied_choices == [OnboardingChoice.FAST]
    assert saved_choice == [OnboardingChoice.FAST]
    assert emitted == [True]
    assert shown_steps == [next_step(OnboardingStep.CHOOSE_GOAL)]


def test_validate_hotkey_pair_rejects_duplicates_and_modifier_only_toggle():
    wizard = OnboardingWizardWindows.__new__(OnboardingWizardWindows)

    _, _, duplicate_error = wizard._validate_hotkey_pair("ctrl+alt+r", "alt+ctrl+r")
    assert duplicate_error is not None
    assert "identisch" in duplicate_error

    _, _, modifier_error = wizard._validate_hotkey_pair("ctrl+alt", "")
    assert modifier_error is not None
    assert "nicht-modifier" in modifier_error.lower()


def test_validate_hotkey_pair_allows_modifier_only_hold():
    wizard = OnboardingWizardWindows.__new__(OnboardingWizardWindows)

    toggle, hold, error = wizard._validate_hotkey_pair("ctrl+alt+r", "ctrl+win")

    assert error is None
    assert toggle == "ctrl+alt+r"
    assert hold == "ctrl+win"


def test_validate_hotkey_pair_rejects_overlapping_hotkeys():
    wizard = OnboardingWizardWindows.__new__(OnboardingWizardWindows)

    _, _, overlap_error = wizard._validate_hotkey_pair(
        "ctrl+win+shift+f13", "ctrl+win+f13"
    )
    assert overlap_error is not None
    assert "überlappen" in overlap_error.lower()


def test_ensure_default_hotkeys_applies_recommended_pair_when_missing(monkeypatch):
    wizard = OnboardingWizardWindows.__new__(OnboardingWizardWindows)
    wizard._toggle_input = _FakeField("")
    wizard._hold_input = _FakeField("")
    wizard._hotkey_status_label = _FakeLabel()
    wizard._env_settings_cache = {}

    saved: list[tuple[str, str | None]] = []
    refreshed: list[bool] = []
    emitted: list[bool] = []

    wizard.settings_changed = types.SimpleNamespace(emit=lambda: emitted.append(True))
    wizard._refresh_test_hotkey_label = lambda: refreshed.append(True)
    wizard._persist_hotkeys = lambda toggle, hold: saved.append((toggle, hold)) or True

    wizard._ensure_default_hotkeys()

    assert wizard._toggle_input.text() == DEFAULT_WINDOWS_TOGGLE_HOTKEY
    assert wizard._hold_input.text() == DEFAULT_WINDOWS_HOLD_HOTKEY
    assert saved == [
        (DEFAULT_WINDOWS_TOGGLE_HOTKEY, DEFAULT_WINDOWS_HOLD_HOTKEY),
    ]
    assert refreshed == [True]
    assert emitted == [True]
    assert "Standard gesetzt" in wizard._hotkey_status_label.text


def test_ensure_default_hotkeys_keeps_existing_user_config():
    wizard = OnboardingWizardWindows.__new__(OnboardingWizardWindows)
    wizard._toggle_input = _FakeField("ctrl+shift+r")
    wizard._hold_input = _FakeField("")
    wizard._hotkey_status_label = _FakeLabel()
    wizard._env_settings_cache = {"PULSESCRIBE_TOGGLE_HOTKEY": "ctrl+shift+r"}
    wizard.settings_changed = types.SimpleNamespace(
        emit=lambda: (_ for _ in ()).throw(AssertionError("must not emit"))
    )
    wizard._refresh_test_hotkey_label = (
        lambda: (_ for _ in ()).throw(AssertionError("must not refresh"))
    )
    wizard._persist_hotkeys = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        AssertionError("must not save defaults")
    )

    wizard._ensure_default_hotkeys()

    assert wizard._toggle_input.text() == "ctrl+shift+r"
    assert wizard._hold_input.text() == ""


def test_on_api_key_input_changed_updates_status_and_navigation():
    wizard = OnboardingWizardWindows.__new__(OnboardingWizardWindows)
    wizard._api_key_status = _FakeLabel()
    wizard._update_navigation_called = 0
    wizard._update_navigation = lambda: setattr(
        wizard, "_update_navigation_called", wizard._update_navigation_called + 1
    )

    wizard._on_api_key_input_changed("dg-new")
    assert "API-Key erkannt" in wizard._api_key_status.text

    wizard._on_api_key_input_changed(" ")
    assert "Erforderlich" in wizard._api_key_status.text
    assert wizard._update_navigation_called == 2


def test_on_language_changed_skips_noop_persistence_and_emit(monkeypatch):
    import ui.onboarding_wizard_windows as wizard_mod

    wizard = OnboardingWizardWindows.__new__(OnboardingWizardWindows)
    wizard._env_settings_cache = {"PULSESCRIBE_LANGUAGE": "de"}

    emitted: list[bool] = []
    persisted: list[dict[str, str | None]] = []
    wizard.settings_changed = types.SimpleNamespace(emit=lambda: emitted.append(True))

    monkeypatch.setattr(
        wizard_mod,
        "update_env_settings",
        lambda updates: persisted.append(dict(updates)),
    )

    wizard._on_language_changed("de")

    assert persisted == []
    assert emitted == []


def test_can_advance_fast_with_existing_groq_api_key(monkeypatch):
    import ui.onboarding_wizard_windows as wizard_mod

    wizard = OnboardingWizardWindows.__new__(OnboardingWizardWindows)
    wizard._step = OnboardingStep.CHOOSE_GOAL
    wizard._choice = OnboardingChoice.FAST
    wizard._api_key_field = _FakeField("")

    monkeypatch.setattr(
        wizard_mod,
        "get_api_key",
        lambda key: "grq-test-key" if key == "GROQ_API_KEY" else None,
    )

    assert wizard._can_advance() is True


def test_apply_choice_preset_fast_skips_duplicate_api_key_and_mode_writes(monkeypatch):
    import ui.onboarding_wizard_windows as wizard_mod

    wizard = OnboardingWizardWindows.__new__(OnboardingWizardWindows)
    wizard._choice = OnboardingChoice.FAST
    wizard._api_key_field = _FakeField("dg-same")
    wizard._api_key_container = _FakeLabel()
    wizard._api_key_status = _FakeLabel()
    wizard._env_settings_cache = {
        "DEEPGRAM_API_KEY": "dg-same",
        "PULSESCRIBE_MODE": "deepgram",
    }

    save_calls: list[tuple[str, str]] = []
    env_updates: list[dict[str, str | None]] = []

    monkeypatch.setattr(
        wizard_mod,
        "save_api_key",
        lambda key, value: save_calls.append((key, value)),
    )
    monkeypatch.setattr(
        wizard_mod,
        "update_env_settings",
        lambda updates: env_updates.append(dict(updates)),
    )

    changed = wizard._apply_choice_preset(OnboardingChoice.FAST)

    assert changed is False
    assert save_calls == []
    assert env_updates == []
    assert wizard._api_key_container.visible is False


def test_select_choice_fast_hides_api_input_when_groq_key_exists(monkeypatch):
    import ui.onboarding_wizard_windows as wizard_mod

    wizard = OnboardingWizardWindows.__new__(OnboardingWizardWindows)
    wizard._choice_buttons = {}
    wizard._api_key_container = _FakeLabel()
    wizard._api_key_status = _FakeLabel()
    wizard._api_key_field = _FakeField("")
    wizard._update_navigation = lambda: None

    monkeypatch.setattr(
        wizard_mod,
        "get_api_key",
        lambda key: "grq-test-key" if key == "GROQ_API_KEY" else None,
    )

    wizard._select_choice(OnboardingChoice.FAST, save=False)

    assert wizard._api_key_container.visible is False


def test_start_hotkey_recording_uses_qt_fallback_when_pynput_missing(monkeypatch):
    import ui.onboarding_wizard_windows as wizard_mod

    wizard = OnboardingWizardWindows.__new__(OnboardingWizardWindows)
    wizard._toggle_input = _FakeField("ctrl+alt+r")
    wizard._hold_input = _FakeField("")
    wizard._recording_field = None
    wizard._hotkey_recorded = False
    wizard._using_qt_grab = False
    wizard._pressed_keys = set()
    wizard._pressed_keys_lock = threading.Lock()
    wizard._hotkey_status_label = _FakeLabel()
    wizard._hotkey_listener = None

    grabbed = []
    focused = []
    wizard.grabKeyboard = lambda: grabbed.append(True)
    wizard.setFocus = lambda: focused.append(True)
    wizard.releaseKeyboard = lambda: None

    monkeypatch.setattr(wizard_mod, "get_pynput_key_map", lambda: (False, {}))
    monkeypatch.setattr(wizard_mod, "get_env_setting", lambda _key: "ctrl+alt+r")

    wizard._start_hotkey_recording("toggle")

    assert wizard._recording_field == "toggle"
    assert wizard._using_qt_grab is True
    assert grabbed == [True]
    assert focused == [True]
    assert "Qt-Fallback" in wizard._hotkey_status_label.text


def test_start_hotkey_recording_falls_back_when_listener_start_fails(monkeypatch):
    import ui.onboarding_wizard_windows as wizard_mod

    wizard = OnboardingWizardWindows.__new__(OnboardingWizardWindows)
    wizard._toggle_input = _FakeField("ctrl+alt+r")
    wizard._hold_input = _FakeField("")
    wizard._recording_field = None
    wizard._hotkey_recorded = False
    wizard._using_qt_grab = False
    wizard._pressed_keys = set()
    wizard._pressed_keys_lock = threading.Lock()
    wizard._hotkey_status_label = _FakeLabel()
    wizard._hotkey_listener = None

    grabbed = []
    focused = []
    wizard.grabKeyboard = lambda: grabbed.append(True)
    wizard.setFocus = lambda: focused.append(True)
    wizard.releaseKeyboard = lambda: None

    class _FailingListener:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("hook unavailable")

    fake_keyboard = types.SimpleNamespace(Listener=_FailingListener)
    monkeypatch.setattr(wizard_mod, "get_pynput_key_map", lambda: (True, {}))
    monkeypatch.setitem(
        sys.modules,
        "pynput",
        types.SimpleNamespace(keyboard=fake_keyboard),
    )
    monkeypatch.setattr(wizard_mod, "get_env_setting", lambda _key: "ctrl+alt+r")

    wizard._start_hotkey_recording("toggle")

    assert wizard._recording_field == "toggle"
    assert wizard._using_qt_grab is True
    assert grabbed == [True]
    assert focused == [True]
    assert "Listener fehlgeschlagen" in wizard._hotkey_status_label.text
    assert "Qt-Fallback" in wizard._hotkey_status_label.text


def test_keypress_qt_fallback_updates_hotkey_field():
    wizard = OnboardingWizardWindows.__new__(OnboardingWizardWindows)
    wizard._recording_field = "toggle"
    wizard._toggle_input = _FakeField("")
    wizard._hold_input = _FakeField("")
    wizard._is_closed = False
    wizard._using_qt_grab = True
    wizard._hotkey_recorded = False
    wizard._stop_hotkey_recording = lambda save=False: None

    event = _FakeKeyEvent(
        Qt.Key.Key_R,
        Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier,
    )

    wizard.keyPressEvent(event)

    assert wizard._toggle_input.text() == "ctrl+alt+r"
    assert wizard._hotkey_recorded is True
    assert event.accepted is True


def test_set_hotkey_field_text_noop_when_wizard_is_closed():
    wizard = OnboardingWizardWindows.__new__(OnboardingWizardWindows)
    wizard._recording_field = "toggle"
    wizard._toggle_input = _FakeField("ctrl+alt+r")
    wizard._hold_input = _FakeField("")
    wizard._is_closed = True

    wizard._set_hotkey_field_text("toggle", "ctrl+shift+r")

    assert wizard._toggle_input.text() == "ctrl+alt+r"


def test_set_hotkey_field_text_ignores_stale_field_updates():
    wizard = OnboardingWizardWindows.__new__(OnboardingWizardWindows)
    wizard._recording_field = "toggle"
    wizard._toggle_input = _FakeField("ctrl+alt+r")
    wizard._hold_input = _FakeField("ctrl+win")
    wizard._is_closed = False

    wizard._set_hotkey_field_text("hold", "ctrl+alt+space")

    assert wizard._toggle_input.text() == "ctrl+alt+r"
    assert wizard._hold_input.text() == "ctrl+win"


def test_keypress_qt_fallback_ignores_auto_repeat_events():
    wizard = OnboardingWizardWindows.__new__(OnboardingWizardWindows)
    wizard._recording_field = "toggle"
    wizard._toggle_input = _FakeField("ctrl+alt+r")
    wizard._hold_input = _FakeField("")
    wizard._is_closed = False
    wizard._using_qt_grab = True
    wizard._hotkey_recorded = False
    wizard._stop_hotkey_recording = lambda save=False: None

    event = _FakeKeyEvent(
        Qt.Key.Key_R,
        Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier,
        auto_repeat=True,
    )

    wizard.keyPressEvent(event)

    assert wizard._toggle_input.text() == "ctrl+alt+r"
    assert wizard._hotkey_recorded is False
    assert event.accepted is True


def test_stop_hotkey_recording_releases_qt_grab(monkeypatch):
    import ui.onboarding_wizard_windows as wizard_mod

    wizard = OnboardingWizardWindows.__new__(OnboardingWizardWindows)
    wizard._recording_field = "toggle"
    wizard._toggle_input = _FakeField("ctrl+alt+r")
    wizard._hold_input = _FakeField("")
    wizard._hotkey_recorded = False
    wizard._hotkey_listener = None
    wizard._using_qt_grab = True
    wizard._pressed_keys = {"ctrl"}
    wizard._pressed_keys_lock = threading.Lock()

    released = []
    wizard.releaseKeyboard = lambda: released.append(True)
    wizard._set_hotkey_status = lambda *_args, **_kwargs: None

    monkeypatch.setattr(wizard_mod, "get_env_setting", lambda _key: "ctrl+alt+r")

    wizard._stop_hotkey_recording(save=False)

    assert released == [True]
    assert wizard._using_qt_grab is False


def test_clear_hotkey_stops_active_recording_before_clearing(monkeypatch):
    wizard = OnboardingWizardWindows.__new__(OnboardingWizardWindows)
    wizard._recording_field = "toggle"
    wizard._toggle_input = _FakeField("ctrl+alt+r")
    wizard._hold_input = _FakeField("ctrl+win")
    wizard._hotkey_status_label = _FakeLabel()
    wizard._env_settings_cache = {
        "PULSESCRIBE_TOGGLE_HOTKEY": "ctrl+alt+r",
        "PULSESCRIBE_HOLD_HOTKEY": "ctrl+win",
    }

    stop_calls: list[bool] = []
    emitted: list[bool] = []
    refreshed: list[bool] = []
    updated: list[bool] = []
    persisted: list[tuple[str | None, str | None]] = []

    wizard._stop_hotkey_recording = lambda save=False: stop_calls.append(save)
    wizard.settings_changed = types.SimpleNamespace(emit=lambda: emitted.append(True))
    wizard._refresh_test_hotkey_label = lambda: refreshed.append(True)
    wizard._update_navigation = lambda: updated.append(True)
    wizard._persist_hotkeys = lambda toggle, hold: persisted.append((toggle, hold)) or True

    wizard._clear_hotkey("toggle")

    assert stop_calls == [False]
    assert wizard._toggle_input.text() == ""
    assert wizard._hold_input.text() == "ctrl+win"
    assert persisted == [(None, "ctrl+win")]
    assert emitted == [True]
    assert refreshed == [True]
    assert updated == [True]


def test_clear_hotkey_updates_status_feedback():
    wizard = OnboardingWizardWindows.__new__(OnboardingWizardWindows)
    wizard._recording_field = None
    wizard._toggle_input = _FakeField("ctrl+alt+r")
    wizard._hold_input = _FakeField("")
    wizard._hotkey_status_label = _FakeLabel()
    wizard._env_settings_cache = {"PULSESCRIBE_TOGGLE_HOTKEY": "ctrl+alt+r"}
    wizard.settings_changed = types.SimpleNamespace(emit=lambda: None)
    wizard._refresh_test_hotkey_label = lambda: None
    wizard._update_navigation = lambda: None
    wizard._persist_hotkeys = lambda *_args, **_kwargs: True

    wizard._clear_hotkey("toggle")

    assert "Hotkey entfernt" in wizard._hotkey_status_label.text


def test_apply_hotkey_preset_stops_active_recording_before_applying():
    wizard = OnboardingWizardWindows.__new__(OnboardingWizardWindows)
    wizard._recording_field = "toggle"
    wizard._toggle_input = _FakeField("ctrl+alt+r")
    wizard._hold_input = _FakeField("ctrl+win")
    wizard._hotkey_status_label = _FakeLabel()

    stop_calls: list[bool] = []
    emitted: list[bool] = []
    refreshed: list[bool] = []
    updated: list[bool] = []
    persisted: list[tuple[str | None, str | None]] = []

    wizard._stop_hotkey_recording = lambda save=False: stop_calls.append(save)
    wizard.settings_changed = types.SimpleNamespace(emit=lambda: emitted.append(True))
    wizard._refresh_test_hotkey_label = lambda: refreshed.append(True)
    wizard._update_navigation = lambda: updated.append(True)
    wizard._persist_hotkeys = lambda toggle, hold: persisted.append((toggle, hold)) or True

    wizard._apply_hotkey_preset("f19", None)

    assert stop_calls == [False]
    assert wizard._toggle_input.text() == "f19"
    assert wizard._hold_input.text() == ""
    assert persisted == [("f19", "")]
    assert emitted == [True]
    assert refreshed == [True]
    assert updated == [True]
    assert "Preset angewendet" in wizard._hotkey_status_label.text


def test_start_ipc_test_ignores_duplicate_start():
    wizard = OnboardingWizardWindows.__new__(OnboardingWizardWindows)
    wizard._ipc_test_cmd_id = "running"
    wizard._ipc_client = object()
    wizard._test_status_label = _FakeLabel()
    wizard._test_start_btn = _FakeButton()
    wizard._test_stop_btn = _FakeButton()
    wizard._test_notice = _FakeLabel()
    wizard._set_test_status = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        AssertionError("duplicate start should return early")
    )

    wizard._start_ipc_test()


def test_start_ipc_test_disables_stop_button_until_recording_ack():
    commands: list[str] = []

    class _FakeIPCClient:
        def send_command(self, command: str) -> str:
            commands.append(command)
            return "cmd-1"

    wizard = OnboardingWizardWindows.__new__(OnboardingWizardWindows)
    wizard._ipc_test_cmd_id = None
    wizard._ipc_client = _FakeIPCClient()
    wizard._ipc_poll_timer = _FakeTimer()
    wizard._ipc_seen_recording = False
    wizard._ipc_stop_requested = False
    wizard._ipc_recording_polls_after_stop = 0
    wizard._ipc_last_status = None
    wizard._test_status_label = _FakeLabel()
    wizard._test_start_btn = _FakeButton()
    wizard._test_stop_btn = _FakeButton()
    wizard._test_notice = _FakeLabel()
    wizard._set_test_status = lambda *_args, **_kwargs: None
    wizard._update_navigation = lambda: None

    wizard._start_ipc_test()

    assert commands == ["start_test"]
    assert wizard._test_stop_btn.visible is True
    assert wizard._test_stop_btn.enabled is False
    assert wizard._ipc_poll_timer.started_with == [IPC_POLL_INTERVAL_MS]


def test_start_ipc_test_clears_previous_transcript():
    commands: list[str] = []

    class _FakeIPCClient:
        def send_command(self, command: str) -> str:
            commands.append(command)
            return "cmd-1"

    transcript = _FakePlainText("stale text")

    wizard = OnboardingWizardWindows.__new__(OnboardingWizardWindows)
    wizard._ipc_test_cmd_id = None
    wizard._ipc_client = _FakeIPCClient()
    wizard._ipc_poll_timer = _FakeTimer()
    wizard._ipc_seen_recording = False
    wizard._ipc_stop_requested = False
    wizard._ipc_recording_polls_after_stop = 0
    wizard._ipc_last_status = None
    wizard._test_status_label = _FakeLabel()
    wizard._test_start_btn = _FakeButton()
    wizard._test_stop_btn = _FakeButton()
    wizard._test_notice = _FakeLabel()
    wizard._test_transcript = transcript
    wizard._set_test_status = lambda *_args, **_kwargs: None
    wizard._update_navigation = lambda: None

    wizard._start_ipc_test()

    assert commands == ["start_test"]
    assert transcript.value == ""
    assert transcript.clear_calls == 1


def test_start_ipc_test_resets_previous_success_state():
    commands: list[str] = []

    class _FakeIPCClient:
        def send_command(self, command: str) -> str:
            commands.append(command)
            return "cmd-1"

    wizard = OnboardingWizardWindows.__new__(OnboardingWizardWindows)
    wizard._ipc_test_cmd_id = None
    wizard._ipc_client = _FakeIPCClient()
    wizard._ipc_poll_timer = types.SimpleNamespace(
        start=lambda _ms: None,
        stop=lambda: None,
    )
    wizard._ipc_seen_recording = False
    wizard._ipc_stop_requested = False
    wizard._ipc_recording_polls_after_stop = 0
    wizard._ipc_last_status = None
    wizard._test_status_label = _FakeLabel()
    wizard._test_start_btn = _FakeButton()
    wizard._test_stop_btn = _FakeButton()
    wizard._test_notice = _FakeLabel()
    wizard._test_transcript = _FakePlainText("old success")
    wizard._test_successful = True
    wizard._set_test_status = lambda *_args, **_kwargs: None

    navigation_updates: list[bool] = []
    wizard._update_navigation = lambda: navigation_updates.append(
        wizard._test_successful
    )

    wizard._start_ipc_test()

    assert commands == ["start_test"]
    assert wizard._test_successful is False
    assert navigation_updates == [False]


def test_can_advance_requires_successful_test_on_test_step():
    wizard = OnboardingWizardWindows.__new__(OnboardingWizardWindows)
    wizard._step = OnboardingStep.TEST_DICTATION
    wizard._test_successful = False

    assert wizard._can_advance() is False

    wizard._test_successful = True

    assert wizard._can_advance() is True


def test_cancel_ipc_test_if_running_sends_stop_command():
    commands: list[str] = []

    wizard = OnboardingWizardWindows.__new__(OnboardingWizardWindows)
    wizard._ipc_client = types.SimpleNamespace(
        send_command=lambda cmd: commands.append(cmd)
    )
    wizard._ipc_test_cmd_id = "cmd-1"

    wizard._cancel_ipc_test_if_running()

    assert commands == ["stop_test"]


def test_show_step_leaving_test_requests_ipc_cancel():
    wizard = OnboardingWizardWindows.__new__(OnboardingWizardWindows)
    wizard._step = OnboardingStep.TEST_DICTATION
    wizard._mic_timer = None
    wizard._persist_progress = False
    wizard._progress_label = None
    wizard._stack = _FakeStack()
    wizard._step_widgets = {}
    wizard._step_builders = {}
    wizard._update_navigation = lambda: None

    cancel_calls: list[bool] = []
    stop_calls: list[bool] = []
    reset_calls: list[bool] = []
    wizard._cancel_ipc_test_if_running = lambda: cancel_calls.append(True)
    wizard._stop_ipc_polling = lambda: stop_calls.append(True)
    wizard._reset_test_ui = lambda: reset_calls.append(True)

    wizard._show_step(OnboardingStep.HOTKEY)

    assert cancel_calls == [True]
    assert stop_calls == [True]
    assert reset_calls == [True]


def test_show_step_hotkey_applies_missing_defaults():
    wizard = OnboardingWizardWindows.__new__(OnboardingWizardWindows)
    wizard._step = OnboardingStep.CHOOSE_GOAL
    wizard._mic_timer = None
    wizard._persist_progress = False
    wizard._progress_label = None
    wizard._stack = _FakeStack()
    wizard._step_widgets = {}
    wizard._update_navigation = lambda: None

    ensure_calls: list[bool] = []
    wizard._ensure_default_hotkeys = lambda: ensure_calls.append(
        isinstance(wizard._toggle_input, _FakeField)
    )
    wizard._step_builders = {
        OnboardingStep.HOTKEY: lambda: setattr(
            wizard, "_toggle_input", _FakeField("")
        )
        or object()
    }

    wizard._show_step(OnboardingStep.HOTKEY)

    assert ensure_calls == [True]
    assert wizard._stack.current_widget is not None


def test_ensure_step_widget_builds_once():
    wizard = OnboardingWizardWindows.__new__(OnboardingWizardWindows)
    wizard._stack = _FakeStack()
    wizard._step_widgets = {}

    built: list[str] = []
    hotkey_widget = object()
    wizard._step_builders = {
        OnboardingStep.HOTKEY: lambda: built.append("hotkey") or hotkey_widget
    }

    assert wizard._ensure_step_widget(OnboardingStep.HOTKEY) is hotkey_widget
    assert wizard._ensure_step_widget(OnboardingStep.HOTKEY) is hotkey_widget
    assert wizard._is_step_widget_built(OnboardingStep.HOTKEY) is True
    assert built == ["hotkey"]
    assert wizard._stack.widgets == [hotkey_widget]


def test_stop_ipc_test_requires_recording_ack_before_sending_command():
    commands: list[str] = []
    statuses: list[tuple[str, str]] = []

    wizard = OnboardingWizardWindows.__new__(OnboardingWizardWindows)
    wizard._ipc_client = types.SimpleNamespace(
        send_command=lambda cmd: commands.append(cmd)
    )
    wizard._ipc_test_cmd_id = "cmd-1"
    wizard._ipc_seen_recording = False
    wizard._ipc_stop_requested = False
    wizard._ipc_recording_polls_after_stop = 0
    wizard._test_status_label = _FakeLabel()
    wizard._test_stop_btn = _FakeButton()
    wizard._set_test_status = lambda text, color: statuses.append((text, color))

    wizard._stop_ipc_test()

    assert commands == []
    assert statuses == [("Warte auf Aufnahme-Start...", "text_secondary")]
    assert wizard._ipc_stop_requested is False
    assert wizard._test_stop_btn.enabled is True


def test_stop_ipc_test_sends_stop_command_after_recording_ack():
    commands: list[str] = []

    wizard = OnboardingWizardWindows.__new__(OnboardingWizardWindows)
    wizard._ipc_client = types.SimpleNamespace(
        send_command=lambda cmd: commands.append(cmd)
    )
    wizard._ipc_test_cmd_id = "cmd-1"
    wizard._ipc_seen_recording = True
    wizard._ipc_stop_requested = False
    wizard._ipc_recording_polls_after_stop = 9
    wizard._ipc_poll_timer = _FakeTimer(interval=IPC_RECORDING_IDLE_POLL_INTERVAL_MS)
    wizard._test_status_label = _FakeLabel()
    wizard._test_stop_btn = _FakeButton()

    wizard._stop_ipc_test()

    assert commands == ["stop_test"]
    assert wizard._ipc_stop_requested is True
    assert wizard._ipc_recording_polls_after_stop == 0
    assert wizard._ipc_poll_timer.intervals == [IPC_POLL_INTERVAL_MS]
    assert wizard._test_status_label.text == "Wird gestoppt..."
    assert wizard._test_stop_btn.enabled is False


def test_poll_ipc_response_timeout_after_stop_maps_to_no_speech():
    wizard = OnboardingWizardWindows.__new__(OnboardingWizardWindows)
    wizard._ipc_test_cmd_id = "cmd-1"
    wizard._ipc_client = types.SimpleNamespace(
        poll_response=lambda _cmd_id: None,
        clear_response=lambda: None,
    )
    wizard._ipc_poll_timer = types.SimpleNamespace(stop=lambda: None)
    wizard._ipc_poll_count = IPC_MAX_POLLS_BEFORE_TIMEOUT - 1
    wizard._ipc_seen_recording = True
    wizard._ipc_stop_requested = True
    wizard._ipc_recording_polls_after_stop = 0
    wizard._ipc_last_status = "recording"

    results: list[tuple[str, str | None]] = []
    wizard._on_ipc_test_complete = lambda transcript, error: results.append(
        (transcript, error)
    )

    wizard._poll_ipc_response()

    assert results == [("", None)]
    assert wizard._ipc_test_cmd_id is None
    assert wizard._ipc_stop_requested is False


def test_poll_ipc_response_timeout_before_recording_fails_fast_with_connection_error():
    wizard = OnboardingWizardWindows.__new__(OnboardingWizardWindows)
    wizard._ipc_test_cmd_id = "cmd-1"
    wizard._ipc_client = types.SimpleNamespace(
        poll_response=lambda _cmd_id: None,
        clear_response=lambda: None,
    )
    wizard._ipc_poll_timer = types.SimpleNamespace(stop=lambda: None)
    wizard._ipc_poll_count = IPC_CONNECT_MAX_POLLS_BEFORE_TIMEOUT - 1
    wizard._ipc_seen_recording = False
    wizard._ipc_stop_requested = False
    wizard._ipc_recording_polls_after_stop = 0
    wizard._ipc_last_status = None

    results: list[tuple[str, str | None]] = []
    wizard._on_ipc_test_complete = lambda transcript, error: results.append(
        (transcript, error)
    )

    wizard._poll_ipc_response()

    assert results == [("", "Keine Verbindung zu PulseScribe")]
    assert wizard._ipc_test_cmd_id is None


def test_poll_ipc_response_after_recording_uses_longer_result_timeout():
    wizard = OnboardingWizardWindows.__new__(OnboardingWizardWindows)
    wizard._ipc_test_cmd_id = "cmd-1"
    wizard._ipc_client = types.SimpleNamespace(
        poll_response=lambda _cmd_id: None,
        clear_response=lambda: None,
    )
    wizard._ipc_poll_timer = types.SimpleNamespace(stop=lambda: None)
    wizard._ipc_poll_count = IPC_CONNECT_MAX_POLLS_BEFORE_TIMEOUT - 1
    wizard._ipc_seen_recording = True
    wizard._ipc_stop_requested = False
    wizard._ipc_recording_polls_after_stop = 0
    wizard._ipc_last_status = "recording"
    wizard._on_ipc_test_complete = (
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("should not timeout before result-timeout budget is reached")
        )
    )

    wizard._poll_ipc_response()

    assert wizard._ipc_poll_count == IPC_CONNECT_MAX_POLLS_BEFORE_TIMEOUT
    assert wizard._ipc_test_cmd_id == "cmd-1"


def test_poll_ipc_response_stale_recording_after_stop_completes():
    wizard = OnboardingWizardWindows.__new__(OnboardingWizardWindows)
    wizard._ipc_test_cmd_id = "cmd-1"
    wizard._ipc_client = types.SimpleNamespace(
        poll_response=lambda _cmd_id: {"status": "recording"},
        clear_response=lambda: None,
    )
    wizard._ipc_poll_timer = types.SimpleNamespace(stop=lambda: None)
    wizard._ipc_poll_count = 0
    wizard._ipc_seen_recording = False
    wizard._ipc_stop_requested = True
    wizard._ipc_recording_polls_after_stop = (
        IPC_RECORDING_STALE_POLLS_AFTER_STOP - 1
    )
    wizard._ipc_last_status = "recording"
    wizard._set_test_status = lambda *_args, **_kwargs: None

    results: list[tuple[str, str | None]] = []
    wizard._on_ipc_test_complete = lambda transcript, error: results.append(
        (transcript, error)
    )

    wizard._poll_ipc_response()

    assert results == [("", None)]
    assert wizard._ipc_test_cmd_id is None
    assert wizard._ipc_seen_recording is False


def test_poll_ipc_response_repeated_recording_status_avoids_repainting():
    wizard = OnboardingWizardWindows.__new__(OnboardingWizardWindows)
    wizard._ipc_test_cmd_id = "cmd-1"
    wizard._ipc_client = types.SimpleNamespace(
        poll_response=lambda _cmd_id: {"status": "recording"},
        clear_response=lambda: None,
    )
    wizard._ipc_poll_timer = _FakeTimer(interval=IPC_POLL_INTERVAL_MS)
    wizard._ipc_poll_count = 17
    wizard._ipc_seen_recording = False
    wizard._ipc_stop_requested = False
    wizard._ipc_recording_polls_after_stop = 0
    wizard._ipc_last_status = "recording"
    wizard._test_stop_btn = _FakeButton()
    wizard._test_stop_btn.enabled = False

    status_updates: list[tuple[str, str]] = []
    wizard._set_test_status = lambda text, color: status_updates.append((text, color))
    wizard._on_ipc_test_complete = (
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("should not complete on regular recording status")
        )
    )

    wizard._poll_ipc_response()

    assert status_updates == []
    assert wizard._ipc_poll_count == 0
    assert wizard._ipc_seen_recording is True
    assert wizard._ipc_poll_timer.intervals == [IPC_RECORDING_IDLE_POLL_INTERVAL_MS]
    assert wizard._test_stop_btn.enabled is True


def test_poll_ipc_response_noop_after_recording_uses_slower_idle_polling():
    wizard = OnboardingWizardWindows.__new__(OnboardingWizardWindows)
    wizard._ipc_test_cmd_id = "cmd-1"
    wizard._ipc_client = types.SimpleNamespace(
        poll_response=lambda _cmd_id: None,
        clear_response=lambda: None,
    )
    wizard._ipc_poll_timer = _FakeTimer(interval=IPC_POLL_INTERVAL_MS)
    wizard._ipc_poll_count = 0
    wizard._ipc_seen_recording = True
    wizard._ipc_stop_requested = False
    wizard._ipc_recording_polls_after_stop = 0
    wizard._ipc_last_status = "recording"
    wizard._on_ipc_test_complete = (
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("should not complete while idle polling budget remains")
        )
    )

    wizard._poll_ipc_response()

    assert wizard._ipc_poll_count == 1
    assert wizard._ipc_poll_timer.intervals == [IPC_RECORDING_IDLE_POLL_INTERVAL_MS]


def test_poll_ipc_response_stopped_resets_ui_and_sets_status():
    wizard = OnboardingWizardWindows.__new__(OnboardingWizardWindows)
    wizard._ipc_test_cmd_id = "cmd-1"
    wizard._ipc_client = types.SimpleNamespace(
        poll_response=lambda _cmd_id: {"status": "stopped"},
        clear_response=lambda: None,
    )
    wizard._ipc_poll_timer = types.SimpleNamespace(stop=lambda: None)
    wizard._ipc_poll_count = 0
    wizard._ipc_seen_recording = True
    wizard._ipc_stop_requested = True
    wizard._ipc_recording_polls_after_stop = 0
    wizard._ipc_last_status = "recording"

    reset_calls: list[bool] = []
    status_updates: list[tuple[str, str]] = []
    wizard._reset_test_ui = lambda: reset_calls.append(True)
    wizard._set_test_status = lambda text, color: status_updates.append((text, color))

    wizard._poll_ipc_response()

    assert reset_calls == [True]
    assert status_updates == [("Aufnahme gestoppt.", "text_secondary")]
    assert wizard._ipc_test_cmd_id is None


def test_reset_test_ui_hides_notice():
    wizard = OnboardingWizardWindows.__new__(OnboardingWizardWindows)
    wizard._test_start_btn = _FakeButton()
    wizard._test_stop_btn = _FakeButton()
    wizard._test_notice = _FakeLabel()
    wizard._test_notice.visible = True

    wizard._reset_test_ui()

    assert wizard._test_start_btn.visible is True
    assert wizard._test_stop_btn.visible is False
    assert wizard._test_notice.visible is False
