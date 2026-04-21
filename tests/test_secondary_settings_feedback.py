from ui.secondary_settings_feedback import (
    build_display_settings_feedback,
    build_refine_model_guidance,
    build_refine_settings_feedback,
    get_refine_provider_default_model,
)


def test_get_refine_provider_default_model_uses_gemini_specific_default() -> None:
    assert get_refine_provider_default_model("gemini").startswith("gemini-")
    assert get_refine_provider_default_model("groq") == "openai/gpt-oss-120b"


def test_build_refine_model_guidance_distinguishes_default_and_custom_models() -> None:
    default_text = build_refine_model_guidance("groq", "openai/gpt-oss-120b")
    custom_text = build_refine_model_guidance("gemini", "gemini-custom")

    assert default_text.startswith("Leave Model empty")
    assert "Groq's default model" in default_text

    assert custom_text.startswith("Custom override")
    assert "gemini-custom" in custom_text


def test_build_refine_settings_feedback_reports_unsaved_custom_override() -> None:
    text, color = build_refine_settings_feedback(
        refine_enabled=True,
        provider="gemini",
        model="gemini-custom",
        saved_state=(False, "groq", ""),
    )

    assert text.startswith("Unsaved changes here")
    assert "Gemini will use the custom model gemini-custom" in text
    assert "Click Save & Apply" in text
    assert color == "warning"


def test_build_refine_settings_feedback_reports_clean_default_state() -> None:
    text, color = build_refine_settings_feedback(
        refine_enabled=True,
        provider="groq",
        model="openai/gpt-oss-120b",
        saved_state=(True, "groq", "openai/gpt-oss-120b"),
    )

    assert text.startswith("No changes to apply here")
    assert "default model (openai/gpt-oss-120b)" in text
    assert color == "text_secondary"


def test_build_display_settings_feedback_mentions_dock_restart_and_unsaved_state() -> None:
    text, color = build_display_settings_feedback(
        overlay_enabled=False,
        rtf_enabled=True,
        clipboard_restore_enabled=True,
        dock_icon_enabled=False,
        saved_state=(True, False, False, True),
    )

    assert text.startswith("Unsaved changes here")
    assert "Overlay stays hidden during recording" in text
    assert "transcription speed details are shown after each result" in text
    assert "previous clipboard text is restored after paste" in text
    assert "Dock icon changes still require a relaunch" in text
    assert color == "warning"
