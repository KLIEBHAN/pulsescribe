"""Shared CLI type definitions for PulseScribe.

Enums and type aliases used by both transcribe.py and pulsescribe_daemon.py.
"""

from enum import Enum


class TranscriptionMode(str, Enum):
    """Transkriptions-Modi."""

    openai = "openai"
    local = "local"
    deepgram = "deepgram"
    groq = "groq"
    openai_compatible = "openai_compatible"


class Context(str, Enum):
    """Kontext-Typen fuer LLM-Nachbearbeitung."""

    email = "email"
    chat = "chat"
    code = "code"
    default = "default"


class RefineProvider(str, Enum):
    """LLM-Provider fuer Nachbearbeitung."""

    openai = "openai"
    openrouter = "openrouter"
    groq = "groq"
    gemini = "gemini"


class ResponseFormat(str, Enum):
    """Ausgabeformate (nur OpenAI)."""

    text = "text"
    json = "json"
    srt = "srt"
    vtt = "vtt"


class HotkeyMode(str, Enum):
    """Hotkey-Modi fuer Daemon."""

    toggle = "toggle"
    hold = "hold"
