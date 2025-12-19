"""Shared preset definitions and helpers.

Presets are used by the Settings UI and the onboarding wizard to apply a known-good
configuration quickly.
"""

from __future__ import annotations

import platform

from utils.preferences import remove_env_setting, save_env_setting

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


def is_apple_silicon() -> bool:
    try:
        return platform.machine().lower() in ("arm64", "aarch64")
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


def apply_local_preset_to_env(preset_name: str) -> bool:
    """Applies a local preset directly to `.env` via preferences helpers."""
    preset_values = LOCAL_PRESETS.get(preset_name)
    if not preset_values:
        return False

    values = dict(LOCAL_PRESET_BASE)
    values.update(preset_values)

    def _set_or_remove(
        key: str, value: str | None, *, remove_when: set[str] | None = None
    ) -> None:
        if value is None:
            remove_env_setting(key)
            return
        normalized = str(value).strip().lower()
        if not normalized:
            remove_env_setting(key)
            return
        if remove_when and normalized in remove_when:
            remove_env_setting(key)
            return
        save_env_setting(key, normalized)

    # Ensure local mode when applying local presets.
    save_env_setting("PULSESCRIBE_MODE", "local")

    _set_or_remove(
        "PULSESCRIBE_LOCAL_BACKEND",
        values.get("local_backend"),
        remove_when={"whisper"},
    )
    _set_or_remove(
        "PULSESCRIBE_LOCAL_MODEL",
        values.get("local_model"),
        remove_when={"default"},
    )
    _set_or_remove(
        "PULSESCRIBE_DEVICE",
        values.get("device"),
        remove_when={"auto"},
    )
    _set_or_remove(
        "PULSESCRIBE_LOCAL_WARMUP",
        values.get("warmup"),
        remove_when={"auto"},
    )

    def _save_bool_override(key: str, raw: str | None) -> None:
        if raw is None:
            remove_env_setting(key)
            return
        normalized = str(raw).strip().lower()
        if not normalized or normalized == "default":
            remove_env_setting(key)
            return
        save_env_setting(key, normalized)

    _save_bool_override("PULSESCRIBE_LOCAL_FAST", values.get("local_fast"))
    _save_bool_override("PULSESCRIBE_FP16", values.get("fp16"))
    _save_bool_override(
        "PULSESCRIBE_LOCAL_WITHOUT_TIMESTAMPS", values.get("without_timestamps")
    )
    _save_bool_override("PULSESCRIBE_LOCAL_VAD_FILTER", values.get("vad_filter"))

    def _save_optional_str(key: str, raw: str | None) -> None:
        normalized = (raw or "").strip()
        if not normalized:
            remove_env_setting(key)
            return
        save_env_setting(key, normalized)

    _save_optional_str("PULSESCRIBE_LOCAL_BEAM_SIZE", values.get("beam_size"))
    _save_optional_str("PULSESCRIBE_LOCAL_BEST_OF", values.get("best_of"))
    _save_optional_str("PULSESCRIBE_LOCAL_TEMPERATURE", values.get("temperature"))
    _save_optional_str("PULSESCRIBE_LOCAL_COMPUTE_TYPE", values.get("compute_type"))
    _save_optional_str("PULSESCRIBE_LOCAL_CPU_THREADS", values.get("cpu_threads"))
    _save_optional_str("PULSESCRIBE_LOCAL_NUM_WORKERS", values.get("num_workers"))

    return True
