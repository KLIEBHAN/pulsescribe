import sys

import pytest

import utils.preferences as prefs
import utils.permissions as permissions
from utils.onboarding import (
    OnboardingChoice,
    OnboardingStep,
    coerce_onboarding_choice,
    coerce_onboarding_step,
    next_step,
    prev_step,
    step_index,
    total_steps,
)
from ui.onboarding_wizard import (
    OnboardingWizardController,
    _format_hotkey_for_display,
    _format_language_label,
    _language_code_from_title,
)


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
        self.color = None
        self.color_calls = 0

    def setStringValue_(self, value) -> None:
        self.value = value
        self.set_calls += 1

    def setTextColor_(self, value) -> None:
        self.color = value
        self.color_calls += 1


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

    def setAttributedTitle_(self, value) -> None:
        self.title = str(value.string())
        self.title_calls += 1

    def setEnabled_(self, value) -> None:
        self.enabled = value
        self.enabled_calls.append(value)


class _FakeTextView:
    def __init__(self):
        self.value = ""
        self.set_calls: list[str] = []

    def setString_(self, value) -> None:
        self.value = value
        self.set_calls.append(value)


class _FakeContentView:
    def __init__(self):
        self.subviews = []

    def addSubview_(self, view) -> None:
        self.subviews.append(view)


def _isolate_prefs(tmp_path, monkeypatch):
    monkeypatch.setattr(prefs, "PREFS_FILE", tmp_path / "preferences.json")
    monkeypatch.setattr(prefs, "ENV_FILE", tmp_path / ".env")
    prefs._env_cache = None
    prefs._prefs_cache = None


def test_onboarding_step_default_is_choose_goal(tmp_path, monkeypatch):
    _isolate_prefs(tmp_path, monkeypatch)
    assert prefs.get_onboarding_step() == OnboardingStep.CHOOSE_GOAL


def test_onboarding_helpers_progress_and_clamp_at_bounds() -> None:
    assert next_step(OnboardingStep.CHOOSE_GOAL) == OnboardingStep.PERMISSIONS
    assert next_step(OnboardingStep.DONE) == OnboardingStep.DONE
    assert prev_step(OnboardingStep.PERMISSIONS) == OnboardingStep.CHOOSE_GOAL
    assert prev_step(OnboardingStep.CHOOSE_GOAL) == OnboardingStep.CHOOSE_GOAL
    assert step_index(OnboardingStep.CHOOSE_GOAL) == 1
    assert step_index(OnboardingStep.CHEAT_SHEET) == total_steps()
    assert step_index(OnboardingStep.DONE) == total_steps()


def test_onboarding_helpers_handle_unknown_values_gracefully() -> None:
    assert coerce_onboarding_step("unknown") is None
    assert coerce_onboarding_choice("unknown") is None
    assert next_step("unknown") == OnboardingStep.DONE
    assert prev_step("unknown") == OnboardingStep.CHOOSE_GOAL
    assert step_index("unknown") == 1


def test_onboarding_display_helpers_use_friendly_labels() -> None:
    assert _format_language_label("de") == "German"
    assert _language_code_from_title("German") == "de"
    assert _language_code_from_title("auto") == "auto"
    assert _format_hotkey_for_display("option+space") == "Option+Space"
    assert _format_hotkey_for_display("f19") == "F19"
    assert _format_hotkey_for_display("capslock") == "Caps Lock"


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


def test_set_onboarding_step_invalid_value_falls_back_to_done(tmp_path, monkeypatch):
    _isolate_prefs(tmp_path, monkeypatch)

    prefs.set_onboarding_step("totally-unknown")

    assert prefs.load_preferences() == {
        "onboarding_step": "done",
        "has_seen_onboarding": True,
    }


def test_onboarding_choice_roundtrip(tmp_path, monkeypatch):
    _isolate_prefs(tmp_path, monkeypatch)
    assert prefs.get_onboarding_choice() is None
    prefs.set_onboarding_choice(OnboardingChoice.FAST)
    assert prefs.get_onboarding_choice() == OnboardingChoice.FAST
    prefs.set_onboarding_choice(None)
    assert prefs.get_onboarding_choice() is None


def test_set_onboarding_choice_accepts_string_values(tmp_path, monkeypatch):
    _isolate_prefs(tmp_path, monkeypatch)

    prefs.set_onboarding_choice("advanced")

    assert prefs.load_preferences()["onboarding_choice"] == "advanced"
    assert prefs.get_onboarding_choice() == OnboardingChoice.ADVANCED


