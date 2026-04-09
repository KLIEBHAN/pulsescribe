"""Shared preset definitions and helpers.

Presets are used by the Settings UI and the onboarding wizard to apply a known-good
configuration quickly.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
import platform

from utils.local_backend import normalize_local_backend, should_remove_local_backend_env
from utils.preferences import update_env_settings

LOCAL_FP16_ENV_KEY = "PULSESCRIBE_FP16"
LEGACY_LOCAL_FP16_ENV_KEY = "PULSESCRIBE_LOCAL_FP16"
DEFAULT_CPU_LOCAL_PRESET_NAME = "CPU: faster int8 (turbo)"
APPLE_SILICON_FAST_LOCAL_PRESET_NAME = "macOS: MLX Fast (turbo)"
APPLE_SILICON_PRIVATE_LOCAL_PRESET_NAME = "macOS: MLX Balanced (large)"

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
    APPLE_SILICON_PRIVATE_LOCAL_PRESET_NAME: {
        "local_backend": "mlx",
        "local_model": "large",
        "local_fast": "true",
    },
    APPLE_SILICON_FAST_LOCAL_PRESET_NAME: {
        "local_backend": "mlx",
        "local_model": "turbo",
        "local_fast": "true",
    },
    "macOS: Lightning Fast (large-v3)": {
        "local_backend": "lightning",
        "local_model": "large-v3",
        "local_fast": "true",
    },
    DEFAULT_CPU_LOCAL_PRESET_NAME: {
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

EnvOverrideNormalizer = Callable[[str | None], str | None]


@dataclass(frozen=True)
class EnvOverrideSpec:
    env_key: str
    preset_key: str
    normalizer: EnvOverrideNormalizer

    def apply(self, values: dict[str, str]) -> tuple[str, str | None]:
        return self.env_key, self.normalizer(values.get(self.preset_key))


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


_ENV_OVERRIDE_SPECS: tuple[EnvOverrideSpec, ...] = (
    EnvOverrideSpec(
        "PULSESCRIBE_LOCAL_BACKEND",
        "local_backend",
        _normalize_local_backend_override,
    ),
    EnvOverrideSpec(
        "PULSESCRIBE_LOCAL_MODEL",
        "local_model",
        partial(_normalize_lower_override, remove_when={"default"}),
    ),
    EnvOverrideSpec(
        "PULSESCRIBE_DEVICE",
        "device",
        partial(_normalize_lower_override, remove_when={"auto"}),
    ),
    EnvOverrideSpec(
        "PULSESCRIBE_LOCAL_WARMUP",
        "warmup",
        partial(_normalize_lower_override, remove_when={"auto"}),
    ),
    EnvOverrideSpec(
        "PULSESCRIBE_LOCAL_FAST",
        "local_fast",
        partial(_normalize_lower_override, remove_when={"default"}),
    ),
    EnvOverrideSpec(
        LOCAL_FP16_ENV_KEY,
        "fp16",
        partial(_normalize_lower_override, remove_when={"default"}),
    ),
    EnvOverrideSpec(
        "PULSESCRIBE_LOCAL_BEAM_SIZE",
        "beam_size",
        _normalize_optional_str,
    ),
    EnvOverrideSpec(
        "PULSESCRIBE_LOCAL_BEST_OF",
        "best_of",
        _normalize_optional_str,
    ),
    EnvOverrideSpec(
        "PULSESCRIBE_LOCAL_TEMPERATURE",
        "temperature",
        _normalize_optional_str,
    ),
    EnvOverrideSpec(
        "PULSESCRIBE_LOCAL_COMPUTE_TYPE",
        "compute_type",
        _normalize_optional_str,
    ),
    EnvOverrideSpec(
        "PULSESCRIBE_LOCAL_CPU_THREADS",
        "cpu_threads",
        _normalize_optional_str,
    ),
    EnvOverrideSpec(
        "PULSESCRIBE_LOCAL_NUM_WORKERS",
        "num_workers",
        _normalize_optional_str,
    ),
    EnvOverrideSpec(
        "PULSESCRIBE_LOCAL_WITHOUT_TIMESTAMPS",
        "without_timestamps",
        partial(_normalize_lower_override, remove_when={"default"}),
    ),
    EnvOverrideSpec(
        "PULSESCRIBE_LOCAL_VAD_FILTER",
        "vad_filter",
        partial(_normalize_lower_override, remove_when={"default"}),
    ),
    EnvOverrideSpec(
        "PULSESCRIBE_LIGHTNING_BATCH_SIZE",
        "lightning_batch_size",
        _normalize_lightning_batch_size,
    ),
    EnvOverrideSpec(
        "PULSESCRIBE_LIGHTNING_QUANT",
        "lightning_quant",
        _normalize_lightning_quant,
    ),
)


def _apply_env_override_specs(
    env_updates: dict[str, str | None],
    values: dict[str, str],
) -> None:
    for spec in _ENV_OVERRIDE_SPECS:
        env_key, normalized_value = spec.apply(values)
        env_updates[env_key] = normalized_value


def _build_local_preset_env_updates(
    preset_values: dict[str, str],
) -> dict[str, str | None]:
    values = _merge_local_preset_values(preset_values)
    env_updates: dict[str, str | None] = {
        "PULSESCRIBE_MODE": "local",
        LEGACY_LOCAL_FP16_ENV_KEY: None,
    }
    _apply_env_override_specs(env_updates, values)
    return env_updates


def is_apple_silicon() -> bool:
    try:
        return platform.system().lower() == "darwin" and platform.machine().lower() in (
            "arm64",
            "aarch64",
        )
    except Exception:
        return False


def _default_local_preset_for_platform(apple_silicon_preset: str) -> str:
    if is_apple_silicon():
        return apple_silicon_preset
    return DEFAULT_CPU_LOCAL_PRESET_NAME


def default_local_preset_fast() -> str:
    return _default_local_preset_for_platform(APPLE_SILICON_FAST_LOCAL_PRESET_NAME)


def default_local_preset_private() -> str:
    return _default_local_preset_for_platform(APPLE_SILICON_PRIVATE_LOCAL_PRESET_NAME)


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

    update_env_settings(_build_local_preset_env_updates(preset_values))
    return True
