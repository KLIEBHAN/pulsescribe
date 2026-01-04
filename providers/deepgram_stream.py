"""Deepgram WebSocket Streaming Provider für pulsescribe.

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

from __future__ import annotations

import asyncio
import logging
import os
import queue
import sys
import threading
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import TYPE_CHECKING, Any, AsyncIterator, Callable

from config import (
    AUDIO_QUEUE_POLL_INTERVAL,
    CLI_BUFFER_LIMIT,
    DEEPGRAM_CLOSE_TIMEOUT,
    DEEPGRAM_WS_URL,
    DEFAULT_DEEPGRAM_MODEL,
    FINALIZE_TIMEOUT,
    FORWARDER_THREAD_JOIN_TIMEOUT,
    INT16_MAX,
    INTERIM_FILE,
    INTERIM_THROTTLE_MS,
    SEND_MEDIA_TIMEOUT,
    WHISPER_BLOCKSIZE,
    WHISPER_CHANNELS,
    WHISPER_SAMPLE_RATE,
    get_input_device,
)
from utils.logging import get_session_id
from utils.timing import log_preview

if TYPE_CHECKING:
    from pathlib import Path

    import numpy as np
    import sounddevice as sd
    from deepgram.clients.listen.v1 import LiveResultResponse
    from deepgram.listen.v1.socket_client import AsyncV1SocketClient

logger = logging.getLogger("pulsescribe")


# =============================================================================
# Enums & Dataclasses
# =============================================================================


class AudioSourceMode(Enum):
    """Modi für Audio-Initialisierung."""

    CLI = auto()  # Puffern bis WebSocket bereit
    DAEMON = auto()  # Buffer direkt in Queue
    WARM_STREAM = auto()  # Mikrofon läuft bereits


@dataclass
class WarmStreamSource:
    """Quelle für Audio von einem bereits laufenden Stream (Warm-Start).

    Ermöglicht instant-start Recording ohne WASAPI-Cold-Start-Delay (~500ms).
    Der Audio-Stream läuft bereits im Hintergrund; beim Recording wird er
    nur "scharf geschaltet" (armed), um Audio-Chunks zu sammeln.

    Workflow:
        1. Stream läuft bereits (Callback ignoriert Audio wenn arm_event nicht gesetzt)
        2. Recording startet → arm_event.set() → Chunks werden in audio_queue geschrieben
        3. Recording stoppt → arm_event.clear() → Chunks werden wieder ignoriert

    Attributes:
        audio_queue: Queue mit Audio-Chunks (bytes, int16 PCM)
        sample_rate: Sample Rate des Streams (z.B. 16000, 48000)
        arm_event: Steuert ob Audio gesammelt wird (set=aktiv, clear=ignoriert)
        stream: Der laufende InputStream (für Cleanup/Reference)
    """

    audio_queue: queue.Queue[bytes]
    sample_rate: int
    arm_event: threading.Event
    stream: sd.InputStream

    def __post_init__(self) -> None:
        """Validiert Sample Rate."""
        if not (8000 <= self.sample_rate <= 48000):
            raise ValueError(f"sample_rate muss zwischen 8000-48000 liegen: {self.sample_rate}")


@dataclass
class StreamState:
    """Zentraler State für Streaming-Session.

    Ersetzt nonlocal-Variablen durch ein explizites State-Objekt.
    Verbessert Testbarkeit und macht den Datenfluss transparenter.
    """

    final_transcripts: list[str] = field(default_factory=list)
    last_interim_write: float = 0.0
    stream_error: Exception | None = None
    stop_event: asyncio.Event = field(default_factory=asyncio.Event)
    finalize_done: asyncio.Event = field(default_factory=asyncio.Event)
    # Flag für einmalige Buffer-Warnung
    buffer_overflow_logged: bool = False


@dataclass
class AudioSourceResult:
    """Ergebnis der Audio-Source-Initialisierung."""

    sample_rate: int
    mic_stream: sd.InputStream | None  # None bei Warm-Stream
    buffer_state: BufferState | None  # Nur für CLI-Mode
    forwarder_thread: threading.Thread | None = None  # Nur für Warm-Stream


@dataclass
class BufferState:
    """State für CLI-Mode Audio-Buffering."""

    buffer: list[bytes] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)
    active: bool = True


# =============================================================================
# Sound Helper
# =============================================================================


def _play_sound(name: str) -> None:
    """Spielt benannten Sound ab.

    Fehler werden geloggt statt still verschluckt.
    """
    try:
        from whisper_platform import get_sound_player

        player = get_sound_player()
        player.play(name)
    except Exception as e:
        logger.debug(f"Sound '{name}' konnte nicht abgespielt werden: {e}")


# =============================================================================
# Deepgram Response Extraction
# =============================================================================


def _extract_transcript(result: LiveResultResponse | Any) -> str | None:
    """Extrahiert Transkript aus Deepgram-Response.

    Deepgram's SDK liefert verschachtelte Objekte:
    result.channel.alternatives[0].transcript

    Args:
        result: Deepgram LiveResultResponse oder ähnliches Response-Objekt

    Returns:
        Transkript-String oder None wenn kein Transkript vorhanden.
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
    sample_rate: int = WHISPER_SAMPLE_RATE,
    channels: int = WHISPER_CHANNELS,
) -> AsyncIterator[AsyncV1SocketClient]:
    """Deepgram WebSocket mit kontrollierbarem close_timeout.

    Das SDK leitet close_timeout nicht an websockets.connect() weiter,
    was zu 5-10s Shutdown-Delays führt. Dieser Context Manager umgeht
    das Problem durch direkte Nutzung der websockets Library.

    Siehe docs/adr/001-deepgram-streaming-shutdown.md
    """
    import httpx
    from deepgram.listen.v1.socket_client import AsyncV1SocketClient
    from websockets.legacy.client import connect as websockets_connect

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
# Mikrofon Setup (DRY)
# =============================================================================


