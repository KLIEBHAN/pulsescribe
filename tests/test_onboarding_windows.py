import pytest
import threading

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt

from ui.onboarding_wizard_windows import OnboardingWizardWindows
from utils.onboarding import OnboardingChoice, OnboardingStep, next_step


class _FakeLabel:
    def __init__(self):
        self.text = ""
        self.style = ""

    def setText(self, text: str) -> None:
        self.text = text

    def setStyleSheet(self, style: str) -> None:
        self.style = style


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
    def __init__(self, key: int, modifiers: Qt.KeyboardModifier):
        self._key = key
        self._modifiers = modifiers
        self.accepted = False

    def key(self) -> int:
        return self._key

    def modifiers(self) -> Qt.KeyboardModifier:
        return self._modifiers

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


def test_keypress_qt_fallback_updates_hotkey_field():
    wizard = OnboardingWizardWindows.__new__(OnboardingWizardWindows)
    wizard._recording_field = "toggle"
    wizard._toggle_input = _FakeField("")
    wizard._hold_input = _FakeField("")
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
