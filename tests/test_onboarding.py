import utils.preferences as prefs
import utils.permissions as permissions
from utils.onboarding import OnboardingChoice, OnboardingStep
from ui.onboarding_wizard import OnboardingWizardController


class _FakeView:
    def __init__(self):
        self.hidden = None
        self.hidden_calls: list[bool] = []

    def setHidden_(self, value) -> None:
        self.hidden = value
        self.hidden_calls.append(value)


class _FakeTextField:
    def __init__(self):
        self.value = ""
        self.set_calls = 0

    def setStringValue_(self, value) -> None:
        self.value = value
        self.set_calls += 1


class _FakeButton:
    def __init__(self):
        self.hidden = None
        self.hidden_calls: list[bool] = []
        self.title = ""
        self.title_calls = 0
        self.enabled = None
        self.enabled_calls: list[bool] = []

    def setHidden_(self, value) -> None:
        self.hidden = value
        self.hidden_calls.append(value)

    def setTitle_(self, value) -> None:
        self.title = value
        self.title_calls += 1

    def setEnabled_(self, value) -> None:
        self.enabled = value
        self.enabled_calls.append(value)


class _FakeContentView:
    def __init__(self):
        self.subviews = []

    def addSubview_(self, view) -> None:
        self.subviews.append(view)


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
    monkeypatch.setattr(
        permissions,
        "check_input_monitoring_permission",
        lambda show_alert=False, request=False: True,
    )
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


def test_ensure_step_built_runs_builder_once():
    wizard = OnboardingWizardController.__new__(OnboardingWizardController)
    wizard._content_view = _FakeContentView()
    wizard._step_views = {}
    wizard._step_content_height = 320
    wizard._step_frame = object()

    built: list[tuple[object, int]] = []
    wizard._step_builders = {
        OnboardingStep.HOTKEY: lambda parent, height: built.append((parent, height))
    }
    wizard._create_step_container = lambda: _FakeView()

    assert wizard._ensure_step_built(OnboardingStep.HOTKEY) is True
    assert wizard._ensure_step_built(OnboardingStep.HOTKEY) is False
    assert wizard._is_step_built(OnboardingStep.HOTKEY) is True
    assert len(wizard._content_view.subviews) == 1
    assert built == [(wizard._content_view.subviews[0], 320)]


def test_render_builds_selected_step_on_demand():
    wizard = OnboardingWizardController.__new__(OnboardingWizardController)
    wizard._step = OnboardingStep.TEST_DICTATION
    wizard._content_view = _FakeContentView()
    wizard._step_views = {}
    wizard._step_content_height = 280
    wizard._step_frame = object()
    wizard._step_label = None
    wizard._progress_label = None
    wizard._back_btn = None
    wizard._next_btn = None
    wizard._create_step_container = lambda: _FakeView()

    built: list[tuple[object, int]] = []
    wizard._step_builders = {
        OnboardingStep.TEST_DICTATION: lambda parent, height: built.append(
            (parent, height)
        )
    }
    hotkey_updates: list[bool] = []
    wizard._update_test_dictation_hotkeys = lambda: hotkey_updates.append(True)
    wizard._sync_hotkey_fields_from_env = lambda: None
    wizard._can_advance = lambda: True

    wizard._render()

    assert built == [(wizard._content_view.subviews[0], 280)]
    assert hotkey_updates == [True]
    assert wizard._content_view.subviews[0].hidden is False


def test_render_skips_duplicate_ui_mutations_for_same_step(monkeypatch):
    import ui.onboarding_wizard as wizard_mod

    wizard = OnboardingWizardController.__new__(OnboardingWizardController)
    wizard._step = OnboardingStep.TEST_DICTATION
    wizard._step_views = {
        OnboardingStep.TEST_DICTATION: _FakeView(),
        OnboardingStep.HOTKEY: _FakeView(),
    }
    wizard._step_label = _FakeTextField()
    wizard._progress_label = _FakeTextField()
    wizard._back_btn = _FakeButton()
    wizard._next_btn = _FakeButton()
    wizard._test_hotkey_label = _FakeTextField()
    wizard._ensure_step_built = lambda _step: None
    wizard._wizard_title = lambda _step: "Test Dictation"
    wizard._can_advance = lambda: True
    wizard._sync_hotkey_fields_from_env = lambda: None

    monkeypatch.setattr(
        wizard_mod,
        "get_env_setting",
        lambda key: "f19" if key == "PULSESCRIBE_TOGGLE_HOTKEY" else None,
    )

    wizard._render()
    wizard._render()

    current_view = wizard._step_views[OnboardingStep.TEST_DICTATION]
    other_view = wizard._step_views[OnboardingStep.HOTKEY]

    assert current_view.hidden_calls == [False]
    assert other_view.hidden_calls == [True]
    assert wizard._step_label.set_calls == 1
    assert wizard._progress_label.set_calls == 1
    assert wizard._back_btn.hidden_calls == [False]
    assert wizard._next_btn.title_calls == 1
    assert wizard._next_btn.enabled_calls == [True]
    assert wizard._test_hotkey_label.set_calls == 1
