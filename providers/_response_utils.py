"""Shared helpers for normalizing cloud-provider SDK responses."""

from __future__ import annotations


def extract_text_response(response: object) -> str | None:
    """Return plain transcript text from common SDK response shapes."""
    if isinstance(response, str):
        return response

    text = getattr(response, "text", None)
    if isinstance(text, str):
        return text

    return None


def serialize_openai_response(response: object, *, requested_format: str) -> str:
    """Convert OpenAI SDK responses into stable CLI output."""
    normalized = (requested_format or "text").strip().lower() or "text"
    text_response = extract_text_response(response)

    if normalized == "text" and text_response is not None:
        return text_response

    model_dump_json = getattr(response, "model_dump_json", None)
    if callable(model_dump_json):
        serialized = model_dump_json(indent=2)
        return serialized if isinstance(serialized, str) else str(serialized)

    if text_response is not None:
        return text_response

    return str(response)


def require_text_response(response: object, *, provider_name: str) -> str:
    """Return transcript text or raise a stable provider-specific type error."""
    text_response = extract_text_response(response)
    if text_response is not None:
        return text_response

    raise TypeError(f"Unerwarteter {provider_name}-Response-Typ: {type(response)}")
