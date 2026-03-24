from __future__ import annotations

import sys
from types import SimpleNamespace

import utils.permissions as permissions_mod
import utils.preferences as prefs
from ui.onboarding_wizard import OnboardingWizardController
from utils.onboarding import OnboardingChoice, OnboardingStep


def _isolate_prefs(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(prefs, "PREFS_FILE", tmp_path / "preferences.json")
    monkeypatch.setattr(prefs, "ENV_FILE", tmp_path / ".env")


class _FakeApiField:
    def __init__(self, value: str = "") -> None:
        self._value = value
        self.focus_requested = False

    def stringValue(self) -> str:
        return self._value

    def setStringValue_(self, value: str) -> None:
        self._value = value

    def becomeFirstResponder(self) -> bool:
        self.focus_requested = True
        return True


class _FakeStatus:
    def __init__(self) -> None:
        self.value = ""

    def setStringValue_(self, value: str) -> None:
        self.value = value


class _FakeContainer:
    def __init__(self) -> None:
        self.hidden: bool | None = None

    def setHidden_(self, hidden: bool) -> None:
        self.hidden = hidden


class _FakeLangPopup:
    def __init__(self, value: str = "auto") -> None:
        self._value = value

    def titleOfSelectedItem(self) -> str:
        return self._value


class _FakeWindow:
    def __init__(self) -> None:
        self.levels: list[int] = []
        self.first_responder = None

    def setLevel_(self, level: int) -> None:
        self.levels.append(level)

    def makeFirstResponder_(self, responder) -> bool:
        self.first_responder = responder
        return True


def _make_wizard(choice: OnboardingChoice, api_key: str = "") -> OnboardingWizardController:
    wizard = OnboardingWizardController.__new__(OnboardingWizardController)
    wizard._step = OnboardingStep.CHOOSE_GOAL
    wizard._choice = choice
    wizard._api_key_field = _FakeApiField(api_key)
    wizard._api_key_container = _FakeContainer()
    wizard._api_key_status = _FakeStatus()
    wizard._lang_popup = _FakeLangPopup("auto")
    wizard._window = None
    wizard._on_settings_changed = None
    wizard._step_views = {}
    wizard._step_label = None
    wizard._progress_label = None
    wizard._back_btn = None
    wizard._next_btn = None
    wizard._test_hotkey_label = None
    wizard._render = lambda: None
    return wizard


def test_next_applies_fast_choice_and_advances_when_api_key_is_present(
    tmp_path, monkeypatch
) -> None:
    _isolate_prefs(tmp_path, monkeypatch)
    monkeypatch.delenv("DEEPGRAM_API_KEY", raising=False)

    wizard = _make_wizard(OnboardingChoice.FAST, api_key=" dg-123 ")
    settings_changed_calls: list[bool] = []
    step_changes: list[OnboardingStep] = []
    wizard._on_settings_changed = lambda: settings_changed_calls.append(True)
    wizard._set_step = lambda step: step_changes.append(step)

    wizard._handle_action("next")

    env = prefs.read_env_file()
    assert env.get("DEEPGRAM_API_KEY") == "dg-123"
    assert env.get("PULSESCRIBE_MODE") == "deepgram"
    assert prefs.get_onboarding_choice() == OnboardingChoice.FAST
    assert step_changes == [OnboardingStep.PERMISSIONS]
    assert settings_changed_calls == [True]
    assert wizard._api_key_container.hidden is True


def test_next_accepts_existing_groq_key_for_fast_choice(
    tmp_path, monkeypatch
) -> None:
    _isolate_prefs(tmp_path, monkeypatch)
    monkeypatch.delenv("DEEPGRAM_API_KEY", raising=False)
    monkeypatch.setenv("GROQ_API_KEY", "grq-123")

    wizard = _make_wizard(OnboardingChoice.FAST, api_key="")
    settings_changed_calls: list[bool] = []
    step_changes: list[OnboardingStep] = []
    wizard._on_settings_changed = lambda: settings_changed_calls.append(True)
    wizard._set_step = lambda step: step_changes.append(step)

    wizard._handle_action("next")

    env = prefs.read_env_file()
    assert "DEEPGRAM_API_KEY" not in env
    assert env.get("PULSESCRIBE_MODE") == "groq"
    assert prefs.get_onboarding_choice() == OnboardingChoice.FAST
    assert step_changes == [OnboardingStep.PERMISSIONS]
    assert settings_changed_calls == [True]
    assert wizard._api_key_container.hidden is True


def test_next_prompts_for_api_key_without_advancing_when_fast_has_no_key(
    tmp_path, monkeypatch
) -> None:
    _isolate_prefs(tmp_path, monkeypatch)
    monkeypatch.delenv("DEEPGRAM_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)

    wizard = _make_wizard(OnboardingChoice.FAST, api_key="")
    render_calls: list[bool] = []
    step_changes: list[OnboardingStep] = []
    wizard._render = lambda: render_calls.append(True)
    wizard._set_step = lambda step: step_changes.append(step)

    wizard._handle_action("next")

    assert step_changes == []
    assert render_calls == [True]
    assert wizard._api_key_container.hidden is False
    assert "Cloud API key" in wizard._api_key_status.value
    assert wizard._api_key_field.focus_requested is True
    assert prefs.get_onboarding_choice() is None
    assert prefs.read_env_file() == {}


def test_open_privacy_settings_restores_floating_window_level(monkeypatch) -> None:
    wizard = OnboardingWizardController.__new__(OnboardingWizardController)
    wizard._window = _FakeWindow()

    calls: list[tuple[str, object]] = []
    monkeypatch.setattr(
        permissions_mod,
        "open_privacy_settings",
        lambda anchor, window=None: calls.append((anchor, window)),
    )
    monkeypatch.setitem(
        sys.modules,
        "AppKit",
        SimpleNamespace(NSFloatingWindowLevel=99),
    )

    wizard._open_privacy_settings("Privacy_Microphone")

    assert calls == [("Privacy_Microphone", wizard._window)]
    assert wizard._window.levels == [99]