def _create_mic_stream(
    callback: Callable[[np.ndarray, int, Any, Any], None],
    session_id: str,
    stream_start: float,
) -> tuple[sd.InputStream, int]:
    """Erstellt und startet Mikrofon-InputStream.

    Konsolidiert duplizierten Mikrofon-Setup-Code.

    Args:
        callback: Audio-Callback für den Stream
        session_id: Session-ID für Logging
        stream_start: Startzeitpunkt für Timing-Messung

    Returns:
        Tuple (mic_stream, sample_rate)
    """
    import numpy as np
    import sounddevice as sd

    input_device, sample_rate = get_input_device()
    blocksize = int(WHISPER_BLOCKSIZE * sample_rate / WHISPER_SAMPLE_RATE)

    mic_stream = sd.InputStream(
        device=input_device,
        samplerate=sample_rate,
        channels=WHISPER_CHANNELS,
        blocksize=blocksize,
        dtype=np.int16,
        callback=callback,
    )
    mic_stream.start()

    logger.debug(f"[{session_id}] Audio-Device: {input_device}, {sample_rate}Hz")

    return mic_stream, sample_rate


# =============================================================================
# Audio Callback Factory
# =============================================================================


def _handle_buffered_audio(
    buffer_state: BufferState,
    audio_bytes: bytes,
    state: StreamState,
    session_id: str,
    loop: asyncio.AbstractEventLoop,
    audio_queue: asyncio.Queue[bytes | None],
) -> None:
    """Handhabt Audio im Buffer-Mode (CLI).

    Puffert Audio während WebSocket-Handshake, sendet danach direkt.
    Lock-Granularität: Nur Buffer-Zugriff ist geschützt, nicht die Queue-Operation.
    """
    # Schneller Check ob Buffering noch aktiv (minimale Lock-Zeit)
    with buffer_state.lock:
        is_buffering = buffer_state.active
        if is_buffering:
            if len(buffer_state.buffer) < CLI_BUFFER_LIMIT:
                buffer_state.buffer.append(audio_bytes)
                return
            elif not state.buffer_overflow_logged:
                logger.warning(
                    f"[{session_id}] Audio-Buffer voll ({CLI_BUFFER_LIMIT} Chunks), "
                    "verwerfe weiteres Audio bis WebSocket verbunden"
                )
                state.buffer_overflow_logged = True
                return

    # Buffering deaktiviert: Direkt an Queue senden (außerhalb des Locks)
    loop.call_soon_threadsafe(audio_queue.put_nowait, audio_bytes)


