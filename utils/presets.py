"""Shared preset definitions and helpers.

Presets are used by the Settings UI and the onboarding wizard to apply a known-good
configuration quickly.
"""

from __future__ import annotations

import platform

from utils.preferences import update_env_settings
from utils.settings_env_updates import SettingsEnvUpdateBuilder

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


def _build_local_preset_env_updates(
    preset_values: dict[str, str],
) -> dict[str, str | None]:
    """Normalize preset strings like settings, without validating numeric text."""
    values = {**LOCAL_PRESET_BASE, **preset_values}
    builder = SettingsEnvUpdateBuilder()
    builder.set_present("PULSESCRIBE_MODE", "local")
    builder.remove_key(LEGACY_LOCAL_FP16_ENV_KEY)
    builder.set_local_backend("PULSESCRIBE_LOCAL_BACKEND", values.get("local_backend"))

    for env_key, preset_key, default in (
        ("PULSESCRIBE_LOCAL_MODEL", "local_model", "default"),
        ("PULSESCRIBE_DEVICE", "device", "auto"),
        ("PULSESCRIBE_LOCAL_WARMUP", "warmup", "auto"),
        ("PULSESCRIBE_LOCAL_FAST", "local_fast", "default"),
        (LOCAL_FP16_ENV_KEY, "fp16", "default"),
    ):
        builder.set_optional(
            env_key, values.get(preset_key), remove_when={default}, lower=True
        )

    for env_key, preset_key in (
        ("PULSESCRIBE_LOCAL_BEAM_SIZE", "beam_size"),
        ("PULSESCRIBE_LOCAL_BEST_OF", "best_of"),
        ("PULSESCRIBE_LOCAL_TEMPERATURE", "temperature"),
        ("PULSESCRIBE_LOCAL_COMPUTE_TYPE", "compute_type"),
        ("PULSESCRIBE_LOCAL_CPU_THREADS", "cpu_threads"),
        ("PULSESCRIBE_LOCAL_NUM_WORKERS", "num_workers"),
    ):
        builder.set_optional(env_key, values.get(preset_key))

    for env_key, preset_key in (
        ("PULSESCRIBE_LOCAL_WITHOUT_TIMESTAMPS", "without_timestamps"),
        ("PULSESCRIBE_LOCAL_VAD_FILTER", "vad_filter"),
    ):
        builder.set_optional(
            env_key, values.get(preset_key), remove_when={"default"}, lower=True
        )

    builder.set_optional(
        "PULSESCRIBE_LIGHTNING_BATCH_SIZE",
        values.get("lightning_batch_size"),
        remove_when={"12"},
    )
    builder.set_optional(
        "PULSESCRIBE_LIGHTNING_QUANT",
        values.get("lightning_quant"),
        remove_when={"none"},
        lower=True,
    )
    return builder.build()


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


def apply_local_preset_to_env(preset_name: str) -> bool:
    """Applies a local preset directly to `.env` via preferences helpers."""
    preset_values = LOCAL_PRESETS.get(preset_name)
    if not preset_values:
        return False

    update_env_settings(_build_local_preset_env_updates(preset_values))
    return True
