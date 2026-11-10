"""Helpers for local-backend UI defaults, guidance, and persistence."""

from __future__ import annotations

import os

import importlib.util
import re
import sys
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


# Python module that implements each local backend.
LOCAL_BACKEND_MODULES = {
    "whisper": "whisper",
    "faster": "faster_whisper",
    "mlx": "mlx_whisper",
    "lightning": "lightning_whisper_mlx",
}


def is_frozen_app() -> bool:
    """True inside the packaged app (PyInstaller), where users cannot install backends."""
    return bool(getattr(sys, "frozen", False))


def is_local_backend_installed(backend: str) -> bool:
    """Check whether a backend's package is present, without importing it."""
    module = LOCAL_BACKEND_MODULES.get(backend)
    if module is None:
        return False
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def available_local_backend_options(options: list[str]) -> list[str]:
    """Backend choices for Settings: all in source installs, only bundled ones in the app."""
    if not is_frozen_app():
        return list(options)
    return [option for option in options if option == DEFAULT_LOCAL_BACKEND
            or is_local_backend_installed(option)]


_ALL_LOCAL_BACKENDS = frozenset({"whisper", "faster", "mlx", "lightning"})
_DECODING_BACKENDS = frozenset({"whisper", "faster", "mlx"})  # Lightning only reads the language

# Backends on which each advanced option takes effect (see providers/local.py).
ADVANCED_OPTION_BACKENDS: dict[str, frozenset[str]] = {
    "preset": _ALL_LOCAL_BACKENDS,
    "warmup": _ALL_LOCAL_BACKENDS,
    "idle_unload": _ALL_LOCAL_BACKENDS,  # macOS app only, see advanced_option_applies
    "live_preview": frozenset({"mlx"}),  # macOS app only; other backends are too slow
    "device": frozenset({"whisper", "faster"}),  # MLX and Lightning always run on Metal
    "fast": _DECODING_BACKENDS,
    "fp16": frozenset({"whisper", "mlx"}),  # faster-whisper uses its compute type instead
    "beam_size": frozenset({"whisper", "faster"}),  # MLX/Lightning have no beam search
    "best_of": _DECODING_BACKENDS,
    "temperature": _DECODING_BACKENDS,
    "compute_type": frozenset({"faster"}),
    "cpu_threads": frozenset({"faster"}),
    "num_workers": frozenset({"faster"}),
    "without_timestamps": frozenset({"faster"}),
    "vad_filter": frozenset({"faster"}),
    "lightning_batch": frozenset({"lightning"}),
    "lightning_quant": frozenset({"lightning"}),
}

# Tooltip text per advanced option, including what "default" means.
ADVANCED_OPTION_HELP: dict[str, str] = {
    "preset": "Applies a tested combination of backend, model and the options below.",
    "idle_unload": "Frees the model's memory after this long without dictation. The next "
                   "dictation loads it again while you speak. never: keep it loaded.",
    "live_preview": "Shows the recognized text in the overlay while you speak. The "
                    "transcript itself stays the same. off: no text until you stop. "
                    "Needs a fast model such as turbo; hidden for large models.",
    "warmup": "Runs a short silent transcription after loading so the first dictation is "
              "fast. auto: on for openai-whisper and faster-whisper.",
    "device": "Hardware for openai-whisper and faster-whisper. auto picks the GPU when it "
              "works (CUDA on Windows, MPS on a Mac with openai-whisper), else the CPU.",
    "fast": "Faster decoding: temperature 0, beam size 1 and no conditioning on the previous "
            "text (avoids repetition loops). default: on for faster-whisper, off otherwise.",
    "fp16": "Half precision. default: on for MLX, off for openai-whisper on MPS/CPU.",
    "beam_size": "Number of beams searched per step (1-20). Higher can be slightly more "
                 "accurate but slower. Empty: 1 with Fast on, otherwise the backend default.",
    "best_of": "Candidates sampled when temperature is above 0 (1-10). Empty: backend default.",
    "temperature": "0 = deterministic. A comma list (e.g. 0.0,0.2,0.4) retries with higher "
                   "values when a result looks wrong. Empty: backend default.",
    "compute_type": "CTranslate2 precision, e.g. int8 (CPU default), float16 (CUDA default) "
                    "or int8_float16.",
    "cpu_threads": "CPU threads for faster-whisper. 0 = automatic.",
    "num_workers": "Parallel transcriptions faster-whisper can run. 1 is enough for dictation.",
    "without_timestamps": "Skip timestamp tokens; slightly faster. default: on.",
    "vad_filter": "Drop silence with Silero VAD before transcribing. default: off.",
    "lightning_batch": "Audio chunks decoded together. Higher is faster but needs more memory.",
    "lightning_quant": "Lightning's pre-quantized weights. none keeps the best quality.",
}