def _create_audio_callback(
    *,
    state: StreamState,
    loop: asyncio.AbstractEventLoop,
    audio_queue: asyncio.Queue[bytes | None],
    session_id: str,
    buffer_state: BufferState | None = None,
    audio_level_callback: Callable[[float], None] | None = None,
) -> Callable[[np.ndarray, int, Any, Any], None]:
    """Factory für Audio-Callbacks.

    Erzeugt einen parametrisierten Callback für sounddevice.InputStream.
    Unterstützt zwei Modi:
    - Direct Mode (buffer_state=None): Audio direkt an Queue senden
    - Buffer Mode (buffer_state gesetzt): Audio puffern bis WebSocket bereit

    Args:
        state: Zentraler StreamState
        loop: Event-Loop für thread-safe Queue-Operationen
        audio_queue: Ziel-Queue für Audio-Chunks
        session_id: Session-ID für Logging
        buffer_state: Optional BufferState für CLI-Mode
        audio_level_callback: Optional Callback für Audio-Level (Visualisierung)

    Returns:
        Callback-Funktion für sounddevice.InputStream
    """
    import numpy as np

    def audio_callback(
        indata: np.ndarray,
        _frames: int,
        _time_info: Any,
        status: Any,
    ) -> None:
        """Verarbeitet Audio-Chunks vom Mikrofon."""
        if status:
            logger.warning(f"[{session_id}] Audio-Status: {status}")

        if state.stop_event.is_set():
            return

        # Audio-Level für Visualisierung berechnen (optional)
        if audio_level_callback is not None:
            rms = float(np.sqrt(np.mean(indata.astype(np.float32) ** 2)) / INT16_MAX)
            audio_level_callback(rms)

        audio_bytes = indata.tobytes()

        # Direct Mode: Sofort an Queue senden
        if buffer_state is None:
            loop.call_soon_threadsafe(audio_queue.put_nowait, audio_bytes)
            return

        # Buffer Mode: Puffern bis WebSocket verbunden
        _handle_buffered_audio(
            buffer_state, audio_bytes, state, session_id, loop, audio_queue
        )

    return audio_callback


# =============================================================================
# Audio Source Initialization
# =============================================================================


def _log_init_complete(
    session_id: str,
    stream_start: float,
    mode_name: str,
    play_ready: bool,
) -> None:
    """Loggt erfolgreiche Audio-Initialisierung und spielt Ready-Sound.

    Gemeinsame Abschluss-Logik für alle Audio-Modi.

    Args:
        session_id: Session-ID für Logging
        stream_start: Startzeitpunkt für Timing-Messung
        mode_name: Name des Modus für Log-Nachricht
        play_ready: Ob Ready-Sound gespielt werden soll
    """
    if play_ready:
        _play_sound("ready")
    mic_init_ms = (time.perf_counter() - stream_start) * 1000
    logger.info(f"[{session_id}] {mode_name} nach {mic_init_ms:.0f}ms")


def _init_warm_stream(
    warm_source: WarmStreamSource,
    state: StreamState,
    loop: asyncio.AbstractEventLoop,
    audio_queue: asyncio.Queue[bytes | None],
    session_id: str,
    play_ready: bool,
    stream_start: float,
) -> AudioSourceResult:
    """Initialisiert Audio-Source für Warm-Stream-Mode.

    Mikrofon läuft bereits, wir "armen" es nur zum Aufnehmen.
    Instant-Start ohne WASAPI Cold-Start-Delay.
    """
    logger.info(
        f"[{session_id}] Warm-Stream Mode: {warm_source.sample_rate}Hz, instant-start"
    )

    # Arm the stream - ab jetzt werden Samples gesammelt
    warm_source.arm_event.set()

    def _warm_stream_forwarder() -> None:
        """Leitet Audio von sync Queue an async Queue weiter."""
        while not state.stop_event.is_set():
            try:
                chunk = warm_source.audio_queue.get(timeout=AUDIO_QUEUE_POLL_INTERVAL)
                loop.call_soon_threadsafe(audio_queue.put_nowait, chunk)
            except queue.Empty:
                continue
            except Exception as e:
                if not state.stop_event.is_set():
                    logger.warning(f"[{session_id}] Warm-Stream Forwarder Error: {e}")
                break

        # Queue drainieren nach Stop - verhindert abgeschnittene letzte Wörter
        drained = 0
        while True:
            try:
                chunk = warm_source.audio_queue.get_nowait()
            except queue.Empty:
                break
            try:
                loop.call_soon_threadsafe(audio_queue.put_nowait, chunk)
                drained += 1
            except RuntimeError:
                # Event-Loop bereits geschlossen - restliche Chunks verwerfen
                logger.debug(f"[{session_id}] Event-Loop geschlossen, Drain abgebrochen")
                break
        if drained > 0:
            logger.debug(f"[{session_id}] Warm-Stream: {drained} Rest-Chunks geleert")

    forwarder_thread = threading.Thread(
        target=_warm_stream_forwarder, daemon=True, name="WarmStreamForwarder"
    )
    forwarder_thread.start()

    _log_init_complete(session_id, stream_start, "Warm-Stream armed", play_ready)

    return AudioSourceResult(
        sample_rate=warm_source.sample_rate,
        mic_stream=None,
        buffer_state=None,
        forwarder_thread=forwarder_thread,
    )


