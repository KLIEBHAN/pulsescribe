"""Deepgram WebSocket Streaming Provider für whisper_go.

Bietet Real-Time Streaming-Transkription via Deepgram WebSocket API.

Usage:
    from providers.deepgram_stream import transcribe_with_deepgram_stream

    # CLI-Modus (Enter zum Stoppen)
    text = transcribe_with_deepgram_stream(language="de")

    # Mit vorgepuffertem Audio (Daemon-Modus)
    text = transcribe_with_deepgram_stream_with_buffer(
        model="nova-3",
        language="de",
        early_buffer=audio_chunks。,
    )
"""

import logging
import os
import threading
from pathlib import Path

logger = logging.getLogger("whisper_go")

# Default-Modell
DEFAULT_MODEL = "nova-3"


def _get_session_id() -> str:
    """Holt Session-ID für Logging."""
    try:
        from utils.logging import get_session_id
        return get_session_id()
    except ImportError:
        return "unknown"


class DeepgramStreamProvider:
    """Deepgram WebSocket Streaming Provider.

    Implementiert das TranscriptionProvider-Interface für Streaming-Transkription.
    """

    @property
    def name(self) -> str:
        return "deepgram_stream"

    @property
    def default_model(self) -> str:
        return DEFAULT_MODEL

    def supports_streaming(self) -> bool:
        return True

    def transcribe(
        self,
        audio_path: Path,
        model: str | None = None,
        language: str | None = None,
    ) -> str:
        """Transkribiert eine Audio-Datei via REST API (nicht Streaming).

        Für Datei-Transkription nutze den regulären DeepgramProvider.
        """
        # Fallback auf REST-Provider für Datei-Transkription
        from .deepgram import DeepgramProvider
        return DeepgramProvider().transcribe(audio_path, model, language)

    def transcribe_stream(
        self,
        model: str | None = None,
        language: str | None = None,
    ) -> str:
        """Streaming-Transkription vom Mikrofon.

        Args:
            model: Deepgram-Modell (default: nova-3)
            language: Sprachcode oder None für Auto-Detection

        Returns:
            Transkribierter Text
        """
        return transcribe_with_deepgram_stream(
            model=model or self.default_model,
            language=language,
        )


def transcribe_with_deepgram_stream(
    model: str = DEFAULT_MODEL,
    language: str | None = None,
) -> str:
    """Sync Wrapper für async Deepgram Streaming.

    Verwendet asyncio.run() um die async Implementierung auszuführen.
    Für Raycast-Integration: SIGUSR1 stoppt die Aufnahme sauber.
    """
    import asyncio

    # Import der Core-Funktion aus transcribe (wird später hierhin verschoben)
    # Dies ermöglicht schrittweise Migration
    try:
        import transcribe
        return asyncio.run(
            transcribe._transcribe_with_deepgram_stream_async(model, language)
        )
    except ImportError:
        raise RuntimeError(
            "Deepgram Streaming benötigt das transcribe-Modul. "
            "Vollständige Extraktion folgt in zukünftigem PR."
        )


def transcribe_with_deepgram_stream_with_buffer(
    model: str,
    language: str | None,
    early_buffer: list[bytes],
) -> str:
    """Streaming mit vorgepuffertem Audio (Daemon-Mode).

    Args:
        model: Deepgram-Modell
        language: Sprachcode oder None
        early_buffer: Vorab aufgenommene Audio-Chunks

    Returns:
        Transkribierter Text
    """
    import asyncio

    # Import der Core-Funktion aus transcribe
    try:
        import transcribe
        return asyncio.run(
            transcribe._deepgram_stream_core(
                model, language, early_buffer=early_buffer, play_ready=False
            )
        )
    except ImportError:
        raise RuntimeError(
            "Deepgram Streaming benötigt das transcribe-Modul. "
            "Vollständige Extraktion folgt in zukünftigem PR."
        )


async def deepgram_stream_core(
    model: str,
    language: str | None,
    *,
    early_buffer: list[bytes] | None = None,
    play_ready: bool = True,
    external_stop_event: threading.Event | None = None,
) -> str:
    """Gemeinsamer Streaming-Core für Deepgram.

    Diese Funktion delegiert aktuell an transcribe._deepgram_stream_core.
    In einem zukünftigen PR wird die Logik hierhin verschoben.

    Args:
        model: Deepgram-Modell (z.B. "nova-3")
        language: Sprachcode oder None für Auto-Detection
        early_buffer: Vorab gepuffertes Audio (für Daemon-Mode)
        play_ready: Ready-Sound nach Mikrofon-Init spielen (für CLI)
        external_stop_event: threading.Event zum externen Stoppen

    Returns:
        Transkribierter Text
    """
    import transcribe
    return await transcribe._deepgram_stream_core(
        model,
        language,
        early_buffer=early_buffer,
        play_ready=play_ready,
        external_stop_event=external_stop_event,
    )


__all__ = [
    "DeepgramStreamProvider",
    "transcribe_with_deepgram_stream",
    "transcribe_with_deepgram_stream_with_buffer",
    "deepgram_stream_core",
    "DEFAULT_MODEL",
]
