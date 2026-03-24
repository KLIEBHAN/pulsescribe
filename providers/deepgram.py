"""Deepgram Nova-3 Provider (REST API).

Nutzt Deepgram's REST API für Transkription.
Für Streaming siehe deepgram_stream.py.
"""

import logging
import os
import threading
from pathlib import Path
from utils.timing import timed_operation
from utils.vocabulary import load_vocabulary

from config import DEFAULT_DEEPGRAM_MODEL
from ._language import normalize_auto_language

logger = logging.getLogger("pulsescribe.providers.deepgram")
_UPLOAD_CHUNK_SIZE = 1024 * 1024

# Singleton Client
_client = None
_client_signature: str | None = None
_client_lock = threading.Lock()


def _get_client():
    """Gibt Deepgram-Client Singleton zurück (Lazy Init)."""
    global _client, _client_signature

    api_key = os.getenv("DEEPGRAM_API_KEY")
    if not api_key:
        _client = None
        _client_signature = None
        raise ValueError("DEEPGRAM_API_KEY nicht gesetzt")

    if _client is None or _client_signature != api_key:
        with _client_lock:
            api_key = os.getenv("DEEPGRAM_API_KEY")
            if not api_key:
                _client = None
                _client_signature = None
                raise ValueError("DEEPGRAM_API_KEY nicht gesetzt")
            if _client is None or _client_signature != api_key:
                from deepgram import DeepgramClient

                _client = DeepgramClient(api_key=api_key)
                _client_signature = api_key
                logger.debug("Deepgram-Client initialisiert")
    return _client


def _iter_audio_chunks(audio_path: Path, *, chunk_size: int = _UPLOAD_CHUNK_SIZE):
    """Yield an audio file in chunks so REST uploads do not require a full RAM copy."""
    with audio_path.open("rb") as audio_file:
        while True:
            chunk = audio_file.read(chunk_size)
            if not chunk:
                break
            yield chunk




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

        # Vocabulary laden
        MAX_KEYWORDS = 100
        vocab = load_vocabulary()
        keywords = vocab.get("keywords", [])[:MAX_KEYWORDS]

        logger.info(
            f"Deepgram: {model}, {audio_kb}KB, lang={language or 'auto'}, "
            f"vocab={len(keywords)}"
        )

        client = _get_client()

        # Nova-3 nutzt 'keyterm', ältere Modelle nutzen 'keywords'
        is_nova3 = model.startswith("nova-3")
        vocab_params = {}
        if keywords:
            if is_nova3:
                vocab_params["keyterm"] = keywords
            else:
                vocab_params["keywords"] = keywords

        request_params = {
            "request": _iter_audio_chunks(audio_path),
            "model": model,
            "smart_format": True,
            "punctuate": True,
            **vocab_params,
        }
        if language:
            request_params["language"] = language

        with timed_operation("Deepgram-Transkription", logger=logger, include_session=False):
            response = client.listen.v1.media.transcribe_file(**request_params)

        # Sichere Extraktion: Prüfe auf leere channels/alternatives
        channels = getattr(response.results, "channels", [])
        if not channels or not getattr(channels[0], "alternatives", []):
            logger.warning("Deepgram-Antwort enthält keine Transkription")
            return ""
        result = channels[0].alternatives[0].transcript or ""

        logger.debug(f"Ergebnis: {result[:100]}..." if len(result) > 100 else f"Ergebnis: {result}")

        return result

    def supports_streaming(self) -> bool:
        """REST API unterstützt kein Streaming (siehe DeepgramStreamProvider)."""
        return False


__all__ = ["DeepgramProvider"]
