"""Transkriptions-Provider für PulseScribe.

Dieses Modul stellt ein einheitliches Interface für alle Transkriptions-Provider bereit.

Usage:
    from providers import get_provider

    provider = get_provider("deepgram")
    text = provider.transcribe(audio_path, language="de")

Unterstützte Provider:
    - openai: OpenAI Whisper API (gpt-4o-transcribe)
    - deepgram: Deepgram Nova-3 (REST API)
    - deepgram_stream: Deepgram WebSocket Streaming
    - groq: Groq Whisper auf LPU
    - local: Lokales Whisper-Modell
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING

# Defaults zentral in config.py halten (vermeidet Drift)
from config import (
    DEFAULT_API_MODEL,
    DEFAULT_DEEPGRAM_MODEL,
    DEFAULT_GROQ_MODEL,
    DEFAULT_LOCAL_MODEL,
)

if TYPE_CHECKING:
    from .base import TranscriptionProvider


@dataclass(frozen=True)
class _ProviderSpec:
    module_name: str
    class_name: str
    default_model: str

_UNKNOWN_PROVIDER_DEFAULT_MODEL = "whisper-1"

_PROVIDER_SPECS: dict[str, _ProviderSpec] = {
    "openai": _ProviderSpec("openai", "OpenAIProvider", DEFAULT_API_MODEL),
    "deepgram": _ProviderSpec("deepgram", "DeepgramProvider", DEFAULT_DEEPGRAM_MODEL),
    "deepgram_stream": _ProviderSpec(
        "deepgram_stream",
        "DeepgramStreamProvider",
        DEFAULT_DEEPGRAM_MODEL,
    ),
    "groq": _ProviderSpec("groq", "GroqProvider", DEFAULT_GROQ_MODEL),
    "local": _ProviderSpec("local", "LocalProvider", DEFAULT_LOCAL_MODEL),
}

# Default-Modelle pro Provider
DEFAULT_MODELS = {
    mode: spec.default_model for mode, spec in _PROVIDER_SPECS.items()
}


def _raise_local_mode_unavailable(exc: ImportError) -> None:
    raise ValueError(
        "Lokaler Modus nicht verfügbar. "
        "Dies ist wahrscheinlich ein Slim-Build ohne lokale Whisper-Backends. "
        "Verwende einen Cloud-Provider (deepgram, openai, groq) oder installiere "
        "den Full-Build mit lokalen Backends.\n"
        f"Original-Fehler: {exc}"
    ) from None


def _get_provider_spec(mode: str) -> _ProviderSpec:
    try:
        return _PROVIDER_SPECS[mode]
    except KeyError:
        raise ValueError(f"Unbekannter Provider: {mode}") from None


def _load_provider_class(mode: str):
    spec = _get_provider_spec(mode)
    try:
        module = __import__(spec.module_name, globals(), locals(), [spec.class_name], 1)
    except ImportError as exc:
        if mode == "local":
            _raise_local_mode_unavailable(exc)
        raise
    return getattr(module, spec.class_name)


def get_provider(mode: str) -> "TranscriptionProvider":
    """Factory für Transkriptions-Provider.

    Args:
        mode: Provider-Name ('openai', 'deepgram', 'deepgram_stream', 'groq', 'local')

    Returns:
        TranscriptionProvider-Implementierung

    Raises:
        ValueError: Bei unbekanntem Provider
    """
    provider_class = _load_provider_class(mode)
    return provider_class()


def get_default_model(mode: str) -> str:
    """Gibt das Default-Modell für einen Provider zurück."""
    spec = _PROVIDER_SPECS.get(mode)
    if spec is None:
        return _UNKNOWN_PROVIDER_DEFAULT_MODEL
    return spec.default_model


__all__ = [
    "get_provider",
    "get_default_model",
    "DEFAULT_MODELS",
]
