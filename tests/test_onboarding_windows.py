import pytest
import threading
import types
import sys

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt

from ui.onboarding_wizard_windows import (
    IPC_MAX_POLLS_BEFORE_TIMEOUT,
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


def test_go_next_fast_reapplies_choice_preset_after_api_key_entry(monkeypatch):
    import ui.onboarding_wizard_windows as wizard_mod

    wizard = OnboardingWizardWindows.__new__(OnboardingWizardWindows)
    wizard._step = OnboardingStep.CHOOSE_GOAL
    wizard._choice = OnboardingChoice.FAST
    wizard._api_key_field = _FakeField("dg-test-key")
    wizard._stop_hotkey_recording = lambda: None

    applied_choices: list[OnboardingChoice] = []
    shown_steps: list[OnboardingStep] = []
    saved_keys: list[tuple[str, str]] = []
    saved_choice: list[OnboardingChoice] = []

    wizard._apply_choice_preset = lambda choice: applied_choices.append(choice)
    wizard._show_step = lambda step: shown_steps.append(step)
    wizard._complete = lambda: (_ for _ in ()).throw(
        AssertionError("should not complete on CHOOSE_GOAL")
    )

    monkeypatch.setattr(
        wizard_mod,
        "save_api_key",
        lambda key, value: saved_keys.append((key, value)),
    )
    monkeypatch.setattr(
        wizard_mod,
        "set_onboarding_choice",
        lambda choice: saved_choice.append(choice),
    )

    wizard._go_next()

    assert saved_keys == [("DEEPGRAM_API_KEY", "dg-test-key")]
    assert applied_choices == [OnboardingChoice.FAST]
    assert saved_choice == [OnboardingChoice.FAST]
    assert shown_steps == [next_step(OnboardingStep.CHOOSE_GOAL)]


def test_validate_hotkey_pair_rejects_duplicates_and_modifier_only():
    wizard = OnboardingWizardWindows.__new__(OnboardingWizardWindows)

    _, _, duplicate_error = wizard._validate_hotkey_pair("ctrl+alt+r", "alt+ctrl+r")
    assert duplicate_error is not None
    assert "identisch" in duplicate_error

    _, _, modifier_error = wizard._validate_hotkey_pair("ctrl+alt", "")
    assert modifier_error is not None
    assert "nicht-modifier" in modifier_error.lower()


def test_validate_hotkey_pair_rejects_overlapping_hotkeys():
    wizard = OnboardingWizardWindows.__new__(OnboardingWizardWindows)

    _, _, overlap_error = wizard._validate_hotkey_pair(
        "ctrl+win+shift+f13", "ctrl+win+f13"
    )
    assert overlap_error is not None
    assert "überlappen" in overlap_error.lower()


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

    wizard._set_hotkey_field_text("ctrl+shift+r")

    assert wizard._toggle_input.text() == "ctrl+alt+r"


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
    wizard._set_test_status = lambda *_args, **_kwargs: None

    wizard._start_ipc_test()

    assert commands == ["start_test"]
    assert wizard._test_stop_btn.visible is True
    assert wizard._test_stop_btn.enabled is False


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
    wizard._stack = types.SimpleNamespace(setCurrentIndex=lambda _idx: None)
    wizard._update_navigation = lambda: None

    cancel_calls: list[bool] = []
    stop_calls: list[bool] = []
    wizard._cancel_ipc_test_if_running = lambda: cancel_calls.append(True)
    wizard._stop_ipc_polling = lambda: stop_calls.append(True)

    wizard._show_step(OnboardingStep.HOTKEY)

    assert cancel_calls == [True]
    assert stop_calls == [True]


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
    wizard._test_status_label = _FakeLabel()
    wizard._test_stop_btn = _FakeButton()

    wizard._stop_ipc_test()

    assert commands == ["stop_test"]
    assert wizard._ipc_stop_requested is True
    assert wizard._ipc_recording_polls_after_stop == 0
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
    wizard._ipc_poll_timer = types.SimpleNamespace(stop=lambda: None)
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
    assert wizard._test_stop_btn.enabled is True


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