def test_set_onboarding_choice_invalid_value_clears_choice_only(tmp_path, monkeypatch):
    _isolate_prefs(tmp_path, monkeypatch)
    prefs.save_preferences(
        {
            "onboarding_choice": "fast",
            "display_name": "Müller",
        }
    )

    prefs.set_onboarding_choice("not-a-choice")

    assert prefs.load_preferences() == {"display_name": "Müller"}


def test_set_onboarding_seen_preserves_existing_preferences(tmp_path, monkeypatch):
    _isolate_prefs(tmp_path, monkeypatch)
    prefs.save_preferences({"display_name": "Müller"})

    prefs.set_onboarding_seen(True)

    assert prefs.load_preferences() == {
        "display_name": "Müller",
        "has_seen_onboarding": True,
    }


def test_set_show_welcome_on_startup_preserves_existing_preferences(
    tmp_path, monkeypatch
):
    _isolate_prefs(tmp_path, monkeypatch)
    prefs.save_preferences({"display_name": "Müller"})

    prefs.set_show_welcome_on_startup(False)

    assert prefs.load_preferences() == {
        "display_name": "Müller",
        "show_welcome_on_startup": False,
    }


def test_hotkey_preset_replaces_both_hotkeys_with_one_toggle(tmp_path, monkeypatch):
    _isolate_prefs(tmp_path, monkeypatch)
    prefs.save_env_setting("PULSESCRIBE_TOGGLE_HOTKEY", "option+space")
    prefs.save_env_setting("PULSESCRIBE_HOLD_HOTKEY", "fn")

    wizard = OnboardingWizardController(persist_progress=False)
    wizard._handle_action("hotkey_f19_toggle")

    env = prefs.read_env_file()
    assert env.get("PULSESCRIBE_TOGGLE_HOTKEY") == "f19"
    assert "PULSESCRIBE_HOLD_HOTKEY" not in env


def test_hotkey_preset_saves_hold_and_clears_toggle_and_legacy_keys(tmp_path, monkeypatch):
    _isolate_prefs(tmp_path, monkeypatch)
    monkeypatch.setattr(
        permissions,
        "check_input_monitoring_permission",
        lambda show_alert=False, request=False: True,
    )
    prefs.save_env_setting("PULSESCRIBE_TOGGLE_HOTKEY", "fn")
    prefs.save_env_setting("PULSESCRIBE_HOLD_HOTKEY", "capslock")
    prefs.save_env_setting("PULSESCRIBE_HOTKEY", "f13")
    prefs.save_env_setting("PULSESCRIBE_HOTKEY_MODE", "toggle")

    wizard = OnboardingWizardController(persist_progress=False)
    wizard._handle_action("hotkey_fn_hold")

    env = prefs.read_env_file()
    assert env.get("PULSESCRIBE_HOLD_HOTKEY") == "fn"
    # Toggle "fn" would conflict, but the wizard clears that slot in the same update.
    assert "PULSESCRIBE_TOGGLE_HOTKEY" not in env
    assert "PULSESCRIBE_HOTKEY" not in env
    assert "PULSESCRIBE_HOTKEY_MODE" not in env


def test_selected_hotkey_choice_maps_config_to_rows() -> None:
    from ui.onboarding_wizard import _selected_hotkey_choice

    assert _selected_hotkey_choice("", "fn") == "hold_fn"
    assert _selected_hotkey_choice("option+space", "") == "toggle_opt_space"
    assert _selected_hotkey_choice("f19", "") == "toggle_f19"
    assert _selected_hotkey_choice("ctrl+shift+d", "") == "custom"
    assert _selected_hotkey_choice("", "capslock") == "custom"
    # Two hotkeys or none: nothing preselected, config stays as it is.
    assert _selected_hotkey_choice("option+space", "fn") is None
    assert _selected_hotkey_choice("", "") is None


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
    wizard._subtitle_label = None
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


