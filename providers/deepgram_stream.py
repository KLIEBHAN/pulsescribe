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
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import (
    TYPE_CHECKING,
    Any,
    AsyncContextManager,
    AsyncIterator,
    Callable,
)

from config import (
    AUDIO_QUEUE_POLL_INTERVAL,
    CLI_BUFFER_LIMIT,
    DEEPGRAM_EMPTY_FINALIZE_GRACE_SECONDS,
    DEEPGRAM_CLOSE_TIMEOUT,
    DEEPGRAM_KEEPALIVE_INTERVAL_SECONDS,
    DEEPGRAM_TAIL_PADDING_SECONDS,
    DEEPGRAM_WS_URL,
    DEFAULT_DEEPGRAM_MODEL,
    DRAIN_EMPTY_THRESHOLD,
    DRAIN_MAX_DURATION,
    DRAIN_POLL_INTERVAL,
    FINALIZE_TIMEOUT,
    FORWARDER_THREAD_JOIN_TIMEOUT,
    INT16_MAX,
    INTERIM_FILE,
    INTERIM_THROTTLE_MS,
    PRE_DRAIN_DURATION,
    PRE_DRAIN_MIN_DURATION,
    SEND_MEDIA_TIMEOUT,
    WHISPER_BLOCKSIZE,
    WHISPER_CHANNELS,
    WHISPER_SAMPLE_RATE,
    get_input_device,
)
from providers._language import normalize_auto_language
from utils.audio_latency import (
    create_low_latency_input_stream,
    platform_audio_blocksize,
)
from utils.logging import get_session_id
from utils.timing import redacted_text_summary

