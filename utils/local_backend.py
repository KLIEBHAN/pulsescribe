"""Helpers for local-backend UI defaults, guidance, and persistence."""

from __future__ import annotations

from typing import NamedTuple

DEFAULT_LOCAL_BACKEND = "auto"
VALID_LOCAL_BACKENDS = {"whisper", "faster", "mlx", "lightning", "auto"}
DEFAULT_CPU_THREADS_LIMIT = 32
LOCAL_BACKEND_ALIASES = {
    "openai-whisper": "whisper",
    "faster-whisper": "faster",
    "mlx-whisper": "mlx",
    "lightning-whisper-mlx": "lightning",
}


class LocalAdvancedUiState(NamedTuple):
    """Describe which expert controls should be shown for the current mode."""

    show_general: bool
    show_faster: bool
    show_lightning: bool
    guidance: str


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


def get_local_advanced_ui_state(
    mode: str | None,
    backend: str | None,
) -> LocalAdvancedUiState:
    """Return visibility + guidance for advanced local settings surfaces."""
    normalized_mode = (mode or "").strip().lower()
    normalized_backend = normalize_local_backend(backend)

    if normalized_mode != "local":
        return LocalAdvancedUiState(
            show_general=False,
            show_faster=False,
            show_lightning=False,
            guidance=(
                "Advanced local controls only apply in Local Whisper mode. "
                "Switch Providers → Mode to Local Whisper if you want to tune offline dictation."
            ),
        )

    if normalized_backend == "faster":
        return LocalAdvancedUiState(
            show_general=True,
            show_faster=True,
            show_lightning=False,
            guidance=(
                "Faster-Whisper is selected. Leave expert overrides on default unless "
                "you need to tune performance or compatibility."
            ),
        )

    if normalized_backend == "lightning":
        return LocalAdvancedUiState(
            show_general=True,
            show_faster=False,
            show_lightning=True,
            guidance=(
                "Lightning is selected. Adjust batch size or quantization only when "
                "you need to trade memory usage against speed."
            ),
        )

    if normalized_backend == "mlx":
        return LocalAdvancedUiState(
            show_general=True,
            show_faster=False,
            show_lightning=False,
            guidance=(
                "MLX is selected. General local overrides stay available; "
                "Faster-Whisper and Lightning-only controls stay hidden."
            ),
        )

    if normalized_backend == "whisper":
        return LocalAdvancedUiState(
            show_general=True,
            show_faster=False,
            show_lightning=False,
            guidance=(
                "OpenAI Whisper is selected. General local overrides stay available; "
                "Faster-Whisper and Lightning-only controls stay hidden."
            ),
        )

    return LocalAdvancedUiState(
        show_general=True,
        show_faster=False,
        show_lightning=False,
        guidance=(
            "Auto backend keeps PulseScribe's recommended local defaults. "
            "Choose a specific backend to reveal its expert controls."
        ),
    )


def get_cpu_threads_limit(cpu_count: int | None = None) -> int:
    """Return an upper bound for CPU thread input in UI fields.

    The old hard limit of 32 blocked valid values on high-core systems.
    We keep 32 as minimum fallback and allow up to detected logical CPUs.
    """
    detected = cpu_count
    if detected is None or detected <= 0:
        detected = DEFAULT_CPU_THREADS_LIMIT
    return max(DEFAULT_CPU_THREADS_LIMIT, int(detected))


__all__ = [
    "DEFAULT_LOCAL_BACKEND",
    "VALID_LOCAL_BACKENDS",
    "DEFAULT_CPU_THREADS_LIMIT",
    "LOCAL_BACKEND_ALIASES",
    "LocalAdvancedUiState",
    "normalize_local_backend",
    "should_remove_local_backend_env",
    "get_local_advanced_ui_state",
    "get_cpu_threads_limit",
]
