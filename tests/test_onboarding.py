import utils.preferences as prefs
from utils.onboarding import OnboardingChoice, OnboardingStep
from ui.onboarding_wizard import OnboardingWizardController


def _isolate_prefs(tmp_path, monkeypatch):
    monkeypatch.setattr(prefs, "PREFS_FILE", tmp_path / "preferences.json")
    monkeypatch.setattr(prefs, "ENV_FILE", tmp_path / ".env")


def test_onboarding_step_default_is_choose_goal(tmp_path, monkeypatch):
    _isolate_prefs(tmp_path, monkeypatch)
    assert prefs.get_onboarding_step() == OnboardingStep.CHOOSE_GOAL


def test_onboarding_step_defaults_to_done_when_seen(tmp_path, monkeypatch):
    _isolate_prefs(tmp_path, monkeypatch)
    prefs.save_preferences({"has_seen_onboarding": True})
    assert prefs.get_onboarding_step() == OnboardingStep.DONE


def test_set_onboarding_step_persists_and_marks_seen_on_done(tmp_path, monkeypatch):
    _isolate_prefs(tmp_path, monkeypatch)
    prefs.set_onboarding_step(OnboardingStep.PERMISSIONS)
    assert prefs.load_preferences()["onboarding_step"] == "permissions"
    assert prefs.load_preferences().get("has_seen_onboarding") in (None, False)

    prefs.set_onboarding_step(OnboardingStep.DONE)
    data = prefs.load_preferences()
    assert data["onboarding_step"] == "done"
    assert data["has_seen_onboarding"] is True


def test_onboarding_choice_roundtrip(tmp_path, monkeypatch):
    _isolate_prefs(tmp_path, monkeypatch)
    assert prefs.get_onboarding_choice() is None
    prefs.set_onboarding_choice(OnboardingChoice.FAST)
    assert prefs.get_onboarding_choice() == OnboardingChoice.FAST
    prefs.set_onboarding_choice(None)
    assert prefs.get_onboarding_choice() is None


def test_hotkey_preset_updates_toggle_without_clearing_hold(tmp_path, monkeypatch):
    _isolate_prefs(tmp_path, monkeypatch)
    prefs.save_env_setting("PULSESCRIBE_TOGGLE_HOTKEY", "option+space")
    prefs.save_env_setting("PULSESCRIBE_HOLD_HOTKEY", "fn")

    wizard = OnboardingWizardController(persist_progress=False)
    monkeypatch.setattr(wizard, "_stop_hotkey_recording", lambda *a, **k: None)
    wizard._handle_action("hotkey_f19_toggle")

    env = prefs.read_env_file()
    assert env.get("PULSESCRIBE_TOGGLE_HOTKEY") == "f19"
    assert env.get("PULSESCRIBE_HOLD_HOTKEY") == "fn"


def test_hotkey_preset_updates_hold_without_clearing_toggle(tmp_path, monkeypatch):
    _isolate_prefs(tmp_path, monkeypatch)
    prefs.save_env_setting("PULSESCRIBE_TOGGLE_HOTKEY", "f19")
    prefs.save_env_setting("PULSESCRIBE_HOLD_HOTKEY", "capslock")
    prefs.save_env_setting("PULSESCRIBE_HOTKEY", "f13")
    prefs.save_env_setting("PULSESCRIBE_HOTKEY_MODE", "toggle")

    wizard = OnboardingWizardController(persist_progress=False)
    monkeypatch.setattr(wizard, "_stop_hotkey_recording", lambda *a, **k: None)
    wizard._handle_action("hotkey_fn_hold")

    env = prefs.read_env_file()
    assert env.get("PULSESCRIBE_TOGGLE_HOTKEY") == "f19"
    assert env.get("PULSESCRIBE_HOLD_HOTKEY") == "fn"
    assert "PULSESCRIBE_HOTKEY" not in env
    assert "PULSESCRIBE_HOTKEY_MODE" not in env


def test_set_api_key_saves_non_empty_value(tmp_path, monkeypatch):
    _isolate_prefs(tmp_path, monkeypatch)

    saved = prefs.set_api_key("DEEPGRAM_API_KEY", " dg-123 ")

    env = prefs.read_env_file()
    assert saved is True
    assert env.get("DEEPGRAM_API_KEY") == "dg-123"


def test_set_api_key_empty_value_removes_existing(tmp_path, monkeypatch):
    _isolate_prefs(tmp_path, monkeypatch)
    prefs.save_api_key("DEEPGRAM_API_KEY", "dg-123")

    saved = prefs.set_api_key("DEEPGRAM_API_KEY", "   ")

    env = prefs.read_env_file()
    assert saved is False
    assert "DEEPGRAM_API_KEY" not in env