if TYPE_CHECKING:
    from pathlib import Path

    import numpy as np
    import sounddevice as sd

    LiveResultResponse = Any
    AsyncV1SocketClient = Any

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
        drain_event: Optional, erlaubt Audio-Sammlung während Drain-Phase
        stream: Der laufende InputStream (für Cleanup/Reference)
    """

    audio_queue: queue.Queue[bytes]
    sample_rate: int
    arm_event: threading.Event
    stream: sd.InputStream
    drain_event: threading.Event | None = None

    def __post_init__(self) -> None:
        """Validiert Sample Rate."""
        if not (8000 <= self.sample_rate <= 48000):
            raise ValueError(
                f"sample_rate muss zwischen 8000-48000 liegen: {self.sample_rate}"
            )


@dataclass
class StreamState:
    """Zentraler State für Streaming-Session.

    Ersetzt nonlocal-Variablen durch ein explizites State-Objekt.
    Verbessert Testbarkeit und macht den Datenfluss transparenter.
    """

    final_transcripts: list[str] = field(default_factory=list)
    last_interim_write: float = 0.0
    last_interim_text: str = ""
    last_interim_written_text: str = ""
    stream_error: Exception | None = None
    stop_event: asyncio.Event = field(default_factory=asyncio.Event)
    finalize_done: asyncio.Event = field(default_factory=asyncio.Event)
    finalize_empty_ack_received: bool = False
    finalize_transcript_received: bool = False
    # Wird bei jedem Final-Transkript gesetzt; vor dem Finalize-Send geleert.
    # Erlaubt frühen Ausstieg aus der Empty-Finalize-Grace, sobald ein spätes
    # Final-Transkript eintrifft (statt immer die volle Grace-Zeit zu warten).
    final_transcript_event: asyncio.Event = field(default_factory=asyncio.Event)
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


@dataclass(frozen=True)
class DeepgramConnectionConfig:
    """Parameters that make a prewarmed Deepgram socket session-specific."""

    api_key: str = field(repr=False)
    model: str
    language: str | None
    sample_rate: int
    channels: int = WHISPER_CHANNELS


@dataclass
class _PreparedDeepgramConnection:
    """One unused websocket plus the context manager that owns it."""

    config: DeepgramConnectionConfig
    context: AsyncContextManager[AsyncV1SocketClient]
    connection: AsyncV1SocketClient
    keepalive_task: asyncio.Task[None] | None = None


DeepgramConnectionFactory = Callable[..., AsyncContextManager[Any]]


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

    language = normalize_auto_language(language)

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


class DeepgramWarmConnectionManager:
    """Preconnect one Deepgram websocket for the next transcription.

    Deepgram's ``CloseStream`` ends a streaming session, so a socket is claimed
    exactly once and replaced after every dictation. All websocket operations run
    on one dedicated asyncio loop to preserve loop affinity.
    """

    def __init__(
        self,
        *,
        keepalive_interval: float = DEEPGRAM_KEEPALIVE_INTERVAL_SECONDS,
    ) -> None:
        self._keepalive_interval = max(0.01, keepalive_interval)
        self._state_lock = threading.Lock()
        self._session_lock = threading.Lock()
        self._loop_started = threading.Event()
        self._prewarm_complete = threading.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._closing = False
        self._enabled = False
        self._desired_config: DeepgramConnectionConfig | None = None

        # The following fields are accessed only on the manager event loop.
        self._prepared: _PreparedDeepgramConnection | None = None
        self._prewarm_task: asyncio.Task[None] | None = None
        self._prewarm_config: DeepgramConnectionConfig | None = None
        self._active_task: asyncio.Task[Any] | None = None
        self._active_stop_event: threading.Event | None = None

    @staticmethod
    def _build_config(
        *,
        api_key: str | None,
        model: str,
        language: str | None,
        sample_rate: int,
        channels: int,
    ) -> DeepgramConnectionConfig:
        return DeepgramConnectionConfig(
            api_key=_validate_api_key(api_key),
            model=model.strip(),
            language=normalize_auto_language(language),
            sample_rate=sample_rate,
            channels=channels,
        )

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        with self._state_lock:
            if self._closing:
                raise RuntimeError("Deepgram warm connection manager is closed")
            loop = self._loop
            thread = self._thread
            if loop is not None and thread is not None and thread.is_alive():
                return loop

            self._loop_started.clear()
            thread = threading.Thread(
                target=self._run_event_loop,
                daemon=True,
                name="DeepgramWarmWebSocket",
            )
            self._thread = thread
            thread.start()

        if not self._loop_started.wait(timeout=2.0):
            raise RuntimeError("Deepgram warm websocket event loop did not start")
        loop = self._loop
        if loop is None:
            raise RuntimeError("Deepgram warm websocket event loop unavailable")
        return loop

    def _run_event_loop(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        self._loop_started.set()
        try:
            loop.run_forever()
        finally:
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(
                    asyncio.gather(*pending, return_exceptions=True)
                )
            loop.close()
            self._loop = None

    def prewarm(
        self,
        *,
        model: str,
        language: str | None,
        sample_rate: int,
        channels: int = WHISPER_CHANNELS,
        api_key: str | None = None,
    ) -> bool:
        """Start connecting in the background; never wait for the handshake."""
        try:
            config = self._build_config(
                api_key=api_key
                if api_key is not None
                else os.getenv("DEEPGRAM_API_KEY"),
                model=model,
                language=language,
                sample_rate=sample_rate,
                channels=channels,
            )
            _validate_model(config.model)
            loop = self._ensure_loop()
        except (RuntimeError, ValueError) as exc:
            logger.debug("Deepgram WebSocket Pre-Warm übersprungen: %s", exc)
            return False

        coroutine = self._request_prewarm(config)
        with self._state_lock:
            if self._closing:
                coroutine.close()
                return False
            self._enabled = True
            self._desired_config = config
            self._prewarm_complete.clear()
            try:
                asyncio.run_coroutine_threadsafe(coroutine, loop)
            except RuntimeError as exc:
                coroutine.close()
                logger.debug("Deepgram WebSocket Pre-Warm Scheduling fehlgeschlagen: %s", exc)
                return False
        return True

    def wait_until_ready(self, timeout: float | None = None) -> bool:
        """Wait until the latest prewarm attempt completed and a socket is ready."""
        if not self._prewarm_complete.wait(timeout=timeout):
            return False
        loop = self._loop
        if loop is None:
            return False
        coroutine = self._has_ready_connection()
        try:
            future = asyncio.run_coroutine_threadsafe(coroutine, loop)
        except RuntimeError:
            coroutine.close()
            return False
        try:
            return bool(future.result(timeout=timeout))
        except Exception:
            return False

    def transcribe(self, model: str, language: str | None, **kwargs: Any) -> str:
        """Run one transcription on the manager loop and replenish afterwards."""
        if not self._session_lock.acquire(blocking=False):
            raise RuntimeError("Deepgram warm websocket is already in use")
        try:
            loop = self._ensure_loop()
            coroutine = self._run_transcription(model, language, kwargs)
            with self._state_lock:
                if self._closing:
                    coroutine.close()
                    raise RuntimeError("Deepgram warm connection manager is closed")
                try:
                    future = asyncio.run_coroutine_threadsafe(coroutine, loop)
                except RuntimeError:
                    coroutine.close()
                    raise
            return future.result()
        finally:
            self._session_lock.release()

    def invalidate(self) -> None:
        """Discard an unused warm socket without stopping an active session."""
        with self._state_lock:
            self._enabled = False
            self._desired_config = None
            self._prewarm_complete.clear()
            if self._closing:
                return
            loop = self._loop
            if loop is None:
                return
            coroutine = self._invalidate_async()
            try:
                asyncio.run_coroutine_threadsafe(coroutine, loop)
            except RuntimeError:
                coroutine.close()

    def shutdown(self, *, timeout: float = 0.5) -> None:
        """Bounded, idempotent shutdown for app exit."""
        with self._state_lock:
            if self._closing:
                return
            self._closing = True
            self._enabled = False
            self._desired_config = None
            loop = self._loop
            thread = self._thread

        if loop is None or thread is None:
            return

        future = asyncio.run_coroutine_threadsafe(
            self._shutdown_async(grace_timeout=max(0.0, timeout * 0.6)),
            loop,
        )
        with suppress(Exception):
            future.result(timeout=max(0.0, timeout))
        with suppress(RuntimeError):
            loop.call_soon_threadsafe(loop.stop)
        if thread is not threading.current_thread():
            thread.join(timeout=max(0.0, timeout))

    async def _run_transcription(
        self,
        model: str,
        language: str | None,
        kwargs: dict[str, Any],
    ) -> str:
        current_task = asyncio.current_task()
        self._active_task = current_task
        self._active_stop_event = kwargs.get("external_stop_event")
        latency_callback = kwargs.get("latency_event_callback")

        @asynccontextmanager
        async def connection_factory(api_key: str, **connection_kwargs: Any):
            async with self._acquire_connection(
                api_key,
                **connection_kwargs,
            ) as lease:
                connection, was_prewarmed = lease
                _emit_latency_event(
                    latency_callback,
                    "deepgram_warm_ws_claimed"
                    if was_prewarmed
                    else "deepgram_warm_ws_fallback",
                )
                yield connection

        try:
            return await deepgram_stream_core(
                model,
                language,
                connection_factory=connection_factory,
                **kwargs,
            )
        finally:
            if self._active_task is current_task:
                self._active_task = None
                self._active_stop_event = None
            with self._state_lock:
                desired = self._desired_config
                should_replenish = self._enabled and not self._closing
            if desired is not None and should_replenish:
                await self._request_prewarm(desired)

    async def _request_prewarm(self, config: DeepgramConnectionConfig) -> None:
        with self._state_lock:
            if self._closing or not self._enabled or self._desired_config != config:
                return

        prepared = self._prepared
        if (
            prepared is not None
            and prepared.config == config
            and self._connection_is_open(prepared.connection)
        ):
            self._prewarm_complete.set()
            return

        task = self._prewarm_task
        if task is not None and not task.done():
            if self._prewarm_config == config:
                return
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

        self._prewarm_complete.clear()
        self._prewarm_config = config
        self._prewarm_task = asyncio.create_task(
            self._prewarm_connection(config),
            name="DeepgramWebSocketPrewarm",
        )

    async def _prewarm_connection(self, config: DeepgramConnectionConfig) -> None:
        current_task = asyncio.current_task()
        try:
            if self._prepared is not None:
                prepared, self._prepared = self._prepared, None
                await self._close_prepared(prepared)

            prepared = await self._open_prepared(config)
            with self._state_lock:
                still_desired = (
                    self._enabled
                    and not self._closing
                    and self._desired_config == config
                )
            if not still_desired:
                await self._close_prepared(prepared)
                return

            self._prepared = prepared
            prepared.keepalive_task = asyncio.create_task(
                self._keepalive(prepared),
                name="DeepgramWebSocketKeepAlive",
            )
            logger.info(
                "Deepgram WebSocket vorgewärmt: model=%s, lang=%s, %sHz",
                config.model,
                config.language or "auto",
                config.sample_rate,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.debug("Deepgram WebSocket Pre-Warm fehlgeschlagen: %s", exc)
        finally:
            if self._prewarm_task is current_task:
                self._prewarm_task = None
                self._prewarm_config = None
            with self._state_lock:
                should_signal = self._enabled and self._desired_config == config
            if should_signal:
                self._prewarm_complete.set()

    async def _open_prepared(
        self,
        config: DeepgramConnectionConfig,
    ) -> _PreparedDeepgramConnection:
        context = _create_deepgram_connection(
            config.api_key,
            model=config.model,
            language=config.language,
            sample_rate=config.sample_rate,
            channels=config.channels,
        )
        connection = await context.__aenter__()
        return _PreparedDeepgramConnection(
            config=config,
            context=context,
            connection=connection,
        )

    async def _keepalive(self, prepared: _PreparedDeepgramConnection) -> None:
        from deepgram.extensions.types.sockets import ListenV1ControlMessage

        try:
            while True:
                await asyncio.sleep(self._keepalive_interval)
                await prepared.connection.send_control(
                    ListenV1ControlMessage(type="KeepAlive")
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.debug("Deepgram Warm-WebSocket KeepAlive fehlgeschlagen: %s", exc)
            if self._prepared is prepared:
                self._prepared = None
            prepared.keepalive_task = None
            await self._close_prepared(prepared, cancel_keepalive=False)
            with self._state_lock:
                desired = self._desired_config
                should_reconnect = self._enabled and not self._closing
            if desired is not None and should_reconnect:
                await self._request_prewarm(desired)

    @asynccontextmanager
    async def _acquire_connection(
        self,
        api_key: str,
        *,
        model: str,
        language: str | None = None,
        sample_rate: int = WHISPER_SAMPLE_RATE,
        channels: int = WHISPER_CHANNELS,
        **connection_kwargs: Any,
    ) -> AsyncIterator[tuple[AsyncV1SocketClient, bool]]:
        config = self._build_config(
            api_key=api_key,
            model=model,
            language=language,
            sample_rate=sample_rate,
            channels=channels,
        )
        with self._state_lock:
            warm_enabled = self._enabled and not self._closing
            if warm_enabled:
                self._desired_config = config

        if warm_enabled:
            task = self._prewarm_task
            if task is not None and not task.done() and self._prewarm_config == config:
                await asyncio.gather(task, return_exceptions=True)

            prepared, self._prepared = self._prepared, None
            if prepared is not None:
                await self._stop_keepalive(prepared)
                if prepared.config == config and self._connection_is_open(
                    prepared.connection
                ):
                    logger.info("Deepgram Warm-WebSocket übernommen")
                    try:
                        yield prepared.connection, True
                    finally:
                        await self._close_prepared(prepared)
                    return
                await self._close_prepared(prepared)

        logger.debug("Kein nutzbarer Warm-WebSocket; öffne frische Verbindung")
        async with _create_deepgram_connection(
            api_key,
            model=model,
            language=language,
            sample_rate=sample_rate,
            channels=channels,
            **connection_kwargs,
        ) as connection:
            yield connection, False

    async def _stop_keepalive(self, prepared: _PreparedDeepgramConnection) -> None:
        task, prepared.keepalive_task = prepared.keepalive_task, None
        if task is None or task is asyncio.current_task():
            return
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    async def _close_prepared(
        self,
        prepared: _PreparedDeepgramConnection,
        *,
        cancel_keepalive: bool = True,
    ) -> None:
        async def cleanup() -> None:
            if cancel_keepalive:
                await self._stop_keepalive(prepared)
            with suppress(Exception):
                await prepared.context.__aexit__(None, None, None)

        cleanup_task = asyncio.create_task(cleanup())
        try:
            await asyncio.shield(cleanup_task)
        except asyncio.CancelledError:
            # A cancelled prewarm/reload must not leak its already-open socket.
            with suppress(asyncio.CancelledError, Exception):
                await asyncio.shield(cleanup_task)
            raise

    @staticmethod
    def _connection_is_open(connection: AsyncV1SocketClient) -> bool:
        websocket = getattr(connection, "_websocket", None)
        if websocket is None:
            return True
        if bool(getattr(websocket, "closed", False)):
            return False
        state = getattr(websocket, "state", None)
        return getattr(state, "name", None) != "CLOSED"

    async def _has_ready_connection(self) -> bool:
        prepared = self._prepared
        return prepared is not None and self._connection_is_open(prepared.connection)

    async def _invalidate_async(self) -> None:
        task, self._prewarm_task = self._prewarm_task, None
        self._prewarm_config = None
        if task is not None and task is not asyncio.current_task():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if self._prepared is not None:
            prepared, self._prepared = self._prepared, None
            await self._close_prepared(prepared)
        self._prewarm_complete.set()

    async def _shutdown_async(self, *, grace_timeout: float) -> None:
        active = self._active_task
        stop_event = self._active_stop_event
        if stop_event is not None:
            stop_event.set()

        if active is not None and active is not asyncio.current_task():
            done, _pending = await asyncio.wait({active}, timeout=grace_timeout)
            if active not in done:
                active.cancel()
                await asyncio.gather(active, return_exceptions=True)
        await self._invalidate_async()


# =============================================================================
# Mikrofon Setup (DRY)
# =============================================================================


def _stream_blocksize(sample_rate: int) -> int:
    """Return capture blocksize; Windows prefers small chunks for VAD/overlay."""
    default_blocksize = int(WHISPER_BLOCKSIZE * sample_rate / WHISPER_SAMPLE_RATE)
    return platform_audio_blocksize(sample_rate, default_blocksize)


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
    blocksize = _stream_blocksize(sample_rate)

    mic_stream = create_low_latency_input_stream(
        sd,
        logger=logger,
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

        # KEIN stop_event Check hier!
        # Das Mikrofon wird vor Graceful Shutdown gestoppt.
        # Bis dahin sollen alle Chunks verarbeitet werden (verhindert abgeschnittene Wörter).

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


def _forward_warm_chunk(
    *,
    loop: asyncio.AbstractEventLoop,
    audio_queue: asyncio.Queue[bytes | None],
    chunk: bytes,
) -> None:
    loop.call_soon_threadsafe(audio_queue.put_nowait, chunk)


def _forward_warm_stream_until_stop(
    *,
    warm_source: WarmStreamSource,
    state: StreamState,
    loop: asyncio.AbstractEventLoop,
    audio_queue: asyncio.Queue[bytes | None],
    session_id: str,
) -> None:
    while not state.stop_event.is_set():
        try:
            chunk = warm_source.audio_queue.get(timeout=AUDIO_QUEUE_POLL_INTERVAL)
            _forward_warm_chunk(loop=loop, audio_queue=audio_queue, chunk=chunk)
        except queue.Empty:
            continue
        except Exception as e:
            if not state.stop_event.is_set():
                logger.warning(f"[{session_id}] Warm-Stream Forwarder Error: {e}")
            break


def _drain_available_warm_chunks(
    *,
    warm_source: WarmStreamSource,
    loop: asyncio.AbstractEventLoop,
    audio_queue: asyncio.Queue[bytes | None],
) -> int:
    drained = 0
    while True:
        try:
            chunk = warm_source.audio_queue.get_nowait()
            _forward_warm_chunk(loop=loop, audio_queue=audio_queue, chunk=chunk)
            drained += 1
        except (queue.Empty, RuntimeError):
            return drained


def _pre_drain_warm_stream(
    *,
    warm_source: WarmStreamSource,
    loop: asyncio.AbstractEventLoop,
    audio_queue: asyncio.Queue[bytes | None],
) -> int:
    """Drain in-flight warm-stream chunks before the callback is disarmed.

    The warm callback is still armed here, so its last block may arrive a few
    milliseconds late. We therefore always wait at least ``PRE_DRAIN_MIN_DURATION``
    (~1 blocksize) to catch that block, but exit early once the queue stays empty
    instead of always burning the full ``PRE_DRAIN_DURATION``. The adaptive
    post-drain still runs afterwards, so this cannot lose tail audio while saving
    perceived stop->text latency.
    """
    drained = 0
    now = time.monotonic()
    floor_deadline = now + PRE_DRAIN_MIN_DURATION
    hard_deadline = now + PRE_DRAIN_DURATION
    empty_count = 0
    while time.monotonic() < hard_deadline:
        try:
            chunk = warm_source.audio_queue.get(timeout=DRAIN_POLL_INTERVAL)
            _forward_warm_chunk(loop=loop, audio_queue=audio_queue, chunk=chunk)
            drained += 1
            empty_count = 0
        except queue.Empty:
            empty_count += 1
            if (
                empty_count >= DRAIN_EMPTY_THRESHOLD
                and time.monotonic() >= floor_deadline
            ):
                break
        except RuntimeError:
            break
    return drained


def _post_drain_warm_stream(
    *,
    warm_source: WarmStreamSource,
    loop: asyncio.AbstractEventLoop,
    audio_queue: asyncio.Queue[bytes | None],
    session_id: str,
) -> None:
    if warm_source.drain_event is not None:
        warm_source.drain_event.set()
    warm_source.arm_event.clear()
    time.sleep(DRAIN_POLL_INTERVAL)

    try:
        drained = 0
        empty_count = 0
        drain_deadline = time.monotonic() + DRAIN_MAX_DURATION
        while empty_count < DRAIN_EMPTY_THRESHOLD:
            if time.monotonic() >= drain_deadline:
                logger.warning(
                    f"[{session_id}] Drain-Timeout nach {DRAIN_MAX_DURATION}s "
                    f"({drained} Chunks)"
                )
                break
            try:
                chunk = warm_source.audio_queue.get(timeout=DRAIN_POLL_INTERVAL)
                _forward_warm_chunk(loop=loop, audio_queue=audio_queue, chunk=chunk)
                drained += 1
                empty_count = 0
            except queue.Empty:
                empty_count += 1
            except RuntimeError:
                logger.debug(
                    f"[{session_id}] Event-Loop geschlossen, Drain abgebrochen"
                )
                break

        if drained > 0:
            logger.debug(f"[{session_id}] Warm-Stream: {drained} Rest-Chunks geleert")
    finally:
        # drain_event muss gelöscht werden, sonst sammelt der Callback dauerhaft.
        if warm_source.drain_event is not None:
            warm_source.drain_event.clear()


def _run_warm_stream_forwarder(
    *,
    warm_source: WarmStreamSource,
    state: StreamState,
    loop: asyncio.AbstractEventLoop,
    audio_queue: asyncio.Queue[bytes | None],
    session_id: str,
) -> None:
    """Leitet Audio von sync Queue an async Queue weiter und drained Rest-Chunks."""
    _forward_warm_stream_until_stop(
        warm_source=warm_source,
        state=state,
        loop=loop,
        audio_queue=audio_queue,
        session_id=session_id,
    )

    # Zwischen queue.get(timeout) und stop_event Check können Chunks eintreffen.
    immediate_drained = _drain_available_warm_chunks(
        warm_source=warm_source,
        loop=loop,
        audio_queue=audio_queue,
    )
    if immediate_drained > 0:
        logger.debug(f"[{session_id}] Immediate-Drain: {immediate_drained} Chunks")

    # Callback läuft noch: kurze Pre-Drain-Phase leert Sounddevice-Buffer.
    pre_drained = _pre_drain_warm_stream(
        warm_source=warm_source,
        loop=loop,
        audio_queue=audio_queue,
    )
    if pre_drained > 0:
        logger.debug(f"[{session_id}] Pre-Drain: {pre_drained} Chunks geleert")

    _post_drain_warm_stream(
        warm_source=warm_source,
        loop=loop,
        audio_queue=audio_queue,
        session_id=session_id,
    )


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

    forwarder_thread = threading.Thread(
        target=_run_warm_stream_forwarder,
        kwargs={
            "warm_source": warm_source,
            "state": state,
            "loop": loop,
            "audio_queue": audio_queue,
            "session_id": session_id,
        },
        daemon=True,
        name="WarmStreamForwarder",
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

    _log_init_complete(
        session_id, stream_start, "Daemon-Mode Mikrofon bereit", play_ready
    )

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


def _emit_latency_event(
    callback: Callable[[str, dict[str, Any] | None], None] | None,
    name: str,
    **fields: Any,
) -> None:
    """Emit optional latency diagnostics without affecting streaming."""
    if callback is None:
        return
    try:
        callback(name, fields or None)
    except Exception as exc:
        logger.debug("Latency diagnostics callback failed: %s", exc)


def _write_interim_text(path: Path, transcript: str) -> None:
    """Write interim text atomically so readers never see partial payloads."""
    if not hasattr(path, "with_name") or not hasattr(path, "replace"):
        path.write_text(transcript, encoding="utf-8")
        return

    tmp_path = path.with_name(f"{path.name}.tmp")
    try:
        tmp_path.write_text(transcript, encoding="utf-8")
        tmp_path.replace(path)
    except Exception:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _mark_finalize_response(
    state: StreamState,
    *,
    has_transcript: bool,
) -> None:
    if has_transcript:
        state.finalize_transcript_received = True
        state.final_transcript_event.set()
    else:
        state.finalize_empty_ack_received = True
    state.finalize_done.set()


def _handle_final_transcript(
    state: StreamState,
    *,
    session_id: str,
    transcript: str,
) -> None:
    state.final_transcripts.append(transcript)
    state.final_transcript_event.set()
    logger.info(f"[{session_id}] Final: {redacted_text_summary(transcript)}")


def _handle_interim_transcript(
    state: StreamState,
    *,
    session_id: str,
    transcript: str,
    interim_text_callback: Callable[[str], None] | None,
) -> None:
    previous_interim = state.last_interim_text
    state.last_interim_text = transcript
    if transcript == previous_interim and transcript == state.last_interim_written_text:
        return

    now = time.perf_counter()
    if transcript == state.last_interim_written_text:
        return
    if (now - state.last_interim_write) * 1000 < INTERIM_THROTTLE_MS:
        return

    if interim_text_callback is not None:
        try:
            interim_text_callback(transcript)
        except Exception as e:
            logger.debug(f"[{session_id}] Interim-Callback fehlgeschlagen: {e}")
    try:
        _write_interim_text(INTERIM_FILE, transcript)
        state.last_interim_write = now
        state.last_interim_written_text = transcript
        logger.debug(f"[{session_id}] Interim: {redacted_text_summary(transcript)}")
    except OSError as e:
        logger.warning(f"[{session_id}] Interim-Write fehlgeschlagen: {e}")


def _create_message_handler(
    state: StreamState,
    session_id: str,
    interim_text_callback: Callable[[str], None] | None = None,
) -> Callable[[LiveResultResponse | Any], None]:
    """Erstellt Handler für Deepgram-Nachrichten."""

    def on_message(result: LiveResultResponse | Any) -> None:
        """Sammelt Transkripte aus Deepgram-Responses."""
        # from_finalize=True signalisiert: Server hat Rest-Audio verarbeitet
        from_finalize = bool(getattr(result, "from_finalize", False))

        transcript = _extract_transcript(result)
        if not transcript:
            if from_finalize:
                _mark_finalize_response(state, has_transcript=False)
            return

        if from_finalize:
            _mark_finalize_response(state, has_transcript=True)

        is_final = getattr(result, "is_final", False)
        if is_final:
            _handle_final_transcript(
                state,
                session_id=session_id,
                transcript=transcript,
            )
        else:
            _handle_interim_transcript(
                state,
                session_id=session_id,
                transcript=transcript,
                interim_text_callback=interim_text_callback,
            )

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
        # Bei Verbindungsfehlern kommen keine weiteren Finalize-Events mehr.
        state.finalize_done.set()
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
        # Wenn der Socket bereits geschlossen ist, kann kein separates
        # Finalize-Ack mehr eintreffen. Das gilt für den Shutdown-Pfad als
        # terminales Signal und verhindert unnötige Timeout-Wartezeiten.
        state.finalize_done.set()
        state.stop_event.set()

    return on_close


def _resolve_stream_result(state: StreamState, session_id: str) -> str:
    """Return the best available transcript for the completed stream.

    Prefer confirmed final transcripts. If Deepgram only emitted interim text
    before the socket closed, fall back to the last interim instead of losing
    the user's short dictation entirely.
    """
    final_result = " ".join(part for part in state.final_transcripts if part).strip()
    if final_result:
        return final_result

    interim_fallback = state.last_interim_text.strip()
    if interim_fallback:
        logger.info(
            "[%s] Kein Final-Transkript erhalten, nutze letztes Interim als Fallback: %s",
            session_id,
            redacted_text_summary(interim_fallback),
        )
        return interim_fallback

    return ""


# =============================================================================
# Stop Event Watcher
# =============================================================================


def _setup_stop_mechanism(
    state: StreamState,
    loop: asyncio.AbstractEventLoop,
    external_stop_event: threading.Event | None,
    session_id: str,
    *,
    stop_grace_seconds: float = 0.0,
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
        stop_grace_seconds: Zusätzliche Aufnahmezeit nach externem Stop-Signal
    """
    if external_stop_event is not None:
        # Unified-Daemon-Mode: Externes threading.Event überwachen
        def _watch_external_stop() -> None:
            external_stop_event.wait()
            if stop_grace_seconds > 0:
                logger.debug(f"[{session_id}] Stop-Grace: {stop_grace_seconds:.2f}s")
                time.sleep(stop_grace_seconds)
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

    elif (
        sys.platform != "win32"
        and threading.current_thread() is threading.main_thread()
    ):
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
    sample_rate: int = WHISPER_SAMPLE_RATE,
    latency_event_callback: Callable[[str, dict[str, Any] | None], None] | None = None,
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
    # Audio-Callbacks nutzen loop.call_soon_threadsafe(); einmal yielden, damit
    # bereits geplante letzte Chunks vor Tail-Padding und Sentinel in der Queue landen.
    await asyncio.sleep(0)
    tail_padding = _build_tail_padding_chunk(sample_rate)
    if tail_padding:
        await audio_queue.put(tail_padding)
        _emit_latency_event(
            latency_event_callback,
            "deepgram_tail_padding",
            seconds=DEEPGRAM_TAIL_PADDING_SECONDS,
        )
        logger.debug(
            f"[{session_id}] Deepgram Tail-Padding: "
            f"{DEEPGRAM_TAIL_PADDING_SECONDS:.2f}s"
        )
    await audio_queue.put(None)
    await send_task

    # 2. Finalize senden. Event vorher zurücksetzen: Für den Grace-Early-Exit
    # zählen nur Final-Transkripte, die NACH dem Finalize eintreffen.
    state.final_transcript_event.clear()
    logger.info(f"[{session_id}] Sende Finalize...")
    _emit_latency_event(latency_event_callback, "deepgram_finalize_send")
    t_finalize_start = time.perf_counter()
    try:
        await connection.send_control(ListenV1ControlMessage(type="Finalize"))
    except Exception as e:
        logger.warning(f"[{session_id}] Finalize fehlgeschlagen: {e}")

    # 3. Warten auf finale Transkripte
    try:
        await asyncio.wait_for(state.finalize_done.wait(), timeout=FINALIZE_TIMEOUT)
        t_finalize = (time.perf_counter() - t_finalize_start) * 1000
        logger.info(f"[{session_id}] Finalize abgeschlossen ({t_finalize:.0f}ms)")
        _emit_latency_event(
            latency_event_callback,
            "deepgram_finalize_done",
            elapsed_ms=round(t_finalize, 3),
        )
        if (
            state.finalize_empty_ack_received
            and not state.finalize_transcript_received
            and DEEPGRAM_EMPTY_FINALIZE_GRACE_SECONDS > 0
        ):
            # Nicht stur die volle Grace-Zeit warten: Sobald ein spätes
            # Final-Transkript eintrifft, geht es sofort weiter.
            try:
                await asyncio.wait_for(
                    state.final_transcript_event.wait(),
                    timeout=DEEPGRAM_EMPTY_FINALIZE_GRACE_SECONDS,
                )
                logger.debug(
                    f"[{session_id}] Empty-Finalize-Grace: spätes Transkript "
                    "eingetroffen, Grace vorzeitig beendet"
                )
            except asyncio.TimeoutError:
                pass
    except asyncio.TimeoutError:
        t_finalize = (time.perf_counter() - t_finalize_start) * 1000
        logger.warning(
            f"[{session_id}] Finalize-Timeout nach {t_finalize:.0f}ms "
            f"(max: {FINALIZE_TIMEOUT}s)"
        )
        _emit_latency_event(
            latency_event_callback,
            "deepgram_finalize_timeout",
            elapsed_ms=round(t_finalize, 3),
            timeout_s=FINALIZE_TIMEOUT,
        )

    # 4. CloseStream senden
    logger.info(f"[{session_id}] Sende CloseStream...")
    _emit_latency_event(latency_event_callback, "deepgram_close_send")
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


