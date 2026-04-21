from ui.prompt_editor_feedback import build_prompt_editor_state_feedback


def test_prompt_editor_feedback_handles_built_in_default_state() -> None:
    feedback = build_prompt_editor_state_feedback(
        "email",
        "default email",
        saved_text="default email",
        default_text="default email",
    )

    assert feedback.text == "Using the built-in Email prompt."
    assert feedback.color == "text_secondary"
    assert feedback.save_enabled is False
    assert feedback.reset_enabled is False


def test_prompt_editor_feedback_handles_unsaved_custom_override() -> None:
    feedback = build_prompt_editor_state_feedback(
        "email",
        "custom email",
        saved_text="default email",
        default_text="default email",
    )

    assert feedback.text == "Unsaved custom Email prompt. Save to keep this override."
    assert feedback.color == "warning"
    assert feedback.save_enabled is True
    assert feedback.reset_enabled is True


def test_prompt_editor_feedback_handles_saved_custom_state() -> None:
    feedback = build_prompt_editor_state_feedback(
        "email",
        "custom email",
        saved_text="custom email",
        default_text="default email",
    )

    assert feedback.text == "Using a saved custom Email prompt."
    assert feedback.color == "text_secondary"
    assert feedback.save_enabled is False
    assert feedback.reset_enabled is True


def test_prompt_editor_feedback_handles_restored_default_from_custom_state() -> None:
    feedback = build_prompt_editor_state_feedback(
        "voice_commands",
        "default voice commands",
        saved_text="custom voice commands",
        default_text="default voice commands",
    )

    assert feedback.text == (
        "Built-in Voice Commands restored here. Save to remove the custom version."
    )
    assert feedback.color == "warning"
    assert feedback.save_enabled is True
    assert feedback.reset_enabled is False


def test_prompt_editor_feedback_warns_about_invalid_app_mapping_lines() -> None:
    feedback = build_prompt_editor_state_feedback(
        "app_mappings",
        "Slack = chat\nBroken Line\nMail = email\nZoom = unknown",
        saved_text="# App → Context Mappings (one per line: AppName = context)\nMail = email",
        default_text="# App → Context Mappings (one per line: AppName = context)\nMail = email",
    )

    assert "Only 'App = context' lines are saved." in feedback.text
    assert feedback.color == "warning"
    assert feedback.save_enabled is True