@pytest.mark.skipif(sys.platform != "darwin", reason="sets AppKit button titles")
def test_render_skips_duplicate_ui_mutations_for_same_step(monkeypatch):
    import ui.onboarding_wizard as wizard_mod

    wizard = OnboardingWizardController.__new__(OnboardingWizardController)
    wizard._step = OnboardingStep.TEST_DICTATION
    wizard._step_views = {
        OnboardingStep.TEST_DICTATION: _FakeView(),
        OnboardingStep.HOTKEY: _FakeView(),
    }
    wizard._step_label = _FakeTextField()
    wizard._subtitle_label = _FakeTextField()
    wizard._back_btn = _FakeButton()
    wizard._next_btn = _FakeButton()
    wizard._skip_test_btn = _FakeButton()
    wizard._test_hotkey_label = None
    wizard._ensure_step_built = lambda _step: None
    wizard._wizard_title = lambda _step: ("Try it", "A safe test.")
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
    assert wizard._subtitle_label.set_calls == 1
    assert wizard._back_btn.hidden_calls == [False]
    assert wizard._skip_test_btn.hidden_calls == [False]
    assert wizard._next_btn.title_calls == 1
    assert wizard._next_btn.enabled_calls == [True]


def test_update_summary_skips_duplicate_widget_updates():
    wizard = OnboardingWizardController.__new__(OnboardingWizardController)
    wizard._summary_provider_label = _FakeTextField()
    wizard._summary_hotkey_label = _FakeTextField()
    wizard._summary_perm_label = _FakeTextField()
    wizard._summary_test_label = _FakeTextField()
    wizard._ready_label = _FakeTextField()
    wizard._test_outcome = "passed"
    wizard._get_cached_env_setting = lambda key: {
        "PULSESCRIBE_MODE": "deepgram",
        "DEEPGRAM_API_KEY": "dg-123",
        "PULSESCRIBE_TOGGLE_HOTKEY": "f19",
        "PULSESCRIBE_HOLD_HOTKEY": "",
    }.get(key)
    wizard._get_cached_hotkeys = lambda: ("f19", "")
    wizard._read_permission_signature = lambda: ("authorized", True, True)

    wizard._update_summary()
    wizard._update_summary()

    for label in (
        wizard._summary_provider_label,
        wizard._summary_hotkey_label,
        wizard._summary_perm_label,
        wizard._summary_test_label,
        wizard._ready_label,
    ):
        assert label.set_calls == 1
    assert wizard._ready_label.value == "Press F19 in any app to dictate, and again to stop."


def test_review_row_helpers_map_states_to_badges() -> None:
    import ui.onboarding_wizard as wizard_mod

    assert wizard_mod._build_provider_summary_text("deepgram", False) == (
        "Deepgram (Cloud, fastest)",
        "No API key",
        "warn",
    )
    assert wizard_mod._build_provider_summary_text("local", False)[1:] == ("Ready", "ok")
    assert wizard_mod._build_hotkey_summary_text("", "") == (
        "Choose one in the Hotkey step",
        "Not set",
        "warn",
    )
    assert wizard_mod._build_permission_summary_text("denied", True, True)[1:] == (
        "Missing",
        "err",
    )
    assert wizard_mod._build_permission_summary_text("authorized", False, True)[1:] == (
        "Partial",
        "warn",
    )
    assert wizard_mod._build_test_summary_text("skipped")[1:] == ("Skipped", "muted")
    assert wizard_mod._build_test_summary_text("error")[1:] == ("Error", "err")
    assert wizard_mod._build_ready_text("", "fn") == "Hold Fn in any app to dictate."
    assert wizard_mod._build_ready_text("", "") == (
        "Set a hotkey in Settings to start dictating."
    )


def test_can_advance_reuses_cached_permission_signature(monkeypatch):
    import ui.onboarding_wizard as wizard_mod

    wizard = OnboardingWizardController.__new__(OnboardingWizardController)
    wizard._step = OnboardingStep.PERMISSIONS
    wizard._permissions_card = type(
        "_Card",
        (),
        {"get_cached_permission_signature": lambda self: ("authorized", False, False)},
    )()

    monkeypatch.setattr(
        wizard_mod,
        "get_microphone_permission_state",
        lambda: (_ for _ in ()).throw(AssertionError("should use cached signature")),
    )

    assert wizard._can_advance() is True


