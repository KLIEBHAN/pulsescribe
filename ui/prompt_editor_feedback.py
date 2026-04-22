"""Shared UX feedback for prompt editor surfaces."""

from __future__ import annotations

from dataclasses import dataclass

from utils.custom_prompts import (
    get_prompt_editor_context_label,
    normalize_prompt_editor_context,
    parse_app_mappings,
)


@dataclass(frozen=True)
class PromptEditorStateFeedback:
    text: str
    color: str
    save_enabled: bool
    reset_enabled: bool


def _normalize_text(text: str | None) -> str:
    return str(text or "")


def _context_subject(context: str) -> str:
    label = get_prompt_editor_context_label(context)
    context_key = normalize_prompt_editor_context(context)
    if context_key in {"voice_commands", "app_mappings"}:
        return label
    if context_key == "default":
        return "default prompt"
    return f"{label} prompt"


def _has_invalid_app_mapping_lines(text: str) -> bool:
    relevant_lines = [
        line.strip()
        for line in _normalize_text(text).splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    if not relevant_lines:
        return False
    return len(parse_app_mappings(text)) < len(relevant_lines)


def _semantic_editor_state(context: str, text: str | None) -> object:
    context_key = normalize_prompt_editor_context(context)
    normalized_text = _normalize_text(text)
    if context_key == "app_mappings":
        return tuple(sorted(parse_app_mappings(normalized_text).items()))
    return normalized_text


def build_prompt_editor_state_feedback(
    context: str | None,
    draft_text: str | None,
    *,
    saved_text: str | None,
    default_text: str | None,
) -> PromptEditorStateFeedback:
    """Return contextual editor guidance plus button affordance state."""
    context_key = normalize_prompt_editor_context(context)
    subject = _context_subject(context_key)
    draft = _normalize_text(draft_text)
    saved = _normalize_text(saved_text)
    default = _normalize_text(default_text)
    draft_state = _semantic_editor_state(context_key, draft)
    saved_state = _semantic_editor_state(context_key, saved)
    default_state = _semantic_editor_state(context_key, default)

    save_enabled = draft_state != saved_state
    reset_enabled = draft_state != default_state

    if draft_state == saved_state:
        if draft_state == default_state:
            text = f"Using the built-in {subject}."
        else:
            text = f"Using a saved custom {subject}."
        color = "text_secondary"
    elif draft_state == default_state:
        text = f"Built-in {subject} restored here. Save to remove the custom version."
        color = "warning"
    elif not draft.strip() and default.strip():
        text = f"This draft is empty. Saving will fall back to the built-in {subject}."
        color = "warning"
    elif saved_state == default_state:
        text = f"Unsaved custom {subject}. Save to keep this override."
        color = "warning"
    else:
        text = f"Unsaved changes to {subject}."
        color = "warning"

    if context_key == "app_mappings" and _has_invalid_app_mapping_lines(draft):
        text = f"{text} Only 'App = context' lines are saved."
        color = "warning"

    return PromptEditorStateFeedback(
        text=text,
        color=color,
        save_enabled=save_enabled,
        reset_enabled=reset_enabled,
    )


__all__ = [
    "PromptEditorStateFeedback",
    "build_prompt_editor_state_feedback",
]