def _init_daemon_stream(
    early_buffer: list[bytes],
    state: StreamState,
    loop: asyncio.AbstractEventLoop,
    audio_queue: asyncio.Queue[bytes | None],
    session_id: str,
    play_ready: bool,
    stream_start: float,
) -> AudioSourceResult:
    """Initialisiert Audio-Source für Daemon-Mode.

    Vorab aufgenommenes Audio wird direkt in die Queue geschoben.
    Mikrofon wird neu gestartet für weiteres Audio.
    """
    # Early Buffer in Queue schieben
    for chunk in early_buffer:
        audio_queue.put_nowait(chunk)
    logger.info(f"[{session_id}] {len(early_buffer)} early chunks in Queue")

    # Callback für neues Audio (Direct Mode)
    callback = _create_audio_callback(
        state=state,
        loop=loop,
        audio_queue=audio_queue,
        session_id=session_id,
        buffer_state=None,  # Direct Mode
    )

    mic_stream, sample_rate = _create_mic_stream(callback, session_id, stream_start)

    _log_init_complete(session_id, stream_start, "Daemon-Mode Mikrofon bereit", play_ready)

    return AudioSourceResult(
        sample_rate=sample_rate,
        mic_stream=mic_stream,
        buffer_state=None,
    )


def _init_cli_stream(
    state: StreamState,
    loop: asyncio.AbstractEventLoop,
    audio_queue: asyncio.Queue[bytes | None],
    session_id: str,
    play_ready: bool,
    stream_start: float,
    audio_level_callback: Callable[[float], None] | None = None,
) -> AudioSourceResult:
    """Initialisiert Audio-Source für CLI-Mode.

    Puffert Audio bis WebSocket verbunden ist, um Audio-Verlust
    während des ~500ms Handshakes zu vermeiden.
    """
    buffer_state = BufferState()

    callback = _create_audio_callback(
        state=state,
        loop=loop,
        audio_queue=audio_queue,
        session_id=session_id,
        buffer_state=buffer_state,
        audio_level_callback=audio_level_callback,
    )

    mic_stream, sample_rate = _create_mic_stream(callback, session_id, stream_start)

    _log_init_complete(session_id, stream_start, "CLI-Mode Mikrofon bereit", play_ready)

    return AudioSourceResult(
        sample_rate=sample_rate,
        mic_stream=mic_stream,
        buffer_state=buffer_state,
    )


# =============================================================================
# Event Handlers
# =============================================================================


def _create_message_handler(
    state: StreamState,
    session_id: str,
) -> Callable[[LiveResultResponse | Any], None]:
    """Erstellt Handler für Deepgram-Nachrichten."""

    def on_message(result: LiveResultResponse | Any) -> None:
        """Sammelt Transkripte aus Deepgram-Responses."""
        # from_finalize=True signalisiert: Server hat Rest-Audio verarbeitet
        if getattr(result, "from_finalize", False):
            state.finalize_done.set()

        transcript = _extract_transcript(result)
        if not transcript:
            return

        is_final = getattr(result, "is_final", False)

        if is_final:
            state.final_transcripts.append(transcript)
            logger.info(f"[{session_id}] Final: {log_preview(transcript)}")
        else:
            # Throttling: Max alle INTERIM_THROTTLE_MS schreiben
            now = time.perf_counter()
            if (now - state.last_interim_write) * 1000 >= INTERIM_THROTTLE_MS:
                try:
                    INTERIM_FILE.write_text(transcript)
                    state.last_interim_write = now
                    logger.debug(f"[{session_id}] Interim: {log_preview(transcript, 30)}")
                except OSError as e:
                    logger.warning(f"[{session_id}] Interim-Write fehlgeschlagen: {e}")

    return on_message