def test_update_summary_reuses_cached_permission_signature(monkeypatch):
    import ui.onboarding_wizard as wizard_mod

    wizard = OnboardingWizardController.__new__(OnboardingWizardController)
    wizard._summary_provider_label = _FakeTextField()
    wizard._summary_hotkey_label = _FakeTextField()
    wizard._summary_perm_label = _FakeTextField()
    wizard._summary_test_label = _FakeTextField()
    wizard._test_outcome = "passed"
    wizard._permissions_card = type(
        "_Card",
        (),
        {"get_cached_permission_signature": lambda self: ("authorized", True, True)},
    )()
    wizard._get_cached_env_setting = lambda key: {
        "PULSESCRIBE_MODE": "deepgram",
        "PULSESCRIBE_TOGGLE_HOTKEY": "option+space",
        "PULSESCRIBE_HOLD_HOTKEY": "fn",
    }.get(key)
    wizard._get_cached_hotkeys = lambda: ("option+space", "fn")

    monkeypatch.setattr(
        wizard_mod,
        "get_microphone_permission_state",
        lambda: (_ for _ in ()).throw(AssertionError("should use cached signature")),
    )
    monkeypatch.setattr(
        wizard_mod,
        "has_accessibility_permission",
        lambda: (_ for _ in ()).throw(AssertionError("should use cached signature")),
        raising=False,
    )
    monkeypatch.setattr(
        wizard_mod,
        "has_input_monitoring_permission",
        lambda: (_ for _ in ()).throw(AssertionError("should use cached signature")),
        raising=False,
    )

    wizard._update_summary()

    assert wizard._summary_hotkey_label.value == "Toggle Option+Space · Hold Fn"
    assert (
        wizard._summary_perm_label.value
        == "Microphone, auto-paste and Hold hotkeys ready"
    )
    assert wizard._summary_test_label.value == "Your speech was transcribed"


def test_practice_prompt_shows_the_configured_hotkey() -> None:
    from ui.onboarding_wizard import _build_test_prompt

    assert _build_test_prompt("option+space", "fn") == (
        "Hold",
        "Fn",
        "and say a short sentence.",
    )
    assert _build_test_prompt("option+space", "") == (
        "Press",
        "Option+Space",
        "and say a short sentence, then press it again.",
    )
    assert _build_test_prompt("", "") == (
        "",
        "",
        "Click Start practice and say a short sentence.",
    )


def _make_practice_wizard(state: str = "idle", outcome: str = "pending"):
    wizard = OnboardingWizardController.__new__(OnboardingWizardController)
    wizard._step = OnboardingStep.TEST_DICTATION
    wizard._test_state = state
    wizard._test_outcome = outcome
    wizard._test_successful = False
    wizard._test_status_label = None
    wizard._test_text_view = _FakeTextView()
    wizard._test_start_btn = _FakeButton()
    wizard._test_stop_btn = _FakeButton()
    wizard._last_test_preview_text = None
    wizard._on_test_dictation_start = None
    wizard._on_test_dictation_stop = None
    wizard._on_test_dictation_cancel = None
    render_calls: list[bool] = []
    wizard._render = lambda: render_calls.append(True)
    return wizard, render_calls


def test_start_test_dictation_sets_starting_feedback_and_shows_stop_button():
    wizard, _ = _make_practice_wizard()
    start_calls: list[bool] = []
    wizard._on_test_dictation_start = lambda: start_calls.append(True)

    wizard._start_test_dictation()

    assert start_calls == [True]
    assert wizard._test_state == "starting"
    assert wizard._test_outcome == "starting"
    assert wizard._test_text_view.value == "Getting ready…"
    assert wizard._test_start_btn.hidden is True
    assert wizard._test_stop_btn.hidden is False
    assert wizard._test_stop_btn.title == "Cancel"


def test_stop_test_dictation_uses_cancel_path_while_starting():
    wizard, _ = _make_practice_wizard("starting", "starting")
    cancel_calls: list[bool] = []
    wizard._on_test_dictation_cancel = lambda: cancel_calls.append(True)

    wizard._stop_test_dictation()

    assert cancel_calls == [True]
    assert wizard._test_state == "idle"
    assert wizard._test_outcome == "cancelled"
    assert wizard._test_text_view.value == "Cancelled. Start again whenever you're ready."
    assert wizard._test_start_btn.hidden is False
    assert wizard._test_start_btn.title == "Try again"
    assert wizard._test_stop_btn.hidden is True


def test_on_test_dictation_hotkey_state_updates_preview_and_processing_buttons():
    wizard, render_calls = _make_practice_wizard()

    wizard.on_test_dictation_hotkey_state("recording")

    assert wizard._test_outcome == "recording"
    assert wizard._test_text_view.value == "Listening… say a short sentence."
    assert wizard._test_start_btn.hidden is True
    assert wizard._test_stop_btn.hidden is False
    assert wizard._test_stop_btn.title == "Stop"

    wizard.on_test_dictation_hotkey_state("processing")

    assert wizard._test_outcome == "processing"
    assert wizard._test_text_view.value == "Transcribing…"
    assert wizard._test_start_btn.hidden is False
    assert wizard._test_start_btn.title == "Working…"
    assert wizard._test_start_btn.enabled is False
    assert wizard._test_stop_btn.hidden is True
    assert render_calls == [True, True]