def _build_tail_padding_chunk(sample_rate: int) -> bytes:
    """Return linear16 silence for Deepgram's final decoder context."""
    if DEEPGRAM_TAIL_PADDING_SECONDS <= 0:
        return b""
    if sample_rate <= 0:
        return b""

    sample_count = int(sample_rate * WHISPER_CHANNELS * DEEPGRAM_TAIL_PADDING_SECONDS)
    if sample_count <= 0:
        return b""
    return b"\x00\x00" * sample_count


async def _finish_warm_forwarder(
    audio_result: AudioSourceResult,
    session_id: str,
) -> None:
    """Stop the warm-stream forwarder and flush thread-scheduled queue puts."""
    if audio_result.forwarder_thread is None:
        return

    # Warm-Stream: Forwarder-Thread beenden (hat eigenen Pre-Drain)
    audio_result.forwarder_thread.join(timeout=FORWARDER_THREAD_JOIN_TIMEOUT)
    if audio_result.forwarder_thread.is_alive():
        logger.warning(
            f"[{session_id}] Forwarder-Thread Timeout - "
            "letzte Audio-Chunks könnten verloren gehen"
        )
    else:
        logger.debug(f"[{session_id}] Forwarder-Thread beendet")

    # The forwarder uses loop.call_soon_threadsafe(audio_queue.put_nowait, chunk).
    # Yield once so those callbacks run before _graceful_shutdown adds the
    # None sentinel; otherwise the sender can stop before the final chunks.
    await asyncio.sleep(0)


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


