from ui.settings_apply_feedback import (
    build_save_apply_change_hint,
    build_settings_loaded_feedback,
    build_settings_saved_feedback,
    build_unsaved_settings_feedback,
)


def test_build_settings_loaded_feedback_explains_saved_values() -> None:
    text, color = build_settings_loaded_feedback()

    assert text.startswith("Showing the current saved settings")
    assert "Save & Apply" in text
    assert color == "text_secondary"


def test_build_unsaved_settings_feedback_can_include_relaunch_hint() -> None:
    text, color = build_unsaved_settings_feedback(relaunch_required=True)

    assert text.startswith("You have local changes")
    assert "Dock icon changes still need a relaunch" in text
    assert color == "warning"


def test_build_settings_saved_feedback_handles_reload_and_relaunch_cases() -> None:
    auto_text, auto_color = build_settings_saved_feedback(auto_reload_worked=True)
    failed_text, failed_color = build_settings_saved_feedback(auto_reload_worked=False)
    relaunch_text, relaunch_color = build_settings_saved_feedback(
        relaunch_required=True,
    )
    combined_auto_text, combined_auto_color = build_settings_saved_feedback(
        auto_reload_worked=True,
        relaunch_required=True,
    )
    combined_failed_text, combined_failed_color = build_settings_saved_feedback(
        auto_reload_worked=False,
        relaunch_required=True,
    )

    assert "reload them automatically" in auto_text
    assert auto_color == "success"

    assert "automatic reload failed" in failed_text
    assert failed_color == "warning"

    assert relaunch_text == (
        "Settings saved. Relaunch PulseScribe to apply the Dock icon change."
    )
    assert relaunch_color == "success"

    assert combined_auto_text == (
        "Settings saved. PulseScribe will reload most changes automatically. "
        "Relaunch PulseScribe to apply the Dock icon change."
    )
    assert combined_auto_color == "success"

    assert combined_failed_text == (
        "Settings saved, but automatic reload failed. Relaunch PulseScribe to "
        "apply the changes, including the Dock icon change."
    )
    assert combined_failed_color == "warning"


def test_build_settings_saved_feedback_accepts_integer_reload_flags() -> None:
    success_text, success_color = build_settings_saved_feedback(auto_reload_worked=1)
    failure_text, failure_color = build_settings_saved_feedback(auto_reload_worked=0)

    assert success_text == "Settings saved. PulseScribe will reload them automatically."
    assert success_color == "success"

    assert failure_text == (
        "Settings saved, but automatic reload failed. Restart PulseScribe to apply the changes."
    )
    assert failure_color == "warning"


def test_build_settings_saved_feedback_accepts_string_reload_flags() -> None:
    success_text, success_color = build_settings_saved_feedback(
        auto_reload_worked=" true ",
    )
    failure_text, failure_color = build_settings_saved_feedback(
        auto_reload_worked="false",
    )

    assert success_text == "Settings saved. PulseScribe will reload them automatically."
    assert success_color == "success"

    assert failure_text == (
        "Settings saved, but automatic reload failed. Restart PulseScribe to apply the changes."
    )
    assert failure_color == "warning"


def test_build_save_apply_change_hint_switches_between_singular_and_plural() -> None:
    assert build_save_apply_change_hint() == "Click Save & Apply to keep this change."
    assert (
        build_save_apply_change_hint(plural=True)
        == "Click Save & Apply to keep these changes."
    )