def test_on_test_dictation_result_shows_error_and_try_again():
    wizard, render_calls = _make_practice_wizard("recording")

    wizard.on_test_dictation_result("", error="microphone missing")

    assert wizard._test_outcome == "error"
    assert wizard._test_text_view.value == (
        "Microphone access is unavailable for the practice test. Try again."
    )
    assert wizard._test_start_btn.hidden is False
    assert wizard._test_start_btn.title == "Try again"
    assert wizard._test_stop_btn.hidden is True
    assert render_calls == [True]

    wizard.on_test_dictation_result("Hello there")

    assert wizard._test_outcome == "passed"
    assert wizard._test_successful is True
    assert wizard._test_text_view.value == "Hello there"
    assert wizard._test_start_btn.title == "Try again"


def test_practice_badges_follow_test_states() -> None:
    from ui.onboarding_wizard import TEST_BADGES

    assert TEST_BADGES["recording"] == ("Listening", "muted")
    assert TEST_BADGES["processing"] == ("Processing", "muted")
    assert TEST_BADGES["passed"] == ("Worked", "ok")
    assert TEST_BADGES["empty"] == ("No speech", "warn")
    assert TEST_BADGES["error"] == ("Error", "err")
    assert "pending" not in TEST_BADGES  # idle: badge hidden


def test_skip_test_marks_outcome_before_advancing() -> None:
    wizard = OnboardingWizardController.__new__(OnboardingWizardController)
    wizard._step = OnboardingStep.TEST_DICTATION
    wizard._test_outcome = "pending"

    advanced_to: list[OnboardingStep] = []
    wizard._set_step = lambda step: advanced_to.append(step)

    wizard._handle_action("skip_test")

    assert wizard._test_outcome == "skipped"
    assert advanced_to == [OnboardingStep.CHEAT_SHEET]


def test_apply_hotkey_change_saves_one_slot_and_clears_the_other(monkeypatch):
    import utils.hotkey_validation as hotkey_validation_mod

    wizard = OnboardingWizardController.__new__(OnboardingWizardController)
    applied_updates: list[dict[str, str | None]] = []
    on_settings_changed: list[bool] = []
    statuses: list[tuple[str, str | None]] = []
    render_calls: list[bool] = []
    validations: list[tuple[str, str, bool]] = []
    wizard._on_settings_changed = lambda: on_settings_changed.append(True)
    wizard._apply_env_updates = lambda updates: applied_updates.append(updates) or True
    wizard._set_hotkey_status = lambda level, message: statuses.append((level, message))
    wizard._render = lambda: render_calls.append(True)

    def fake_validate(kind, hotkey, *, replace_other=False):
        validations.append((kind, hotkey, replace_other))
        return "f19", "ok", None

    monkeypatch.setattr(hotkey_validation_mod, "validate_hotkey_change", fake_validate)

    assert wizard._apply_hotkey_change("toggle", "f19") is True
    assert validations == [("toggle", "f19", True)]
    assert applied_updates == [
        {
            "PULSESCRIBE_TOGGLE_HOTKEY": "f19",
            "PULSESCRIBE_HOLD_HOTKEY": None,
            "PULSESCRIBE_HOTKEY": None,
            "PULSESCRIBE_HOTKEY_MODE": None,
        }
    ]
    assert on_settings_changed == [True]
    assert statuses == [("ok", "Saved.")]
    assert render_calls == [True]


def test_show_fast_api_key_prompt_skips_duplicate_widget_updates():
    wizard = OnboardingWizardController.__new__(OnboardingWizardController)
    wizard._api_key_container = _FakeView()
    wizard._api_key_status = _FakeTextField()
    wizard._last_api_key_prompt_visible = None
    wizard._last_api_key_prompt_message = None

    focus_calls: list[bool] = []
    render_calls: list[bool] = []
    wizard._focus_api_key_field = lambda: focus_calls.append(True)
    wizard._render = lambda: render_calls.append(True)

    wizard._show_fast_api_key_prompt()
    wizard._show_fast_api_key_prompt()

    assert wizard._api_key_container.hidden_calls == [False]
    assert wizard._api_key_status.set_calls == 1
    assert wizard._api_key_status.value == (
        "Fast mode needs a Deepgram API key. Paste one to continue."
    )
    assert focus_calls == [True, True]
    assert render_calls == [True, True]