def _select_audio_source_mode(
    *,
    early_buffer: list[bytes] | None,
    warm_stream_source: WarmStreamSource | None,
) -> tuple[AudioSourceMode, str]:
    if warm_stream_source is not None:
        return AudioSourceMode.WARM_STREAM, "Warm-Stream"
    if early_buffer:
        return AudioSourceMode.DAEMON, f"Daemon, {len(early_buffer)} early chunks"
    return AudioSourceMode.CLI, "CLI (Buffering)"


def _init_audio_source(
    *,
    mode: AudioSourceMode,
    early_buffer: list[bytes] | None,
    warm_stream_source: WarmStreamSource | None,
    state: StreamState,
    loop: asyncio.AbstractEventLoop,
    audio_queue: asyncio.Queue[bytes | None],
    session_id: str,
    play_ready: bool,
    stream_start: float,
    audio_level_callback: Callable[[float], None] | None,
) -> AudioSourceResult:
    if mode == AudioSourceMode.WARM_STREAM:
        assert warm_stream_source is not None
        return _init_warm_stream(
            warm_source=warm_stream_source,
            state=state,
            loop=loop,
            audio_queue=audio_queue,
            session_id=session_id,
            play_ready=play_ready,
            stream_start=stream_start,
        )
    if mode == AudioSourceMode.DAEMON:
        assert early_buffer is not None
        return _init_daemon_stream(
            early_buffer=early_buffer,
            state=state,
            loop=loop,
            audio_queue=audio_queue,
            session_id=session_id,
            play_ready=play_ready,
            stream_start=stream_start,
        )

    return _init_cli_stream(
        state=state,
        loop=loop,
        audio_queue=audio_queue,
        session_id=session_id,
        play_ready=play_ready,
        stream_start=stream_start,
        audio_level_callback=audio_level_callback,
    )


