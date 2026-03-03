import pytest

pytest.importorskip("PySide6")

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

    def text(self) -> str:
        return self._value

    def setText(self, value: str) -> None:
        self._value = value


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
