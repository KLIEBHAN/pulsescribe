"""Helpers for local-backend UI defaults and persistence."""

from __future__ import annotations

DEFAULT_LOCAL_BACKEND = "auto"
VALID_LOCAL_BACKENDS = {"whisper", "faster", "mlx", "lightning", "auto"}


def normalize_local_backend(value: str | None) -> str:
    """Normalize user/env value to a valid local backend."""
    if value is None:
        return DEFAULT_LOCAL_BACKEND

    normalized = value.strip().lower()
    if normalized in VALID_LOCAL_BACKENDS:
        return normalized
    return DEFAULT_LOCAL_BACKEND


def should_remove_local_backend_env(value: str | None) -> bool:
    """Return True when backend should use runtime default (env key removed)."""
    return normalize_local_backend(value) == DEFAULT_LOCAL_BACKEND
