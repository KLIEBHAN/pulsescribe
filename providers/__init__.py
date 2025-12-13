"""Transkriptions-Provider für whisper_go.

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

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .base import TranscriptionProvider

# Defaults zentral in config.py halten (vermeidet Drift)
from config import (
    DEFAULT_API_MODEL,
    DEFAULT_DEEPGRAM_MODEL,
    DEFAULT_GROQ_MODEL,
    DEFAULT_LOCAL_MODEL,
)

# Default-Modelle pro Provider
DEFAULT_MODELS = {
    "openai": DEFAULT_API_MODEL,
    "deepgram": DEFAULT_DEEPGRAM_MODEL,
    "deepgram_stream": DEFAULT_DEEPGRAM_MODEL,
    "groq": DEFAULT_GROQ_MODEL,
    "local": DEFAULT_LOCAL_MODEL,
}


def get_provider(mode: str) -> "TranscriptionProvider":
    """Factory für Transkriptions-Provider.

    Args:
        mode: Provider-Name ('openai', 'deepgram', 'deepgram_stream', 'groq', 'local')

    Returns:
        TranscriptionProvider-Implementierung

    Raises:
        ValueError: Bei unbekanntem Provider
    """
    if mode == "openai":
        from .openai import OpenAIProvider
        return OpenAIProvider()
    elif mode == "deepgram":
        from .deepgram import DeepgramProvider
        return DeepgramProvider()
    elif mode == "deepgram_stream":
        from .deepgram_stream import DeepgramStreamProvider
        return DeepgramStreamProvider()
    elif mode == "groq":
        from .groq import GroqProvider
        return GroqProvider()
    elif mode == "local":
        from .local import LocalProvider
        return LocalProvider()
    else:
        raise ValueError(f"Unbekannter Provider: {mode}")


def get_default_model(mode: str) -> str:
    """Gibt das Default-Modell für einen Provider zurück."""
    return DEFAULT_MODELS.get(mode, "whisper-1")


__all__ = [
    "get_provider",
    "get_default_model",
    "DEFAULT_MODELS",
]
