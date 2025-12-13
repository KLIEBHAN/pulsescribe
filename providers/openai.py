"""OpenAI Whisper API Provider.

Nutzt die OpenAI Transcription API mit gpt-4o-transcribe oder whisper-1.
"""

import logging
import os
from pathlib import Path
from utils.timing import timed_operation

from config import DEFAULT_API_MODEL

logger = logging.getLogger("whisper_go.providers.openai")

# Singleton Client
_client = None


def _get_client():
    """Gibt OpenAI-Client Singleton zurück (Lazy Init)."""
    global _client
    if _client is None:
        from openai import OpenAI
        _client = OpenAI()
        logger.debug("OpenAI-Client initialisiert")
    return _client


class OpenAIProvider:
    """OpenAI Whisper API Provider.

    Unterstützt:
        - gpt-4o-transcribe (beste Qualität)
        - gpt-4o-mini-transcribe (schneller, günstiger)
        - whisper-1 (original Whisper)
    """

    name = "openai"
    default_model = DEFAULT_API_MODEL

    def __init__(self) -> None:
        # API-Key Validierung beim ersten Aufruf
        self._validated = False

    def _validate(self) -> None:
        """Prüft ob API-Key gesetzt ist."""
        if self._validated:
            return
        if not os.getenv("OPENAI_API_KEY"):
            raise ValueError(
                "OPENAI_API_KEY nicht gesetzt. "
                "Bitte `export OPENAI_API_KEY='sk-...'` ausführen."
            )
        self._validated = True

    def transcribe(
        self,
        audio_path: Path,
        model: str | None = None,
        language: str | None = None,
        response_format: str = "text",
    ) -> str:
        """Transkribiert Audio über die OpenAI API.

        Args:
            audio_path: Pfad zur Audio-Datei
            model: Modell (default: gpt-4o-transcribe)
            language: Sprachcode oder None für Auto-Detection
            response_format: Output-Format (text, json, srt, vtt)

        Returns:
            Transkribierter Text
        """
        self._validate()

        model = model or self.default_model
        audio_kb = audio_path.stat().st_size // 1024

        logger.info(f"OpenAI: {model}, {audio_kb}KB, lang={language or 'auto'}")

        client = _get_client()

        with timed_operation("OpenAI-Transkription", logger=logger, include_session=False):
            with audio_path.open("rb") as audio_file:
                params = {
                    "model": model,
                    "file": audio_file,
                    "response_format": response_format,
                }
                if language:
                    params["language"] = language
                response = client.audio.transcriptions.create(**params)

        # API gibt bei format="text" String zurück, sonst Objekt
        if response_format == "text":
            result = response
        else:
            result = response.text if hasattr(response, "text") else str(response)

        logger.debug(f"Ergebnis: {result[:100]}..." if len(result) > 100 else f"Ergebnis: {result}")

        return result

    def supports_streaming(self) -> bool:
        """OpenAI API unterstützt kein Streaming."""
        return False


__all__ = ["OpenAIProvider"]
