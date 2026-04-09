"""Groq Whisper Provider.

Nutzt Groq's LPU-Chips für extrem schnelle Whisper-Inferenz (~300x Echtzeit).
"""

import logging
from pathlib import Path
from utils.timing import redacted_text_summary, timed_operation

from config import DEFAULT_GROQ_MODEL
from ._client_cache import EnvClientCache, build_cached_env_client_getter
from ._transcription_request import resolve_transcription_request
from .base import EnvValidatedProvider

logger = logging.getLogger("pulsescribe.providers.groq")

_client_cache = EnvClientCache()


_get_client = build_cached_env_client_getter(
    cache=_client_cache,
    env_var="GROQ_API_KEY",
    missing_error="GROQ_API_KEY nicht gesetzt",
    dependency_module="groq",
    dependency_class="Groq",
    logger=logger,
    client_label="Groq-Client",
)


class GroqProvider(EnvValidatedProvider):
    """Groq Whisper Provider.

    Nutzt LPU-Chips für ~300x Echtzeit Whisper-Inferenz
    bei gleicher Qualität wie OpenAI.

    Unterstützt:
        - whisper-large-v3 (beste Qualität)
        - distil-whisper-large-v3-en (nur Englisch, schneller)
    """

    name = "groq"
    default_model = DEFAULT_GROQ_MODEL
    api_key_env_var = "GROQ_API_KEY"
    missing_api_key_message = (
        "GROQ_API_KEY nicht gesetzt. "
        "Registrierung unter https://console.groq.com (kostenlose Credits)"
    )

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

        request = resolve_transcription_request(
            audio_path,
            model=model,
            default_model=self.default_model,
            language=language,
        )

        logger.info(
            f"Groq: {request.model}, {request.audio_kb}KB, "
            f"lang={request.language or 'auto'}"
        )

        client = _get_client()

        with timed_operation("Groq-Transkription", logger=logger, include_session=False):
            with audio_path.open("rb") as audio_file:
                params = {
                    # File-Handle statt .read() – spart Speicher bei großen Dateien
                    "file": (audio_path.name, audio_file),
                    "model": request.model,
                    "response_format": "text",
                    "temperature": 0.0,  # Konsistente Ergebnisse ohne Kreativität
                }
                if request.language:
                    params["language"] = request.language
                response = client.audio.transcriptions.create(**params)

        # Groq gibt bei response_format="text" String zurück
        if isinstance(response, str):
            result = response
        elif hasattr(response, "text"):
            result = response.text
        else:
            raise TypeError(f"Unerwarteter Groq-Response-Typ: {type(response)}")

        logger.debug("Ergebnis: %s", redacted_text_summary(result))

        return result



__all__ = ["GroqProvider"]
