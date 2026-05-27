"""Shared provider settings rules for setup surfaces."""

from __future__ import annotations

from collections.abc import Mapping

ApiKeyStatus = tuple[str, str]


def normalize_provider_mode(mode: str | None, *, default: str = "deepgram") -> str:
    """Return the canonical provider mode used by setup/status helpers."""
    return (mode or default).strip().lower() or default


def build_provider_api_key_status(
    provider_key: str,
    *,
    mode: str | None,
    configured: bool,
    required_provider_by_mode: Mapping[str, str],
) -> ApiKeyStatus:
    """Return display text and color key for one provider credential."""
    if configured:
        return "Configured", "success"

    current_mode = normalize_provider_mode(mode)
    if current_mode == "local":
        return "Not needed", "text_secondary"

    if provider_key == required_provider_by_mode.get(current_mode):
        return "Required", "warning"

    return "Optional", "text_secondary"


__all__ = [
    "ApiKeyStatus",
    "build_provider_api_key_status",
    "normalize_provider_mode",
]
