"""Shared UX copy for secondary settings surfaces.

Covers Refine / output / clipboard style settings that appear in both the
Windows Settings window and the macOS Welcome controller.
"""

from config import DEFAULT_GEMINI_REFINE_MODEL, DEFAULT_REFINE_MODEL

_PROVIDER_LABELS = {
    "gemini": "Gemini",
    "groq": "Groq",
    "openai": "OpenAI",
    "openrouter": "OpenRouter",
}


def normalize_refine_provider(provider: str | None) -> str:
    normalized = (provider or "groq").strip().lower()
    if normalized in _PROVIDER_LABELS:
        return normalized
    return "groq"


def get_refine_provider_label(provider: str | None) -> str:
    return _PROVIDER_LABELS[normalize_refine_provider(provider)]


def get_refine_provider_default_model(provider: str | None) -> str:
    provider_key = normalize_refine_provider(provider)
    if provider_key == "gemini":
        return DEFAULT_GEMINI_REFINE_MODEL
    return DEFAULT_REFINE_MODEL


def build_refine_model_guidance(provider: str | None, model: str | None) -> str:
    provider_label = get_refine_provider_label(provider)
    default_model = get_refine_provider_default_model(provider)
    custom_model = (model or "").strip()
    if custom_model and custom_model != default_model:
        return (
            f"Custom override: {custom_model}. Clear this field to go back to "
            f"{provider_label}'s default model ({default_model})."
        )
    return (
        f"Leave Model empty to use {provider_label}'s default model "
        f"({default_model})."
    )


def build_refine_settings_feedback(
    *,
    refine_enabled: bool,
    provider: str | None,
    model: str | None,
    saved_state: tuple[bool, str, str] | None = None,
) -> tuple[str, str]:
    provider_key = normalize_refine_provider(provider)
    provider_label = get_refine_provider_label(provider_key)
    custom_model = (model or "").strip()
    default_model = get_refine_provider_default_model(provider_key)
    current_state = (bool(refine_enabled), provider_key, custom_model)
    dirty = saved_state is not None and tuple(saved_state) != current_state

    if refine_enabled:
        if custom_model and custom_model != default_model:
            detail = (
                f"Transcript cleanup is on. {provider_label} will use the custom "
                f"model {custom_model}."
            )
        else:
            detail = (
                f"Transcript cleanup is on. {provider_label} will use its default "
                f"model ({default_model}) unless you add an override."
            )
        guidance = "The matching API key is managed on the Providers tab."
    else:
        detail = (
            "Transcript cleanup is off, so PulseScribe will keep the raw "
            "transcription text."
        )
        guidance = (
            "Enable it if you want punctuation, formatting, and spoken-command "
            "cleanup after transcription."
        )

    if dirty:
        return (
            f"Unsaved changes here. {detail} {guidance} Click Save & Apply to keep "
            "this behavior.",
            "warning",
        )

    return (f"No changes to apply here. {detail} {guidance}", "text_secondary")


def build_display_settings_feedback(
    *,
    overlay_enabled: bool,
    rtf_enabled: bool,
    clipboard_restore_enabled: bool,
    dock_icon_enabled: bool | None = None,
    saved_state: tuple[bool, ...] | None = None,
) -> tuple[str, str]:
    current_state: tuple[bool, ...] = (
        bool(overlay_enabled),
        bool(rtf_enabled),
        bool(clipboard_restore_enabled),
    )
    if dock_icon_enabled is not None:
        current_state += (bool(dock_icon_enabled),)
    dirty = saved_state is not None and tuple(saved_state) != current_state

    parts = [
        "Overlay is on during recording"
        if overlay_enabled
        else "Overlay stays hidden during recording",
        "transcription speed details are shown after each result"
        if rtf_enabled
        else "transcription speed details stay hidden",
        "previous clipboard text is restored after paste"
        if clipboard_restore_enabled
        else "previous clipboard text is not restored after paste",
    ]
    if dock_icon_enabled is not None:
        parts.append(
            "Dock icon is shown"
            if dock_icon_enabled
            else "Dock icon is hidden after you relaunch"
        )
        parts.append("Dock icon changes still require a relaunch")

    detail = "; ".join(parts) + "."

    if dirty:
        return (
            f"Unsaved changes here. {detail} Click Save & Apply to keep this behavior.",
            "warning",
        )

    return (f"No changes to apply here. {detail}", "text_secondary")


__all__ = [
    "build_display_settings_feedback",
    "build_refine_model_guidance",
    "build_refine_settings_feedback",
    "get_refine_provider_default_model",
    "get_refine_provider_label",
    "normalize_refine_provider",
]