def effective_local_backend(selected: str | None, *, platform: str | None = None) -> str:
    """Backend that runs for a Settings selection; mirrors providers.local for ``auto``."""
    backend = normalize_local_backend(selected)
    if backend != DEFAULT_LOCAL_BACKEND:
        return backend
    import platform as platform_module

    current = platform or sys.platform
    if current == "darwin" and platform_module.machine() == "arm64" \
            and is_local_backend_installed("mlx"):
        return "mlx"
    return "faster" if is_local_backend_installed("faster") else "whisper"


# Whisper models too slow for the live preview. Measured for large-v3 (MLX, 8 bit):
# a pass over 10 s of audio takes 0.95 s on an M6, passes on an M5 took 1.0 to
# 1.5 s; turbo takes 0.35 s on the M6. A pass has to stay well under a second.
LIVE_PREVIEW_SLOW_MODELS = frozenset({"large", "large-v1", "large-v2", "large-v3"})
# Short name inside a repository ID such as mlx-community/whisper-large-v3-mlx-8bit.
_MODEL_REPO_NAME = re.compile(r"^(?:.*/)?(?:whisper-)?(.+?)(?:-mlx)?(?:-(?:\d+bit|q\d+))?$")


def live_preview_supports_model(model: str | None) -> bool:
    """False for a local model (short name or repository ID) too slow for the live preview."""
    name = (model or "").strip().lower()
    match = _MODEL_REPO_NAME.match(name)
    return (match.group(1) if match else name) not in LIVE_PREVIEW_SLOW_MODELS


def live_preview_model_hint(model: str | None) -> str | None:
    """Settings text that explains the missing live preview for ``model``, else None."""
    if live_preview_supports_model(model):
        return None
    return (
        f"No live text with {(model or '').strip()}: this model is too slow to transcribe "
        "while you speak. Choose turbo to see your words in the overlay."
    )


def advanced_option_applies(option: str, backend: str, *, platform: str | None = None) -> bool:
    """True when ``option`` changes anything for ``backend`` on this platform."""
    backends = ADVANCED_OPTION_BACKENDS[option]
    current_platform = platform or sys.platform
    if option == "device" and current_platform == "darwin":
        backends = frozenset({"whisper"})  # faster-whisper has no GPU path on macOS
    if option in ("idle_unload", "live_preview") and current_platform != "darwin":
        return False  # only the macOS daemon unloads idle models and previews
    return backend in backends


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



_ADVANCED_INT_LABELS = {
    "beam_size": "Beam size",
    "best_of": "Best of",
    "cpu_threads": "CPU threads",
    "num_workers": "Workers",
}


def advanced_int_range(option: str, cpu_count: int | None = None) -> tuple[int, int]:
    """Accepted range of an integer advanced option (UI validators use the same bounds)."""
    if option == "cpu_threads":
        return 0, get_cpu_threads_limit(os.cpu_count() if cpu_count is None else cpu_count)
    return {"beam_size": (1, 20), "best_of": (1, 10), "num_workers": (1, 8)}[option]


def advanced_value_error(option: str, raw: str, cpu_count: int | None = None) -> str | None:
    """Return a user-facing message when a typed advanced value is invalid, else None.

    Empty means "default" and is always valid. Temperature takes one value or a
    comma-separated fallback list, each 0.0-1.0 with a dot as decimal separator.
    """
    value = (raw or "").strip()
    if not value:
        return None
    if option == "temperature":
        try:
            temperatures = [float(part) for part in value.split(",")]
        except ValueError:
            return "Temperature: use numbers like 0.0 or 0.0,0.2,0.4 (dot as decimal separator)."
        if not all(0.0 <= t <= 1.0 for t in temperatures):
            return "Temperature: each value must be between 0.0 and 1.0 (dot as decimal separator)."
        return None
    low, high = advanced_int_range(option, cpu_count)
    label = _ADVANCED_INT_LABELS[option]
    try:
        number = int(value)
    except ValueError:
        return f"{label}: enter a whole number from {low} to {high}, or leave it empty."
    if not low <= number <= high:
        return f"{label}: enter a whole number from {low} to {high}, or leave it empty."
    return None


def first_advanced_value_error(
    values: dict[str, str], backend: str, *, platform: str | None = None
) -> str | None:
    """First invalid typed value among options the backend uses; hidden ones never block saving."""
    for option, raw in values.items():
        if advanced_option_applies(option, backend, platform=platform):
            error = advanced_value_error(option, raw)
            if error:
                return error
    return None


__all__ = [
    "LIVE_PREVIEW_SLOW_MODELS",
    "live_preview_model_hint",
    "live_preview_supports_model",
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
