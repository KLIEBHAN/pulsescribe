"""Helpers for local-backend UI defaults and persistence."""

from __future__ import annotations

DEFAULT_LOCAL_BACKEND = "auto"
VALID_LOCAL_BACKENDS = {"whisper", "faster", "mlx", "lightning", "auto"}
DEFAULT_CPU_THREADS_LIMIT = 32
LOCAL_BACKEND_ALIASES = {
    "openai-whisper": "whisper",
    "faster-whisper": "faster",
    "mlx-whisper": "mlx",
    "lightning-whisper-mlx": "lightning",
}


def normalize_local_backend(value: str | None) -> str:
    """Normalize user/env value to a valid local backend."""
    if value is None:
        return DEFAULT_LOCAL_BACKEND

    normalized = value.strip().lower()
    normalized = LOCAL_BACKEND_ALIASES.get(normalized, normalized)
    if normalized in VALID_LOCAL_BACKENDS:
        return normalized
    return DEFAULT_LOCAL_BACKEND


def should_remove_local_backend_env(value: str | None) -> bool:
    """Return True when backend should use runtime default (env key removed)."""
    return normalize_local_backend(value) == DEFAULT_LOCAL_BACKEND


def get_cpu_threads_limit(cpu_count: int | None = None) -> int:
    """Return an upper bound for CPU thread input in UI fields.

    The old hard limit of 32 blocked valid values on high-core systems.
    We keep 32 as minimum fallback and allow up to detected logical CPUs.
    """
    detected = cpu_count
    if detected is None or detected <= 0:
        detected = DEFAULT_CPU_THREADS_LIMIT
    return max(DEFAULT_CPU_THREADS_LIMIT, int(detected))
