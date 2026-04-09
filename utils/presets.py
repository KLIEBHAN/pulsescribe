"""Shared preset definitions and helpers.

Presets are used by the Settings UI and the onboarding wizard to apply a known-good
configuration quickly.
"""

from __future__ import annotations

import platform

from utils.local_backend import normalize_local_backend, should_remove_local_backend_env
from utils.preferences import update_env_settings

LOCAL_FP16_ENV_KEY = "PULSESCRIBE_FP16"
LEGACY_LOCAL_FP16_ENV_KEY = "PULSESCRIBE_LOCAL_FP16"

# Local presets (UI labels). Values are strings matching the Settings UI controls.
LOCAL_PRESET_BASE: dict[str, str] = {
    "device": "auto",
    "warmup": "auto",
    "local_fast": "default",
    "fp16": "default",
    "beam_size": "",
    "best_of": "",
    "temperature": "",
    "compute_type": "",
    "cpu_threads": "",
    "num_workers": "",
    "without_timestamps": "default",
    "vad_filter": "default",
    "lightning_batch_size": "12",
    "lightning_quant": "none",
}

LOCAL_PRESETS: dict[str, dict[str, str]] = {
    "macOS: MPS Balanced (turbo)": {
        "local_backend": "whisper",
        "local_model": "turbo",
    },
    "macOS: MPS Fast (turbo)": {
        "local_backend": "whisper",
        "local_model": "turbo",
        "local_fast": "true",
    },
    "macOS: MLX Balanced (large)": {
        "local_backend": "mlx",
        "local_model": "large",
        "local_fast": "true",
    },
    "macOS: MLX Fast (turbo)": {
        "local_backend": "mlx",
        "local_model": "turbo",
        "local_fast": "true",
    },
    "macOS: Lightning Fast (large-v3)": {
        "local_backend": "lightning",
        "local_model": "large-v3",
        "local_fast": "true",
    },
    "CPU: faster int8 (turbo)": {
        "local_backend": "faster",
        "local_model": "turbo",
        "device": "cpu",
        "warmup": "false",
        "local_fast": "true",
        "compute_type": "int8",
        "cpu_threads": "0",
        "num_workers": "1",
        "without_timestamps": "true",
        "vad_filter": "true",
    },
}

LOCAL_PRESET_OPTIONS = ["(none)", *LOCAL_PRESETS.keys()]

_LOWER_OVERRIDE_FIELD_SPECS: tuple[tuple[str, str, set[str]], ...] = (
    ("PULSESCRIBE_LOCAL_MODEL", "local_model", {"default"}),
    ("PULSESCRIBE_DEVICE", "device", {"auto"}),
    ("PULSESCRIBE_LOCAL_WARMUP", "warmup", {"auto"}),
    ("PULSESCRIBE_LOCAL_FAST", "local_fast", {"default"}),
    (LOCAL_FP16_ENV_KEY, "fp16", {"default"}),
    (
        "PULSESCRIBE_LOCAL_WITHOUT_TIMESTAMPS",
        "without_timestamps",
        {"default"},
    ),
    ("PULSESCRIBE_LOCAL_VAD_FILTER", "vad_filter", {"default"}),
)
_OPTIONAL_STRING_FIELD_SPECS: tuple[tuple[str, str], ...] = (
    ("PULSESCRIBE_LOCAL_BEAM_SIZE", "beam_size"),
    ("PULSESCRIBE_LOCAL_BEST_OF", "best_of"),
    ("PULSESCRIBE_LOCAL_TEMPERATURE", "temperature"),
    ("PULSESCRIBE_LOCAL_COMPUTE_TYPE", "compute_type"),
    ("PULSESCRIBE_LOCAL_CPU_THREADS", "cpu_threads"),
    ("PULSESCRIBE_LOCAL_NUM_WORKERS", "num_workers"),
)


def _normalize_lower_value(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip().lower()
    return normalized or None


def _normalize_lower_override(
    value: str | None,
    *,
    remove_when: set[str] | None = None,
) -> str | None:
    normalized = _normalize_lower_value(value)
    if normalized is None:
        return None
    if remove_when and normalized in remove_when:
        return None
    return normalized


def _normalize_optional_str(value: str | None) -> str | None:
    normalized = (value or "").strip()
    return normalized or None


def _normalize_local_backend_override(value: str | None) -> str | None:
    backend = normalize_local_backend(value)
    if should_remove_local_backend_env(backend):
        return None
    return backend


def _normalize_lightning_batch_size(value: str | None) -> str | None:
    normalized = _normalize_optional_str(value)
    if not normalized or normalized == "12":
        return None
    return normalized


def _normalize_lightning_quant(value: str | None) -> str | None:
    return _normalize_lower_override(value, remove_when={"none"})


def _apply_lower_override_fields(
    env_updates: dict[str, str | None],
    values: dict[str, str],
) -> None:
    for env_key, preset_key, remove_when in _LOWER_OVERRIDE_FIELD_SPECS:
        env_updates[env_key] = _normalize_lower_override(
            values.get(preset_key),
            remove_when=remove_when,
        )


def _apply_optional_string_fields(
    env_updates: dict[str, str | None],
    values: dict[str, str],
) -> None:
    for env_key, preset_key in _OPTIONAL_STRING_FIELD_SPECS:
        env_updates[env_key] = _normalize_optional_str(values.get(preset_key))


def _build_local_preset_env_updates(values: dict[str, str]) -> dict[str, str | None]:
    env_updates: dict[str, str | None] = {
        "PULSESCRIBE_MODE": "local",
        LEGACY_LOCAL_FP16_ENV_KEY: None,
    }

    env_updates["PULSESCRIBE_LOCAL_BACKEND"] = _normalize_local_backend_override(
        values.get("local_backend")
    )
    _apply_lower_override_fields(env_updates, values)
    _apply_optional_string_fields(env_updates, values)
    env_updates["PULSESCRIBE_LIGHTNING_BATCH_SIZE"] = _normalize_lightning_batch_size(
        values.get("lightning_batch_size")
    )
    env_updates["PULSESCRIBE_LIGHTNING_QUANT"] = _normalize_lightning_quant(
        values.get("lightning_quant")
    )

    return env_updates


def is_apple_silicon() -> bool:
    try:
        return platform.system().lower() == "darwin" and platform.machine().lower() in (
            "arm64",
            "aarch64",
        )
    except Exception:
        return False


def default_local_preset_fast() -> str:
    return (
        "macOS: MLX Fast (turbo)" if is_apple_silicon() else "CPU: faster int8 (turbo)"
    )


def default_local_preset_private() -> str:
    return (
        "macOS: MLX Balanced (large)"
        if is_apple_silicon()
        else "CPU: faster int8 (turbo)"
    )


def _merge_local_preset_values(preset_values: dict[str, str]) -> dict[str, str]:
    """Overlay one preset on top of the shared local preset defaults."""
    values = dict(LOCAL_PRESET_BASE)
    values.update(preset_values)
    return values


def apply_local_preset_to_env(preset_name: str) -> bool:
    """Applies a local preset directly to `.env` via preferences helpers."""
    preset_values = LOCAL_PRESETS.get(preset_name)
    if not preset_values:
        return False

    update_env_settings(
        _build_local_preset_env_updates(_merge_local_preset_values(preset_values))
    )
    return True
