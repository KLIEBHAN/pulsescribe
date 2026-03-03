import pytest

pytest.importorskip("PySide6")

from ui.onboarding_wizard_windows import OnboardingWizardWindows


class _FakeLabel:
    def __init__(self):
        self.text = ""

    def setText(self, text: str) -> None:
        self.text = text


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
