"""Deepgram Nova-3 Provider (REST API).

Nutzt Deepgram's REST API für Transkription.
Für Streaming siehe deepgram_stream.py.
"""

import logging
from pathlib import Path
from utils.timing import timed_operation
from utils.vocabulary import load_vocabulary

from config import DEFAULT_DEEPGRAM_MODEL
from ._client_cache import EnvClientCache, build_cached_env_client_getter
from ._response_utils import log_transcription_result
from ._transcription_request import (
    build_transcription_params,
    resolve_transcription_request,
)
from .base import EnvValidatedProvider

logger = logging.getLogger("pulsescribe.providers.deepgram")
_UPLOAD_CHUNK_SIZE = 1024 * 1024
_MAX_KEYWORDS = 100

_client_cache = EnvClientCache()


_get_client = build_cached_env_client_getter(
    cache=_client_cache,
    env_var="DEEPGRAM_API_KEY",
    missing_error="DEEPGRAM_API_KEY nicht gesetzt",
    dependency_module="deepgram",
    dependency_class="DeepgramClient",
    logger=logger,
    client_label="Deepgram-Client",
)


def _iter_audio_chunks(audio_path: Path, *, chunk_size: int = _UPLOAD_CHUNK_SIZE):
    """Yield an audio file in chunks so REST uploads do not require a full RAM copy."""
    with audio_path.open("rb") as audio_file:
        while True:
            chunk = audio_file.read(chunk_size)
            if not chunk:
                break
            yield chunk


def _load_keywords(max_keywords: int = _MAX_KEYWORDS) -> list[str]:
    """Load and cap normalized vocabulary keywords for Deepgram requests."""
    raw_keywords = load_vocabulary().get("keywords", [])
    if not isinstance(raw_keywords, list):
        return []
    return raw_keywords[:max_keywords]


def _build_vocabulary_params(model: str, keywords: list[str]) -> dict[str, list[str]]:
    """Map normalized keywords to the Deepgram model-specific API field."""
    if not keywords:
        return {}
    if model.startswith("nova-3"):
        return {"keyterm": list(keywords)}
    return {"keywords": list(keywords)}


def _build_request_params(
    audio_path: Path,
    *,
    model: str,
    language: str | None,
    keywords: list[str],
) -> dict[str, object]:
    """Build a Deepgram REST request without mixing I/O, vocab and response logic."""
    return build_transcription_params(
        model=model,
        language=language,
        extra_params={
            "request": _iter_audio_chunks(audio_path),
            "smart_format": True,
            "punctuate": True,
            **_build_vocabulary_params(model, keywords),
        },
    )


def _get_first_transcript_alternative(response):
    """Return the first Deepgram transcript alternative when present."""
    channels = getattr(getattr(response, "results", None), "channels", [])
    if not channels:
        return None

    alternatives = getattr(channels[0], "alternatives", [])
    if not alternatives:
        return None

    return alternatives[0]


def _extract_transcript(response) -> str:
    """Extract transcript text from a Deepgram REST response."""
    alternative = _get_first_transcript_alternative(response)
    if alternative is None:
        logger.warning("Deepgram-Antwort enthält keine Transkription")
        return ""
    return getattr(alternative, "transcript", "") or ""


class DeepgramProvider(EnvValidatedProvider):
    """Deepgram REST API Provider.

    Unterstützt:
        - nova-3 (neuestes Modell, beste Qualität)
        - nova-2 (bewährt, günstiger)

    Features:
        - smart_format: Automatische Formatierung
        - Custom Vocabulary via keyterm/keywords
    """

    name = "deepgram"
    default_model = DEFAULT_DEEPGRAM_MODEL
    api_key_env_var = "DEEPGRAM_API_KEY"
    missing_api_key_message = (
        "DEEPGRAM_API_KEY nicht gesetzt. "
        "Registrierung unter https://console.deepgram.com (200$ Startguthaben)"
    )

    def transcribe(
        self,
        audio_path: Path,
        model: str | None = None,
        language: str | None = None,
    ) -> str:
        """Transkribiert Audio über Deepgram REST API.

        Args:
            audio_path: Pfad zur Audio-Datei
            model: Modell (default: nova-3)
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

        keywords = _load_keywords()

        logger.info(
            f"Deepgram: {request.model}, {request.audio_kb}KB, "
            f"lang={request.language or 'auto'}, vocab={len(keywords)}"
        )

        client = _get_client()
        request_params = _build_request_params(
            audio_path,
            model=request.model,
            language=request.language,
            keywords=keywords,
        )

        with timed_operation("Deepgram-Transkription", logger=logger, include_session=False):
            response = client.listen.v1.media.transcribe_file(**request_params)

        result = _extract_transcript(response)

        log_transcription_result(logger, result)

        return result



__all__ = ["DeepgramProvider"]
