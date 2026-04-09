"""OpenAI Whisper API Provider.

Nutzt die OpenAI Transcription API mit gpt-4o-transcribe oder whisper-1.
"""

import logging
import os
from pathlib import Path
from utils.timing import redacted_text_summary, timed_operation

from config import DEFAULT_API_MODEL
from ._client_cache import EnvClientCache
from ._language import normalize_auto_language

logger = logging.getLogger("pulsescribe.providers.openai")

_JSON_ONLY_MODELS = ("gpt-4o-transcribe", "gpt-4o-mini-transcribe")

_client_cache = EnvClientCache()


def _get_client():
    """Gibt OpenAI-Client Singleton zurück (Lazy Init)."""

    def _factory(api_key: str):
        from openai import OpenAI

        return OpenAI(api_key=api_key)

    return _client_cache.get(
        env_var="OPENAI_API_KEY",
        missing_error="OPENAI_API_KEY nicht gesetzt",
        create_client=_factory,
        logger=logger,
        client_label="OpenAI-Client",
    )


def _uses_json_only_response_format(model: str) -> bool:
    """Return True for OpenAI transcription models that only support JSON output."""
    normalized = (model or "").strip().lower()
    return normalized.startswith(_JSON_ONLY_MODELS)


def _resolve_api_response_format(model: str, requested_format: str) -> str:
    """Map CLI response formats to the actual API format for a given model."""
    normalized = (requested_format or "text").strip().lower() or "text"
    if not _uses_json_only_response_format(model):
        return normalized

    if normalized in {"text", "json"}:
        return "json"

    raise ValueError(
        f"OpenAI-Modell '{model}' unterstützt kein Ausgabeformat '{normalized}'. "
        "Für SRT/VTT bitte '--model whisper-1' verwenden."
    )


def _serialize_response(response, *, requested_format: str) -> str:
    """Convert SDK responses into stable CLI output."""
    normalized = (requested_format or "text").strip().lower() or "text"

    if isinstance(response, str):
        return response

    if normalized == "text":
        text = getattr(response, "text", None)
        if isinstance(text, str):
            return text

    model_dump_json = getattr(response, "model_dump_json", None)
    if callable(model_dump_json):
        serialized = model_dump_json(indent=2)
        return serialized if isinstance(serialized, str) else str(serialized)

    text = getattr(response, "text", None)
    if isinstance(text, str):
        return text

    return str(response)


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
        language = normalize_auto_language(language)
        api_response_format = _resolve_api_response_format(model, response_format)
        audio_kb = audio_path.stat().st_size // 1024

        logger.info(
            "OpenAI: %s, %sKB, lang=%s, format=%s",
            model,
            audio_kb,
            language or "auto",
            response_format,
        )

        client = _get_client()

        with timed_operation("OpenAI-Transkription", logger=logger, include_session=False):
            with audio_path.open("rb") as audio_file:
                params = {
                    "model": model,
                    "file": audio_file,
                    "response_format": api_response_format,
                }
                if language:
                    params["language"] = language
                response = client.audio.transcriptions.create(**params)

        result = _serialize_response(response, requested_format=response_format)

        logger.debug("Ergebnis: %s", redacted_text_summary(result))

        return result

    def supports_streaming(self) -> bool:
        """OpenAI API unterstützt kein Streaming."""
        return False


__all__ = ["OpenAIProvider"]