def _register_deepgram_handlers(
    connection: AsyncV1SocketClient,
    *,
    state: StreamState,
    session_id: str,
    interim_text_callback: Callable[[str], None] | None,
) -> None:
    from deepgram.core.events import EventType

    connection.on(
        EventType.MESSAGE,
        _create_message_handler(state, session_id, interim_text_callback),
    )
    connection.on(EventType.ERROR, _create_error_handler(state, session_id))
    connection.on(EventType.CLOSE, _create_close_handler(state, session_id))


def _flush_buffered_audio_after_connect(
    *,
    audio_result: AudioSourceResult,
    audio_queue: asyncio.Queue[bytes | None],
    session_id: str,
    stream_start: float,
) -> float:
    ws_time = (time.perf_counter() - stream_start) * 1000
    if audio_result.buffer_state is None:
        logger.info(f"[{session_id}] WebSocket verbunden nach {ws_time:.0f}ms")
        return ws_time

    with audio_result.buffer_state.lock:
        audio_result.buffer_state.active = False
        buffered_count = len(audio_result.buffer_state.buffer)
        for chunk in audio_result.buffer_state.buffer:
            audio_queue.put_nowait(chunk)
        audio_result.buffer_state.buffer.clear()
    logger.info(
        f"[{session_id}] WebSocket verbunden nach {ws_time:.0f}ms, "
        f"{buffered_count} gepufferte Chunks"
    )
    return ws_time