def _create_error_handler(
    state: StreamState,
    session_id: str,
) -> Callable[[Any], None]:
    """Erstellt Handler für Deepgram-Fehler."""

    def on_error(error: Exception | str | Any) -> None:
        """Behandelt Fehler vom Deepgram-Server."""
        logger.error(f"[{session_id}] Deepgram Error: {error}")
        if isinstance(error, Exception):
            state.stream_error = error
        else:
            state.stream_error = Exception(str(error))
        state.stop_event.set()

    return on_error


def _create_close_handler(
    state: StreamState,
    session_id: str,
) -> Callable[[Any], None]:
    """Erstellt Handler für Verbindungs-Ende."""

    def on_close(_data: Any) -> None:
        """Behandelt Verbindungs-Ende."""
        logger.debug(f"[{session_id}] Connection closed")
        state.stop_event.set()

    return on_close


# =============================================================================
# Stop Event Watcher
# =============================================================================


def _setup_stop_mechanism(
    state: StreamState,
    loop: asyncio.AbstractEventLoop,
    external_stop_event: threading.Event | None,
    session_id: str,
) -> None:
    """Richtet den Stop-Mechanismus ein.

    Unterstützte Modi:
    1. external_stop_event gesetzt: Thread überwacht das Event (alle Plattformen)
    2. Unix + Main-Thread: SIGUSR1 Signal-Handler
    3. Windows ohne external_stop_event: Warnung, da kein Stop-Mechanismus verfügbar

    Args:
        state: StreamState mit stop_event
        loop: Event-Loop für thread-safe Aufrufe
        external_stop_event: Externes threading.Event zum Stoppen
        session_id: Session-ID für Logging
    """
    if external_stop_event is not None:
        # Unified-Daemon-Mode: Externes threading.Event überwachen
        def _watch_external_stop() -> None:
            external_stop_event.wait()
            try:
                loop.call_soon_threadsafe(state.stop_event.set)
            except RuntimeError as e:
                logger.debug(
                    f"[{session_id}] Event-Loop geschlossen, Stop-Event nicht gesetzt: {e}"
                )

        stop_watcher = threading.Thread(
            target=_watch_external_stop, daemon=True, name="StopWatcher"
        )
        stop_watcher.start()
        logger.debug(f"[{session_id}] External stop event watcher gestartet")

    elif sys.platform != "win32" and threading.current_thread() is threading.main_thread():
        # Unix only: SIGUSR1 Signal-Handler
        import signal

        loop.add_signal_handler(signal.SIGUSR1, state.stop_event.set)
        logger.debug(f"[{session_id}] SIGUSR1 handler registriert")

    else:
        # Windows ohne external_stop_event oder non-main thread
        # In diesem Fall muss der Caller selbst für das Stoppen sorgen
        # (z.B. durch direktes Setzen von state.stop_event)
        logger.warning(
            f"[{session_id}] Kein Stop-Mechanismus verfügbar. "
            f"Auf Windows muss external_stop_event gesetzt werden, "
            f"oder state.stop_event manuell gesetzt werden."
        )


def _cleanup_stop_mechanism(
    loop: asyncio.AbstractEventLoop,
    external_stop_event: threading.Event | None,
) -> None:
    """Entfernt Signal-Handler (nur Unix)."""
    if external_stop_event is None and sys.platform != "win32":
        if threading.current_thread() is threading.main_thread():
            import signal

            try:
                loop.remove_signal_handler(signal.SIGUSR1)
            except Exception as e:
                logger.debug(f"Signal-Handler Cleanup fehlgeschlagen: {e}")


# =============================================================================
# Streaming Core
# =============================================================================


