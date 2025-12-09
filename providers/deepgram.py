"""Deepgram Nova-3 Provider (REST API).

Nutzt Deepgram's REST API für Transkription.
Für Streaming siehe deepgram_stream.py.
"""

import json
import logging
import os
from contextlib import contextmanager
from pathlib import Path
import time

logger = logging.getLogger("whisper_go.providers.deepgram")

# Singleton Client
_client = None

# Vocabulary-Pfad
VOCABULARY_FILE = Path.home() / ".whisper_go" / "vocabulary.json"


def _get_client():
    """Gibt Deepgram-Client Singleton zurück (Lazy Init)."""
    global _client
    if _client is None:
        from deepgram import DeepgramClient

        api_key = os.getenv("DEEPGRAM_API_KEY")
        if not api_key:
            raise ValueError("DEEPGRAM_API_KEY nicht gesetzt")
        _client = DeepgramClient(api_key=api_key)
        logger.debug("Deepgram-Client initialisiert")
    return _client


def _load_vocabulary() -> dict:
    """Lädt Custom Vocabulary aus JSON-Datei."""
    if not VOCABULARY_FILE.exists():
        return {"keywords": []}
    try:
        data = json.loads(VOCABULARY_FILE.read_text())
        if not isinstance(data.get("keywords"), list):
            data["keywords"] = []
        return data
    except (json.JSONDecodeError, IOError) as e:
        logger.warning(f"Vocabulary-Datei fehlerhaft: {e}")
        return {"keywords": []}


@contextmanager
def _timed_operation(name: str):
    """Kontextmanager für Zeitmessung."""
    start = time.perf_counter()
    try:
        yield
    finally:
        elapsed_ms = (time.perf_counter() - start) * 1000
        if elapsed_ms >= 1000:
            logger.info(f"{name}: {elapsed_ms / 1000:.2f}s")
        else:
            logger.info(f"{name}: {elapsed_ms:.0f}ms")


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
    default_model = "nova-3"

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
        audio_kb = audio_path.stat().st_size // 1024

        # Vocabulary laden
        MAX_KEYWORDS = 100
        vocab = _load_vocabulary()
        keywords = vocab.get("keywords", [])[:MAX_KEYWORDS]

        logger.info(
            f"Deepgram: {model}, {audio_kb}KB, lang={language or 'auto'}, "
            f"vocab={len(keywords)}"
        )

        client = _get_client()

        with audio_path.open("rb") as f:
            audio_data = f.read()

        # Nova-3 nutzt 'keyterm', ältere Modelle nutzen 'keywords'
        is_nova3 = model.startswith("nova-3")
        vocab_params = {}
        if keywords:
            if is_nova3:
                vocab_params["keyterm"] = keywords
            else:
                vocab_params["keywords"] = keywords

        with _timed_operation("Deepgram-Transkription"):
            response = client.listen.v1.media.transcribe_file(
                request=audio_data,
                model=model,
                language=language,
                smart_format=True,
                punctuate=True,
                **vocab_params,
            )

        result = response.results.channels[0].alternatives[0].transcript

        logger.debug(f"Ergebnis: {result[:100]}..." if len(result) > 100 else f"Ergebnis: {result}")

        return result

    def supports_streaming(self) -> bool:
        """REST API unterstützt kein Streaming (siehe DeepgramStreamProvider)."""
        return False


__all__ = ["DeepgramProvider"]
