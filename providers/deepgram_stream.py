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
        early_buffer=audio_chunks,
    )
"""

import asyncio
import logging
import os
import signal
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, AsyncIterator, Callable

if TYPE_CHECKING:
    from deepgram.listen.v1.socket_client import AsyncV1SocketClient

# Zentrale Konfiguration importieren
from config import (
    WHISPER_SAMPLE_RATE,
    WHISPER_CHANNELS,
    WHISPER_BLOCKSIZE,
    INT16_MAX,
    INTERIM_THROTTLE_MS,
    FINALIZE_TIMEOUT,
    DEEPGRAM_WS_URL,
    DEEPGRAM_CLOSE_TIMEOUT,
    INTERIM_FILE,
    DEFAULT_DEEPGRAM_MODEL,
)

logger = logging.getLogger("whisper_go")

# Default-Modell (Alias für Rückwärtskompatibilität)
DEFAULT_MODEL = DEFAULT_DEEPGRAM_MODEL

# Session-ID Cache
_session_id: str = ""


def _get_session_id() -> str:
    """Holt Session-ID für Logging."""
    global _session_id
    if not _session_id:
        try:
            from utils.logging import get_session_id
            _session_id = get_session_id()
        except ImportError:
            import uuid
            _session_id = uuid.uuid4().hex[:8]
    return _session_id


def _log_preview(text: str, max_length: int = 100) -> str:
    """Kürzt Text für Log-Ausgabe mit Ellipsis wenn nötig."""
    if len(text) <= max_length:
        return text
    return f"{text[:max_length]}..."


def _play_sound(name: str) -> None:
    """Spielt benannten Sound ab."""
    try:
        from whisper_platform import get_sound_player
        player = get_sound_player()
        player.play(name)
    except Exception:
        pass


# =============================================================================
# Deepgram Response Extraction
# =============================================================================


def _extract_transcript(result) -> str | None:
    """Extrahiert Transkript aus Deepgram-Response.

    Deepgram's SDK liefert verschachtelte Objekte:
    result.channel.alternatives[0].transcript

    Returns None wenn kein Transkript vorhanden.
    """
    channel = getattr(result, "channel", None)
    if not channel:
        return None
    alternatives = getattr(channel, "alternatives", [])
    if not alternatives:
        return None
    return getattr(alternatives[0], "transcript", "") or None


# =============================================================================
# Deepgram WebSocket Connection
# =============================================================================


@asynccontextmanager
async def _create_deepgram_connection(
    api_key: str,
    *,
    model: str,
    language: str | None = None,
    smart_format: bool = True,
    punctuate: bool = True,
    interim_results: bool = True,
    encoding: str = "linear16",
    sample_rate: int = 16000,
    channels: int = 1,
) -> AsyncIterator["AsyncV1SocketClient"]:
    """Deepgram WebSocket mit kontrollierbarem close_timeout.

    Das SDK leitet close_timeout nicht an websockets.connect() weiter,
    was zu 5-10s Shutdown-Delays führt. Dieser Context Manager umgeht
    das Problem durch direkte Nutzung der websockets Library.

    Siehe docs/adr/001-deepgram-streaming-shutdown.md
    """
    # Lazy imports (nur bei Deepgram-Streaming benötigt)
    import httpx
    from websockets.legacy.client import connect as websockets_connect
    from deepgram.listen.v1.socket_client import AsyncV1SocketClient

    # Query-Parameter aufbauen
    params = httpx.QueryParams()
    params = params.add("model", model)
    if language:
        params = params.add("language", language)
    # Booleans explizit senden (True="true", False="false")
    params = params.add("smart_format", "true" if smart_format else "false")
    params = params.add("punctuate", "true" if punctuate else "false")
    params = params.add("interim_results", "true" if interim_results else "false")
    params = params.add("encoding", encoding)
    params = params.add("sample_rate", str(sample_rate))
    params = params.add("channels", str(channels))

    ws_url = f"{DEEPGRAM_WS_URL}?{params}"
    headers = {"Authorization": f"Token {api_key}"}

    async with websockets_connect(
        ws_url,
        extra_headers=headers,
        close_timeout=DEEPGRAM_CLOSE_TIMEOUT,
    ) as protocol:
        yield AsyncV1SocketClient(websocket=protocol)


# =============================================================================
# Streaming Core
# =============================================================================


async def deepgram_stream_core(
    model: str,
    language: str | None,
    *,
    early_buffer: list[bytes] | None = None,
    play_ready: bool = True,
    external_stop_event: threading.Event | None = None,
    audio_level_callback: Callable[[float], None] | None = None,
) -> str:
    """Gemeinsamer Streaming-Core für Deepgram (SDK v5.3).

    Args:
        model: Deepgram-Modell (z.B. "nova-3")
        language: Sprachcode oder None für Auto-Detection
        early_buffer: Vorab gepuffertes Audio (für Daemon-Mode)
        play_ready: Ready-Sound nach Mikrofon-Init spielen (für CLI)
        external_stop_event: threading.Event zum externen Stoppen (statt SIGUSR1)

    Drei Modi:
    - CLI (early_buffer=None): Buffering während WebSocket-Connect
    - Daemon (early_buffer=[...]): Buffer direkt in Queue, kein Buffering
    - Unified (external_stop_event): Externes Stop-Event statt SIGUSR1
    """
    import numpy as np
    import sounddevice as sd
    from deepgram.core.events import EventType
    from deepgram.extensions.types.sockets import ListenV1ControlMessage

    session_id = _get_session_id()

    api_key = os.getenv("DEEPGRAM_API_KEY")
    if not api_key:
        raise ValueError("DEEPGRAM_API_KEY nicht gesetzt")

    stream_start = time.perf_counter()
    mode_str = "mit Buffer" if early_buffer else "Buffering"
    buffer_info = f", {len(early_buffer)} early chunks" if early_buffer else ""
    logger.info(
        f"[{session_id}] Deepgram-Stream ({mode_str}): {model}, "
        f"lang={language or 'auto'}{buffer_info}"
    )

    # --- Shared State für Callbacks ---
    final_transcripts: list[str] = []
    stop_event = asyncio.Event()  # Signalisiert Ende der Aufnahme
    finalize_done = asyncio.Event()  # Server hat Rest-Audio verarbeitet
    stream_error: Exception | None = None

    # --- Deepgram Event-Handler ---
    last_interim_write = 0.0  # Throttle-State für Interim-Writes

    def on_message(result):
        """Sammelt Transkripte aus Deepgram-Responses."""
        nonlocal last_interim_write

        # from_finalize=True signalisiert: Server hat Rest-Audio verarbeitet
        if getattr(result, "from_finalize", False):
            finalize_done.set()

        transcript = _extract_transcript(result)
        if not transcript:
            return

        # Nur LiveResultResponse hat is_final (Default: False = interim)
        is_final = getattr(result, "is_final", False)

        if is_final:
            final_transcripts.append(transcript)
            logger.info(f"[{session_id}] Final: {_log_preview(transcript)}")
        else:
            # Throttling: Max alle INTERIM_THROTTLE_MS schreiben
            now = time.perf_counter()
            if (now - last_interim_write) * 1000 >= INTERIM_THROTTLE_MS:
                try:
                    INTERIM_FILE.write_text(transcript)
                    last_interim_write = now
                    logger.debug(
                        f"[{session_id}] Interim: {_log_preview(transcript, 30)}"
                    )
                except OSError as e:
                    # I/O-Fehler nicht den Stream abbrechen lassen
                    logger.warning(f"[{session_id}] Interim-Write fehlgeschlagen: {e}")

    def on_error(error):
        nonlocal stream_error
        logger.error(f"[{session_id}] Deepgram Error: {error}")
        stream_error = error if isinstance(error, Exception) else Exception(str(error))

    def on_close(_data):
        logger.debug(f"[{session_id}] Connection closed")
        stop_event.set()

    # Stop-Mechanismus: SIGUSR1 oder externes Event
    loop = asyncio.get_running_loop()

    if external_stop_event is not None:
        # Unified-Daemon-Mode: Externes threading.Event überwachen
        def _watch_external_stop():
            external_stop_event.wait()
            try:
                loop.call_soon_threadsafe(stop_event.set)
            except RuntimeError:
                # Event loop already closed (App shutdown via CMD+Q)
                pass

        stop_watcher = threading.Thread(target=_watch_external_stop, daemon=True)
        stop_watcher.start()
        logger.debug(f"[{session_id}] External stop event watcher gestartet")
    elif threading.current_thread() is threading.main_thread():
        # CLI/Raycast-Mode: SIGUSR1 Signal-Handler
        loop.add_signal_handler(signal.SIGUSR1, stop_event.set)

    audio_queue: asyncio.Queue[bytes | None] = asyncio.Queue()

    # --- Modus-spezifische Audio-Initialisierung ---
    #
    # Zwei Modi mit unterschiedlichem Timing:
    # - Daemon: Mikrofon lief bereits, Audio ist gepuffert → direkt in Queue
    # - CLI: Mikrofon startet jetzt, WebSocket noch nicht bereit → puffern
    #
    if early_buffer:
        # Daemon-Mode: Vorab aufgenommenes Audio direkt verfügbar machen
        for chunk in early_buffer:
            audio_queue.put_nowait(chunk)
        logger.info(f"[{session_id}] {len(early_buffer)} early chunks in Queue")

        def audio_callback(indata, _frames, _time_info, status):
            """Sendet Audio direkt an Queue (WebSocket bereits verbunden)."""
            if status:
                logger.warning(f"[{session_id}] Audio-Status: {status}")
            if not stop_event.is_set():
                # float32 [-1,1] → int16 für Deepgram
                audio_bytes = (indata * INT16_MAX).astype(np.int16).tobytes()
                loop.call_soon_threadsafe(audio_queue.put_nowait, audio_bytes)

        buffer_lock = None
        audio_buffer = None
    else:
        # CLI-Mode: Puffern bis WebSocket bereit ist
        # Verhindert Audio-Verlust während ~500ms WebSocket-Handshake
        audio_buffer: list[bytes] = []
        buffer_lock = threading.Lock()
        buffering_active = True

        def audio_callback(indata, _frames, _time_info, status):
            """Puffert Audio bis WebSocket verbunden, dann direkt senden."""
            if status:
                logger.warning(f"[{session_id}] Audio-Status: {status}")
            
            # RMS Berechnung für Visualisierung
            if audio_level_callback:
                rms = float(np.sqrt(np.mean(indata**2)))
                audio_level_callback(rms)

            if stop_event.is_set():
                return
            # float32 [-1,1] → int16 für Deepgram
            audio_bytes = (indata * INT16_MAX).astype(np.int16).tobytes()
            with buffer_lock:
                if buffering_active:
                    audio_buffer.append(audio_bytes)
                else:
                    loop.call_soon_threadsafe(audio_queue.put_nowait, audio_bytes)

    # Mikrofon starten
    mic_stream = sd.InputStream(
        samplerate=WHISPER_SAMPLE_RATE,
        channels=WHISPER_CHANNELS,
        blocksize=WHISPER_BLOCKSIZE,
        dtype=np.float32,
        callback=audio_callback,
    )
    mic_stream.start()

    mic_init_ms = (time.perf_counter() - stream_start) * 1000
    logger.info(f"[{session_id}] Mikrofon bereit nach {mic_init_ms:.0f}ms")

    if play_ready:
        _play_sound("ready")

    try:
        async with _create_deepgram_connection(
            api_key,
            model=model,
            language=language,
            sample_rate=WHISPER_SAMPLE_RATE,
            channels=WHISPER_CHANNELS,
        ) as connection:
            connection.on(EventType.MESSAGE, on_message)
            connection.on(EventType.ERROR, on_error)
            connection.on(EventType.CLOSE, on_close)

            ws_time = (time.perf_counter() - stream_start) * 1000

            # Buffer flush nur im CLI-Mode
            if buffer_lock and audio_buffer is not None:
                with buffer_lock:
                    buffering_active = False
                    buffered_count = len(audio_buffer)
                    for chunk in audio_buffer:
                        audio_queue.put_nowait(chunk)
                    audio_buffer.clear()
                logger.info(
                    f"[{session_id}] WebSocket verbunden nach {ws_time:.0f}ms, "
                    f"{buffered_count} gepufferte Chunks"
                )
            else:
                logger.info(f"[{session_id}] WebSocket verbunden nach {ws_time:.0f}ms")

            # --- Async Tasks für bidirektionale Kommunikation ---
            async def send_audio():
                """Sendet Audio-Chunks an Deepgram bis Stop-Signal."""
                nonlocal stream_error
                try:
                    while not stop_event.is_set():
                        try:
                            # 100ms Timeout: Regelmäßig stop_event prüfen
                            chunk = await asyncio.wait_for(
                                audio_queue.get(), timeout=0.1
                            )
                            if chunk is None:  # Sentinel = sauberes Ende
                                break
                            await connection.send_media(chunk)
                        except asyncio.TimeoutError:
                            continue
                except asyncio.CancelledError:
                    pass
                except Exception as e:
                    logger.error(f"[{session_id}] Audio-Send Fehler: {e}")
                    stream_error = e
                    stop_event.set()

            async def listen_for_messages():
                """Empfängt Transkripte von Deepgram."""
                try:
                    await connection.start_listening()
                except asyncio.CancelledError:
                    pass
                except Exception as e:
                    logger.debug(f"[{session_id}] Listener beendet: {e}")

            send_task = asyncio.create_task(send_audio())
            listen_task = asyncio.create_task(listen_for_messages())

            # --- Warten auf Stop (SIGUSR1 von Raycast oder CTRL+C) ---
            await stop_event.wait()
            logger.info(f"[{session_id}] Stop-Signal empfangen")

            # Interim-Datei sofort löschen (Menübar zeigt nur während Recording)
            INTERIM_FILE.unlink(missing_ok=True)

            # ═══════════════════════════════════════════════════════════════════
            # GRACEFUL SHUTDOWN - Optimiert für minimale Latenz
            #
            # Hintergrund: Der Deepgram SDK Context Manager (async with) verwendet
            # intern websockets.connect(), dessen __aexit__ auf einen sauberen
            # WebSocket Close-Handshake wartet (bis zu 10s Timeout).
            #
            # Lösung: Wir senden explizit Finalize + CloseStream BEVOR der
            # Context Manager endet. Das reduziert die Shutdown-Zeit von
            # ~10s auf ~2s. Siehe docs/adr/001-deepgram-streaming-shutdown.md
            # ═══════════════════════════════════════════════════════════════════

            # 1. Audio-Sender beenden
            await audio_queue.put(None)  # Sentinel signalisiert Ende
            await send_task

            # 2. Finalize: Deepgram verarbeitet gepuffertes Audio
            #    Server antwortet mit from_finalize=True wenn fertig
            logger.info(f"[{session_id}] Sende Finalize...")
            try:
                await connection.send_control(ListenV1ControlMessage(type="Finalize"))
            except Exception as e:
                logger.warning(f"[{session_id}] Finalize fehlgeschlagen: {e}")

            # 3. Warten auf finale Transkripte (from_finalize=True Event)
            try:
                await asyncio.wait_for(finalize_done.wait(), timeout=FINALIZE_TIMEOUT)
                logger.info(f"[{session_id}] Finalize abgeschlossen")
            except asyncio.TimeoutError:
                logger.warning(
                    f"[{session_id}] Finalize-Timeout ({FINALIZE_TIMEOUT}s)"
                )

            # 4. CloseStream: Erzwingt sofortiges Verbindungs-Ende
            #    Ohne CloseStream wartet der async-with Exit ~10s auf Server-Close
            logger.info(f"[{session_id}] Sende CloseStream...")
            try:
                await connection.send_control(
                    ListenV1ControlMessage(type="CloseStream")
                )
                logger.info(f"[{session_id}] CloseStream gesendet")
            except Exception as e:
                logger.warning(f"[{session_id}] CloseStream fehlgeschlagen: {e}")

            # 5. Listener Task beenden
            logger.info(f"[{session_id}] Beende Listener...")
            listen_task.cancel()
            await asyncio.gather(listen_task, return_exceptions=True)
            logger.info(f"[{session_id}] Listener beendet, verlasse async-with...")

            # Hinweis: Der async-with Exit blockiert noch ~2s im SDK.
            # Das ist websockets-Library-Verhalten und ohne Hacks nicht vermeidbar.

    finally:
        # Cleanup: Mikrofon und Signal-Handler freigeben
        try:
            mic_stream.stop()
            mic_stream.close()
        except Exception:
            pass
        # Signal-Handler nur entfernen wenn nicht external_stop_event verwendet
        if (
            external_stop_event is None
            and threading.current_thread() is threading.main_thread()
        ):
            try:
                loop.remove_signal_handler(signal.SIGUSR1)
            except Exception:
                pass

    if stream_error:
        raise stream_error

    result = " ".join(final_transcripts)
    logger.info(f"[{session_id}] Streaming abgeschlossen: {len(result)} Zeichen")
    return result


# =============================================================================
# Public API
# =============================================================================


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
        from .deepgram import DeepgramProvider
        return DeepgramProvider().transcribe(audio_path, model, language)

    def transcribe_stream(
        self,
        model: str | None = None,
        language: str | None = None,
    ) -> str:
        """Streaming-Transkription vom Mikrofon."""
        return transcribe_with_deepgram_stream(
            model=model or self.default_model,
            language=language,
        )


async def _transcribe_with_deepgram_stream_async(
    model: str = DEFAULT_MODEL,
    language: str | None = None,
) -> str:
    """Async Deepgram Streaming für CLI-Nutzung (Wrapper um Core)."""
    return await deepgram_stream_core(model, language, play_ready=True)


def transcribe_with_deepgram_stream_with_buffer(
    model: str,
    language: str | None,
    early_buffer: list[bytes],
) -> str:
    """Streaming mit vorgepuffertem Audio (Daemon-Mode, Wrapper um Core)."""
    return asyncio.run(
        deepgram_stream_core(
            model, language, early_buffer=early_buffer, play_ready=False
        )
    )


def transcribe_with_deepgram_stream(
    model: str = DEFAULT_MODEL,
    language: str | None = None,
) -> str:
    """Sync Wrapper für async Deepgram Streaming.

    Verwendet asyncio.run() um die async Implementierung auszuführen.
    Für Raycast-Integration: SIGUSR1 stoppt die Aufnahme sauber.
    """
    return asyncio.run(_transcribe_with_deepgram_stream_async(model, language))


# Aliase für Rückwärtskompatibilität mit transcribe.py
_deepgram_stream_core = deepgram_stream_core


__all__ = [
    "DeepgramStreamProvider",
    "deepgram_stream_core",
    "transcribe_with_deepgram_stream",
    "transcribe_with_deepgram_stream_with_buffer",
    "_transcribe_with_deepgram_stream_async",
    "_extract_transcript",
    "_create_deepgram_connection",
    "_deepgram_stream_core",
    "DEFAULT_MODEL",
    "WHISPER_SAMPLE_RATE",
    "WHISPER_CHANNELS",
    "WHISPER_BLOCKSIZE",
]
