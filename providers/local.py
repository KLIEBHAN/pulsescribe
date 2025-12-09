"""Lokaler Whisper Provider.

Nutzt openai-whisper für Offline-Transkription.
"""

import json
import logging
from pathlib import Path
import sys

logger = logging.getLogger("whisper_go.providers.local")

# Vocabulary-Pfad
VOCABULARY_FILE = Path.home() / ".whisper_go" / "vocabulary.json"


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


def _log_stderr(message: str) -> None:
    """Status-Meldung auf stderr."""
    print(message, file=sys.stderr)


class LocalProvider:
    """Lokaler Whisper Provider.

    Nutzt openai-whisper für Offline-Transkription.
    Keine API-Kosten, aber langsamer (~5-10s je nach Modell).

    Unterstützte Modelle:
        - tiny: 39M Parameter, ~1GB VRAM, sehr schnell
        - base: 74M Parameter, ~1GB VRAM, schnell
        - small: 244M Parameter, ~2GB VRAM, mittel
        - medium: 769M Parameter, ~5GB VRAM, langsam
        - large: 1550M Parameter, ~10GB VRAM, sehr langsam
        - turbo: 809M Parameter, ~6GB VRAM, schnell & gut (empfohlen)
    """

    name = "local"
    default_model = "turbo"

    def __init__(self) -> None:
        self._model_cache: dict = {}

    def transcribe(
        self,
        audio_path: Path,
        model: str | None = None,
        language: str | None = None,
    ) -> str:
        """Transkribiert Audio lokal mit openai-whisper.

        Args:
            audio_path: Pfad zur Audio-Datei
            model: Modell-Name (default: turbo)
            language: Sprachcode oder None für Auto-Detection

        Returns:
            Transkribierter Text
        """
        import whisper

        model_name = model or self.default_model

        # Modell laden (mit Caching für wiederholte Aufrufe)
        if model_name not in self._model_cache:
            _log_stderr(f"Lade Modell '{model_name}'...")
            self._model_cache[model_name] = whisper.load_model(model_name)

        whisper_model = self._model_cache[model_name]

        _log_stderr(f"Transkribiere {audio_path.name}...")

        options: dict = {}
        if language:
            options["language"] = language

        # Custom Vocabulary als initial_prompt für bessere Erkennung
        MAX_KEYWORDS = 50
        vocab = _load_vocabulary()
        keywords = vocab.get("keywords", [])[:MAX_KEYWORDS]
        if keywords:
            options["initial_prompt"] = f"Fachbegriffe: {', '.join(keywords)}"
            logger.debug(f"Lokales Whisper mit {len(keywords)} Keywords")

        result = whisper_model.transcribe(str(audio_path), **options)

        return result["text"]

    def supports_streaming(self) -> bool:
        """Lokales Whisper unterstützt kein Streaming."""
        return False


__all__ = ["LocalProvider"]