async def _send_audio_to_deepgram(
    *,
    connection: AsyncV1SocketClient,
    state: StreamState,
    audio_queue: asyncio.Queue[bytes | None],
    session_id: str,
) -> None:
    """Sendet Audio-Chunks an Deepgram bis Sentinel."""
    last_chunk_at = time.monotonic()
    try:
        while True:
            try:
                chunk = await asyncio.wait_for(
                    audio_queue.get(), timeout=AUDIO_QUEUE_POLL_INTERVAL
                )
                if chunk is None:
                    break
                last_chunk_at = time.monotonic()
                await asyncio.wait_for(
                    connection.send_media(chunk), timeout=SEND_MEDIA_TIMEOUT
                )
            except asyncio.TimeoutError:
                if (
                    state.stop_event.is_set()
                    and time.monotonic() - last_chunk_at >= FINALIZE_TIMEOUT
                ):
                    logger.warning(
                        f"[{session_id}] Audio-Send Abbruch ohne Sentinel "
                        f"(idle >= {FINALIZE_TIMEOUT:.1f}s)"
                    )
                    break
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.error(f"[{session_id}] Audio-Send Fehler: {e}")
        state.stream_error = e
        state.stop_event.set()


async def _listen_for_deepgram_messages(
    *,
    connection: AsyncV1SocketClient,
    session_id: str,
) -> None:
    """Empfängt Transkripte von Deepgram."""
    try:
        await connection.start_listening()
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.debug(f"[{session_id}] Listener beendet: {e}")


