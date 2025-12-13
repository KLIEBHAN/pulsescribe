"""Groq Whisper Provider.

Nutzt Groq's LPU-Chips für extrem schnelle Whisper-Inferenz (~300x Echtzeit).
"""

import logging
import os
from pathlib import Path
from utils.timing import timed_operation

from config import DEFAULT_GROQ_MODEL

logger = logging.getLogger("whisper_go.providers.groq")

# Singleton Client
_client = None


def _get_client():
    """Gibt Groq-Client Singleton zurück (Lazy Init)."""
    global _client
    if _client is None:
        from groq import Groq

        api_key = os.getenv("GROQ_API_KEY")
        if not api_key:
            raise ValueError("GROQ_API_KEY nicht gesetzt")
        _client = Groq(api_key=api_key)
        logger.debug("Groq-Client initialisiert")
    return _client


class GroqProvider:
    """Groq Whisper Provider.

    Nutzt LPU-Chips für ~300x Echtzeit Whisper-Inferenz
    bei gleicher Qualität wie OpenAI.

    Unterstützt:
        - whisper-large-v3 (beste Qualität)
        - distil-whisper-large-v3-en (nur Englisch, schneller)
    """

    name = "groq"
    default_model = DEFAULT_GROQ_MODEL

    def __init__(self) -> None:
        self._validated = False

    def _validate(self) -> None:
        """Prüft ob API-Key gesetzt ist."""
        if self._validated:
            return
        if not os.getenv("GROQ_API_KEY"):
            raise ValueError(
                "GROQ_API_KEY nicht gesetzt. "
                "Registrierung unter https://console.groq.com (kostenlose Credits)"
            )
        self._validated = True

    def transcribe(
        self,
        audio_path: Path,
        model: str | None = None,
        language: str | None = None,
    ) -> str:
        """Transkribiert Audio über Groq API.

        Args:
            audio_path: Pfad zur Audio-Datei
            model: Modell (default: whisper-large-v3)
            language: Sprachcode oder None für Auto-Detection

        Returns:
            Transkribierter Text
        """
        self._validate()

        model = model or self.default_model
        audio_kb = audio_path.stat().st_size // 1024

        logger.info(f"Groq: {model}, {audio_kb}KB, lang={language or 'auto'}")

        client = _get_client()

        with timed_operation("Groq-Transkription", logger=logger, include_session=False):
            with audio_path.open("rb") as audio_file:
                params = {
                    # File-Handle statt .read() – spart Speicher bei großen Dateien
                    "file": (audio_path.name, audio_file),
                    "model": model,
                    "response_format": "text",
                    "temperature": 0.0,  # Konsistente Ergebnisse ohne Kreativität
                }
                if language:
                    params["language"] = language
                response = client.audio.transcriptions.create(**params)

        # Groq gibt bei response_format="text" String zurück
        if isinstance(response, str):
            result = response
        elif hasattr(response, "text"):
            result = response.text
        else:
            raise TypeError(f"Unerwarteter Groq-Response-Typ: {type(response)}")

        logger.debug(f"Ergebnis: {result[:100]}..." if len(result) > 100 else f"Ergebnis: {result}")

        return result

    def supports_streaming(self) -> bool:
        """Groq API unterstützt kein Streaming."""
        return False


__all__ = ["GroqProvider"]
