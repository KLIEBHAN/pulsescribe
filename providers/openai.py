"""OpenAI Whisper API Provider.

Nutzt die OpenAI Transcription API mit gpt-4o-transcribe oder whisper-1.
"""

import logging
from pathlib import Path
from utils.timing import timed_operation

from config import DEFAULT_API_MODEL
from ._client_cache import EnvClientCache, build_cached_env_client_getter
from ._response_utils import log_transcription_result, serialize_openai_response
from ._transcription_request import (
    build_transcription_params,
    execute_audio_file_request,
    resolve_transcription_request,
)
from .base import EnvValidatedProvider

logger = logging.getLogger("pulsescribe.providers.openai")

_JSON_ONLY_MODELS = ("gpt-4o-transcribe", "gpt-4o-mini-transcribe")

_client_cache = EnvClientCache()


_get_client = build_cached_env_client_getter(
    cache=_client_cache,
    env_var="OPENAI_API_KEY",
    missing_error="OPENAI_API_KEY nicht gesetzt",
    dependency_module="openai",
    dependency_class="OpenAI",
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


class OpenAIProvider(EnvValidatedProvider):
    """OpenAI Whisper API Provider.

    Unterstützt:
        - gpt-4o-transcribe (beste Qualität)
        - gpt-4o-mini-transcribe (schneller, günstiger)
        - whisper-1 (original Whisper)
    """

    name = "openai"
    default_model = DEFAULT_API_MODEL
    api_key_env_var = "OPENAI_API_KEY"
    missing_api_key_message = (
        "OPENAI_API_KEY nicht gesetzt. "
        "Bitte `export OPENAI_API_KEY='sk-...'` ausführen."
    )

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

        request = resolve_transcription_request(
            audio_path,
            model=model,
            default_model=self.default_model,
            language=language,
        )
        api_response_format = _resolve_api_response_format(
            request.model,
            response_format,
        )

        logger.info(
            "OpenAI: %s, %sKB, lang=%s, format=%s",
            request.model,
            request.audio_kb,
            request.language or "auto",
            response_format,
        )

        client = _get_client()

        with timed_operation("OpenAI-Transkription", logger=logger, include_session=False):
            response = execute_audio_file_request(
                audio_path,
                request_callable=client.audio.transcriptions.create,
                build_params=lambda audio_file: build_transcription_params(
                    model=request.model,
                    language=request.language,
                    extra_params={
                        "file": audio_file,
                        "response_format": api_response_format,
                    },
                ),
            )

        result = serialize_openai_response(
            response,
            requested_format=response_format,
        )

        log_transcription_result(logger, result)

        return result



__all__ = ["OpenAIProvider"]