async def _stop_audio_source_before_shutdown(
    *,
    audio_result: AudioSourceResult,
    session_id: str,
) -> None:
    if audio_result.forwarder_thread is not None:
        await _finish_warm_forwarder(audio_result, session_id)
        return

    if audio_result.mic_stream is None:
        return

    await asyncio.sleep(PRE_DRAIN_DURATION)
    try:
        audio_result.mic_stream.stop()
        logger.debug(f"[{session_id}] Mikrofon gestoppt (vor Graceful Shutdown)")
    except Exception as e:
        logger.debug(f"[{session_id}] Mikrofon-Stop fehlgeschlagen: {e}")


def _cleanup_deepgram_audio_source(
    *,
    audio_result: AudioSourceResult,
    warm_stream_source: WarmStreamSource | None,
) -> None:
    if audio_result.mic_stream is not None:
        try:
            if audio_result.mic_stream.active:
                audio_result.mic_stream.stop()
            audio_result.mic_stream.close()
        except Exception as e:
            logger.debug(f"Mikrofon-Cleanup fehlgeschlagen: {e}")

    if warm_stream_source is None:
        return

    if warm_stream_source.arm_event.is_set():
        logger.warning(
            "Warm-Stream arm_event noch gesetzt im finally-Block - "
            "Forwarder-Thread wurde vermutlich vorzeitig beendet"
        )
    drain_event = warm_stream_source.drain_event
    if drain_event is not None:
        drain_event.set()
    warm_stream_source.arm_event.clear()
    if drain_event is not None:
        drain_event.clear()