async def _graceful_shutdown(
    connection: AsyncV1SocketClient,
    state: StreamState,
    audio_queue: asyncio.Queue[bytes | None],
    send_task: asyncio.Task[None],
    listen_task: asyncio.Task[None],
    session_id: str,
) -> None:
    """Sauberes Beenden der Streaming-Session.

    Führt die Shutdown-Sequenz in der richtigen Reihenfolge aus:
    1. Audio-Sender beenden (Sentinel in Queue)
    2. Finalize an Deepgram senden (verarbeitet Rest-Audio)
    3. Auf finale Transkripte warten
    4. CloseStream senden
    5. Listener-Task canceln

    Args:
        connection: Aktive Deepgram WebSocket-Verbindung
        state: StreamState mit finalize_done Event
        audio_queue: Queue zum Signalisieren des Sender-Endes
        send_task: Audio-Sender Task
        listen_task: Message-Listener Task
        session_id: Session-ID für Logging
    """
    from deepgram.extensions.types.sockets import ListenV1ControlMessage

    # 1. Audio-Sender beenden (einziger Ort für das None-Sentinel)
    await audio_queue.put(None)
    await send_task

    # 2. Finalize senden
    logger.info(f"[{session_id}] Sende Finalize...")
    t_finalize_start = time.perf_counter()
    try:
        await connection.send_control(ListenV1ControlMessage(type="Finalize"))
    except Exception as e:
        logger.warning(f"[{session_id}] Finalize fehlgeschlagen: {e}")

    # 3. Warten auf finale Transkripte
    try:
        await asyncio.wait_for(
            state.finalize_done.wait(), timeout=FINALIZE_TIMEOUT
        )
        t_finalize = (time.perf_counter() - t_finalize_start) * 1000
        logger.info(f"[{session_id}] Finalize abgeschlossen ({t_finalize:.0f}ms)")
    except asyncio.TimeoutError:
        t_finalize = (time.perf_counter() - t_finalize_start) * 1000
        logger.warning(
            f"[{session_id}] Finalize-Timeout nach {t_finalize:.0f}ms "
            f"(max: {FINALIZE_TIMEOUT}s)"
        )

    # 4. CloseStream senden
    logger.info(f"[{session_id}] Sende CloseStream...")
    try:
        await connection.send_control(ListenV1ControlMessage(type="CloseStream"))
        logger.info(f"[{session_id}] CloseStream gesendet")
    except Exception as e:
        logger.warning(f"[{session_id}] CloseStream fehlgeschlagen: {e}")

    # 5. Listener beenden (Guard: nicht den eigenen Task canceln)
    logger.info(f"[{session_id}] Beende Listener...")
    current_task = asyncio.current_task()
    if listen_task is not current_task:
        listen_task.cancel()
        await asyncio.gather(listen_task, return_exceptions=True)
    logger.info(f"[{session_id}] Listener beendet")


def _validate_model(model: str) -> None:
    """Validiert Model-Parameter."""
    if not model or not model.strip():
        raise ValueError("model darf nicht leer sein")


def _validate_api_key(api_key: str | None) -> str:
    """Validiert API-Key und gibt ihn zurück."""
    if not api_key:
        raise ValueError("DEEPGRAM_API_KEY nicht gesetzt")
    if not api_key.strip():
        raise ValueError("DEEPGRAM_API_KEY ist leer")
    return api_key


