"""Deepgram Nova-3 Provider (REST API).

Nutzt Deepgram's REST API für Transkription.
Für Streaming siehe deepgram_stream.py.
"""

import logging
import os
from pathlib import Path
from utils.timing import redacted_text_summary, timed_operation
from utils.vocabulary import load_vocabulary

from config import DEFAULT_DEEPGRAM_MODEL
from ._client_cache import EnvClientCache
from ._language import normalize_auto_language

logger = logging.getLogger("pulsescribe.providers.deepgram")
_UPLOAD_CHUNK_SIZE = 1024 * 1024
_MAX_KEYWORDS = 100

_client_cache = EnvClientCache()


def _get_client():
    """Gibt Deepgram-Client Singleton zurück (Lazy Init)."""

    def _factory(api_key: str):
        from deepgram import DeepgramClient

        return DeepgramClient(api_key=api_key)

    return _client_cache.get(
        env_var="DEEPGRAM_API_KEY",
        missing_error="DEEPGRAM_API_KEY nicht gesetzt",
        create_client=_factory,
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
    request_params: dict[str, object] = {
        "request": _iter_audio_chunks(audio_path),
        "model": model,
        "smart_format": True,
        "punctuate": True,
        **_build_vocabulary_params(model, keywords),
    }
    if language:
        request_params["language"] = language
    return request_params


def _extract_transcript(response) -> str:
    """Extract transcript text from a Deepgram REST response."""
    channels = getattr(getattr(response, "results", None), "channels", [])
    if not channels:
        logger.warning("Deepgram-Antwort enthält keine Transkription")
        return ""

    alternatives = getattr(channels[0], "alternatives", [])
    if not alternatives:
        logger.warning("Deepgram-Antwort enthält keine Transkription")
        return ""

    return getattr(alternatives[0], "transcript", "") or ""


class DeepgramProvider:
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

    def __init__(self) -> None:
        self._validated = False

    def _validate(self) -> None:
        """Prüft ob API-Key gesetzt ist."""
        if self._validated:
            return
        if not os.getenv("DEEPGRAM_API_KEY"):
            raise ValueError(
                "DEEPGRAM_API_KEY nicht gesetzt. "
                "Registrierung unter https://console.deepgram.com (200$ Startguthaben)"
            )
        self._validated = True

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

        model = model or self.default_model
        language = normalize_auto_language(language)
        audio_kb = audio_path.stat().st_size // 1024

        keywords = _load_keywords()

        logger.info(
            f"Deepgram: {model}, {audio_kb}KB, lang={language or 'auto'}, "
            f"vocab={len(keywords)}"
        )

        client = _get_client()
        request_params = _build_request_params(
            audio_path,
            model=model,
            language=language,
            keywords=keywords,
        )

        with timed_operation("Deepgram-Transkription", logger=logger, include_session=False):
            response = client.listen.v1.media.transcribe_file(**request_params)

        result = _extract_transcript(response)

        logger.debug("Ergebnis: %s", redacted_text_summary(result))

        return result

    def supports_streaming(self) -> bool:
        """REST API unterstützt kein Streaming (siehe DeepgramStreamProvider)."""
        return False


__all__ = ["DeepgramProvider"]