async def deepgram_stream_core(
    model: str,
    language: str | None,
    *,
    early_buffer: list[bytes] | None = None,
    play_ready: bool = True,
    external_stop_event: threading.Event | None = None,
    audio_level_callback: Callable[[float], None] | None = None,
    interim_text_callback: Callable[[str], None] | None = None,
    latency_event_callback: Callable[[str, dict[str, Any] | None], None] | None = None,
    warm_stream_source: WarmStreamSource | None = None,
    stop_grace_seconds: float = 0.0,
    connection_factory: DeepgramConnectionFactory | None = None,
) -> str:
    """Gemeinsamer Streaming-Core für Deepgram (SDK v5.3).

    Args:
        model: Deepgram-Modell (z.B. "nova-3")
        language: Sprachcode oder None für Auto-Detection
        early_buffer: Vorab gepuffertes Audio (für Daemon-Mode)
        play_ready: Ready-Sound nach Mikrofon-Init spielen (für CLI)
        external_stop_event: threading.Event zum externen Stoppen (statt SIGUSR1)
        audio_level_callback: Callback für Audio-Level Updates
        interim_text_callback: Optionaler direkter Callback für Interim-Text
        latency_event_callback: Optionaler Callback für strukturierte Latenz-Events
        warm_stream_source: Externes WarmStreamSource für instant-start (Windows)
        stop_grace_seconds: Zusätzliche Aufnahmezeit nach externem Stop-Signal
        connection_factory: Optionaler Connection-Provider für einen vorgewärmten Socket

    Drei Modi:
    - CLI (early_buffer=None): Buffering während WebSocket-Connect
    - Daemon (early_buffer=[...]): Buffer direkt in Queue, kein Buffering
    - Warm-Stream (warm_stream_source): Mikrofon bereits offen, instant-start

    Returns:
        Transkribierter Text als String

    Raises:
        ValueError: Bei ungültigen Parametern oder fehlendem API-Key
    """
    # Validierung
    _validate_model(model)
    api_key = _validate_api_key(os.getenv("DEEPGRAM_API_KEY"))

    session_id = get_session_id()
    stream_start = time.perf_counter()
    language = normalize_auto_language(language)

    # Modus bestimmen
    mode, mode_str = _select_audio_source_mode(
        early_buffer=early_buffer,
        warm_stream_source=warm_stream_source,
    )
    logger.info(
        f"[{session_id}] Deepgram-Stream ({mode_str}): {model}, "
        f"lang={language or 'auto'}"
    )

    # Zentraler State
    state = StreamState()
    loop = asyncio.get_running_loop()
    audio_queue: asyncio.Queue[bytes | None] = asyncio.Queue()

    # Stop-Mechanismus einrichten
    _setup_stop_mechanism(
        state,
        loop,
        external_stop_event,
        session_id,
        stop_grace_seconds=max(0.0, stop_grace_seconds),
    )

    # Audio-Source initialisieren (modus-spezifisch)
    audio_result = _init_audio_source(
        mode=mode,
        early_buffer=early_buffer,
        warm_stream_source=warm_stream_source,
        state=state,
        loop=loop,
        audio_queue=audio_queue,
        session_id=session_id,
        play_ready=play_ready,
        stream_start=stream_start,
        audio_level_callback=audio_level_callback,
    )

    create_connection = connection_factory or _create_deepgram_connection

    try:
        async with create_connection(
            api_key,
            model=model,
            language=language,
            sample_rate=audio_result.sample_rate,
            channels=WHISPER_CHANNELS,
        ) as connection:
            # Event-Handler registrieren
            _register_deepgram_handlers(
                connection,
                state=state,
                session_id=session_id,
                interim_text_callback=interim_text_callback,
            )

            ws_time = _flush_buffered_audio_after_connect(
                audio_result=audio_result,
                audio_queue=audio_queue,
                session_id=session_id,
                stream_start=stream_start,
            )
            _emit_latency_event(
                latency_event_callback,
                "deepgram_ws_connected",
                elapsed_ms=round(ws_time, 3),
            )

            # Async Tasks für bidirektionale Kommunikation
            send_task = asyncio.create_task(
                _send_audio_to_deepgram(
                    connection=connection,
                    state=state,
                    audio_queue=audio_queue,
                    session_id=session_id,
                )
            )
            listen_task = asyncio.create_task(
                _listen_for_deepgram_messages(
                    connection=connection,
                    session_id=session_id,
                )
            )

            # Warten auf Stop
            await state.stop_event.wait()
            logger.info(f"[{session_id}] Stop-Signal empfangen")
            _emit_latency_event(latency_event_callback, "deepgram_stop_signal")

            # Interim-Datei sofort löschen
            INTERIM_FILE.unlink(missing_ok=True)

            # === AUDIO-SOURCE BEENDEN (vor Graceful Shutdown) ===
            # Wichtig: Audio-Quellen müssen BEVOR das None-Sentinel gesendet wird
            # beendet werden, damit alle Rest-Chunks in der Queue landen.

            await _stop_audio_source_before_shutdown(
                audio_result=audio_result,
                session_id=session_id,
            )

            # Graceful Shutdown durchführen
            await _graceful_shutdown(
                connection=connection,
                state=state,
                audio_queue=audio_queue,
                send_task=send_task,
                listen_task=listen_task,
                session_id=session_id,
                sample_rate=audio_result.sample_rate,
                latency_event_callback=latency_event_callback,
            )

    finally:
        _cleanup_deepgram_audio_source(
            audio_result=audio_result,
            warm_stream_source=warm_stream_source,
        )

        # Signal-Handler entfernen
        _cleanup_stop_mechanism(loop, external_stop_event)

    if state.stream_error:
        raise state.stream_error

    result = _resolve_stream_result(state, session_id)
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
    "DeepgramWarmConnectionManager",
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
    "DeepgramConnectionConfig",
]