async def deepgram_stream_core(
    model: str,
    language: str | None,
    *,
    early_buffer: list[bytes] | None = None,
    play_ready: bool = True,
    external_stop_event: threading.Event | None = None,
    audio_level_callback: Callable[[float], None] | None = None,
    warm_stream_source: WarmStreamSource | None = None,
) -> str:
    """Gemeinsamer Streaming-Core für Deepgram (SDK v5.3).

    Args:
        model: Deepgram-Modell (z.B. "nova-3")
        language: Sprachcode oder None für Auto-Detection
        early_buffer: Vorab gepuffertes Audio (für Daemon-Mode)
        play_ready: Ready-Sound nach Mikrofon-Init spielen (für CLI)
        external_stop_event: threading.Event zum externen Stoppen (statt SIGUSR1)
        audio_level_callback: Callback für Audio-Level Updates
        warm_stream_source: Externes WarmStreamSource für instant-start (Windows)

    Drei Modi:
    - CLI (early_buffer=None): Buffering während WebSocket-Connect
    - Daemon (early_buffer=[...]): Buffer direkt in Queue, kein Buffering
    - Warm-Stream (warm_stream_source): Mikrofon bereits offen, instant-start

    Returns:
        Transkribierter Text als String

    Raises:
        ValueError: Bei ungültigen Parametern oder fehlendem API-Key
    """
    from deepgram.core.events import EventType

    # Validierung
    _validate_model(model)
    api_key = _validate_api_key(os.getenv("DEEPGRAM_API_KEY"))

    session_id = get_session_id()
    stream_start = time.perf_counter()

    # Modus bestimmen
    if warm_stream_source is not None:
        mode = AudioSourceMode.WARM_STREAM
        mode_str = "Warm-Stream"
    elif early_buffer:  # Nicht-leere Liste
        mode = AudioSourceMode.DAEMON
        mode_str = f"Daemon, {len(early_buffer)} early chunks"
    else:
        mode = AudioSourceMode.CLI
        mode_str = "CLI (Buffering)"

    logger.info(
        f"[{session_id}] Deepgram-Stream ({mode_str}): {model}, "
        f"lang={language or 'auto'}"
    )

    # Zentraler State
    state = StreamState()
    loop = asyncio.get_running_loop()
    audio_queue: asyncio.Queue[bytes | None] = asyncio.Queue()

    # Stop-Mechanismus einrichten
    _setup_stop_mechanism(state, loop, external_stop_event, session_id)

    # Audio-Source initialisieren (modus-spezifisch)
    if mode == AudioSourceMode.WARM_STREAM:
        assert warm_stream_source is not None
        audio_result = _init_warm_stream(
            warm_source=warm_stream_source,
            state=state,
            loop=loop,
            audio_queue=audio_queue,
            session_id=session_id,
            play_ready=play_ready,
            stream_start=stream_start,
        )
    elif mode == AudioSourceMode.DAEMON:
        assert early_buffer is not None
        audio_result = _init_daemon_stream(
            early_buffer=early_buffer,
            state=state,
            loop=loop,
            audio_queue=audio_queue,
            session_id=session_id,
            play_ready=play_ready,
            stream_start=stream_start,
        )
    else:  # CLI Mode
        audio_result = _init_cli_stream(
            state=state,
            loop=loop,
            audio_queue=audio_queue,
            session_id=session_id,
            play_ready=play_ready,
            stream_start=stream_start,
            audio_level_callback=audio_level_callback,
        )

    try:
        async with _create_deepgram_connection(
            api_key,
            model=model,
            language=language,
            sample_rate=audio_result.sample_rate,
            channels=WHISPER_CHANNELS,
        ) as connection:
            # Event-Handler registrieren
            connection.on(
                EventType.MESSAGE, _create_message_handler(state, session_id)
            )
            connection.on(EventType.ERROR, _create_error_handler(state, session_id))
            connection.on(EventType.CLOSE, _create_close_handler(state, session_id))

            ws_time = (time.perf_counter() - stream_start) * 1000

            # CLI-Mode: Gepuffertes Audio an Queue senden und auf Direct-Mode umschalten
            # Während des WebSocket-Handshakes (~500ms) wurde Audio im Buffer gesammelt.
            # Jetzt: (1) Buffer leeren, (2) active=False → neue Chunks gehen direkt an Queue
            if audio_result.buffer_state is not None:
                with audio_result.buffer_state.lock:
                    # Umschalten: Buffering → Direct Mode
                    audio_result.buffer_state.active = False
                    buffered_count = len(audio_result.buffer_state.buffer)
                    for chunk in audio_result.buffer_state.buffer:
                        audio_queue.put_nowait(chunk)
                    audio_result.buffer_state.buffer.clear()
                logger.info(
                    f"[{session_id}] WebSocket verbunden nach {ws_time:.0f}ms, "
                    f"{buffered_count} gepufferte Chunks"
                )
            else:
                logger.info(f"[{session_id}] WebSocket verbunden nach {ws_time:.0f}ms")

            # Async Tasks für bidirektionale Kommunikation
            async def send_audio() -> None:
                """Sendet Audio-Chunks an Deepgram bis Sentinel."""
                last_chunk_at = time.monotonic()
                try:
                    # Auch nach Stop-Signal weiter senden, bis das None-Sentinel kommt.
                    # So werden bereits gepufferte Chunks nicht abgeschnitten.
                    while True:
                        try:
                            chunk = await asyncio.wait_for(
                                audio_queue.get(), timeout=AUDIO_QUEUE_POLL_INTERVAL
                            )
                            if chunk is None:
                                break
                            last_chunk_at = time.monotonic()
                            # Timeout für send_media um Hänger zu vermeiden
                            await asyncio.wait_for(
                                connection.send_media(chunk), timeout=SEND_MEDIA_TIMEOUT
                            )
                        except asyncio.TimeoutError:
                            # Fallback: Wenn kein Sentinel kommt, nach kurzer Leerlaufzeit beenden.
                            if (
                                state.stop_event.is_set()
                                and time.monotonic() - last_chunk_at >= FINALIZE_TIMEOUT
                            ):
                                logger.warning(
                                    f"[{session_id}] Audio-Send Abbruch ohne Sentinel "
                                    f"(idle >= {FINALIZE_TIMEOUT:.1f}s)"
                                )
                                break
                            continue
                except asyncio.CancelledError:
                    pass
                except Exception as e:
                    logger.error(f"[{session_id}] Audio-Send Fehler: {e}")
                    state.stream_error = e
                    state.stop_event.set()

            async def listen_for_messages() -> None:
                """Empfängt Transkripte von Deepgram."""
                try:
                    await connection.start_listening()
                except asyncio.CancelledError:
                    pass
                except Exception as e:
                    logger.debug(f"[{session_id}] Listener beendet: {e}")

            send_task = asyncio.create_task(send_audio())
            listen_task = asyncio.create_task(listen_for_messages())

            # Warten auf Stop
            await state.stop_event.wait()
            logger.info(f"[{session_id}] Stop-Signal empfangen")

            # Interim-Datei sofort löschen
            INTERIM_FILE.unlink(missing_ok=True)

            # Warm-Stream: Forwarder-Thread beenden BEVOR Graceful Shutdown
            # Damit alle Rest-Chunks in die Queue geschrieben werden, bevor
            # das None-Sentinel gesendet wird (verhindert abgeschnittene Wörter)
            if audio_result.forwarder_thread is not None:
                audio_result.forwarder_thread.join(timeout=FORWARDER_THREAD_JOIN_TIMEOUT)
                if audio_result.forwarder_thread.is_alive():
                    logger.warning(
                        f"[{session_id}] Forwarder-Thread Timeout - "
                        "letzte Audio-Chunks könnten verloren gehen"
                    )
                else:
                    logger.debug(f"[{session_id}] Forwarder-Thread beendet")

            # Graceful Shutdown durchführen
            await _graceful_shutdown(
                connection=connection,
                state=state,
                audio_queue=audio_queue,
                send_task=send_task,
                listen_task=listen_task,
                session_id=session_id,
            )

    finally:
        # Cleanup: Mikrofon freigeben
        if audio_result.mic_stream is not None:
            try:
                audio_result.mic_stream.stop()
                audio_result.mic_stream.close()
            except Exception as e:
                logger.debug(f"Mikrofon-Cleanup fehlgeschlagen: {e}")

        # Warm-Stream: Disarm (Forwarder ist Daemon-Thread, beendet sich automatisch)
        if warm_stream_source is not None:
            warm_stream_source.arm_event.clear()

        # Signal-Handler entfernen
        _cleanup_stop_mechanism(loop, external_stop_event)

    if state.stream_error:
        raise state.stream_error

    result = " ".join(state.final_transcripts)
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
        return DEFAULT_DEEPGRAM_MODEL

    def supports_streaming(self) -> bool:
        return True

    def transcribe(
        self,
        audio_path: Path | str,
        model: str | None = None,
        language: str | None = None,
    ) -> str:
        """Transkribiert eine Audio-Datei via REST API (nicht Streaming).

        Für Datei-Transkription nutze den regulären DeepgramProvider.

        Args:
            audio_path: Pfad zur Audio-Datei (Path oder str)
            model: Deepgram-Modell (optional)
            language: Sprachcode (optional)

        Returns:
            Transkribierter Text
        """
        from pathlib import Path as PathLib

        from .deepgram import DeepgramProvider

        path = audio_path if isinstance(audio_path, PathLib) else PathLib(audio_path)
        return DeepgramProvider().transcribe(path, model, language)

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
    model: str = DEFAULT_DEEPGRAM_MODEL,
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
    model: str = DEFAULT_DEEPGRAM_MODEL,
    language: str | None = None,
) -> str:
    """Sync Wrapper für async Deepgram Streaming.

    Verwendet asyncio.run() um die async Implementierung auszuführen.
    Für CLI/Signal-Integrationen: SIGUSR1 stoppt die Aufnahme sauber (nur Unix).
    """
    return asyncio.run(_transcribe_with_deepgram_stream_async(model, language))


# Alias für Rückwärtskompatibilität
_deepgram_stream_core = deepgram_stream_core


__all__ = [
    # Public API
    "DeepgramStreamProvider",
    "WarmStreamSource",
    "deepgram_stream_core",
    "transcribe_with_deepgram_stream",
    "transcribe_with_deepgram_stream_with_buffer",
    # Für Tests & Rückwärtskompatibilität
    "_transcribe_with_deepgram_stream_async",
    "_deepgram_stream_core",
    # Dataclasses (für Tests)
    "StreamState",
    "AudioSourceMode",
    "AudioSourceResult",
    "BufferState",
]
