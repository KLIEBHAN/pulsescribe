#!/usr/bin/env python3
"""
whisper_go – Audio-Transkription mit OpenAI Whisper.

Unterstützt sowohl die OpenAI API als auch lokale Whisper-Modelle.
Transkripte werden auf stdout ausgegeben, Status auf stderr.

Usage:
    python transcribe.py audio.mp3
    python transcribe.py audio.mp3 --mode local
    python transcribe.py --record --copy
"""

# Startup-Timing: Zeit erfassen BEVOR andere Imports laden
import time as _time_module  # noqa: E402 - muss vor anderen Imports sein

_PROCESS_START = _time_module.perf_counter()

import argparse  # noqa: E402
import json  # noqa: E402
import logging  # noqa: E402
import os  # noqa: E402
import signal  # noqa: E402
import sys  # noqa: E402
import tempfile  # noqa: E402
import threading  # noqa: E402
import uuid  # noqa: E402
from collections.abc import AsyncIterator  # noqa: E402
from contextlib import asynccontextmanager, contextmanager  # noqa: E402
from typing import TYPE_CHECKING  # noqa: E402
from logging.handlers import RotatingFileHandler  # noqa: E402
from pathlib import Path  # noqa: E402

if TYPE_CHECKING:
    from deepgram.listen.v1.socket_client import AsyncV1SocketClient

from prompts import DEFAULT_APP_CONTEXTS, get_prompt_for_context  # noqa: E402

# Import-Zeit messen (alle Standardlib-Imports abgeschlossen)
_IMPORTS_DONE = _time_module.perf_counter()
time = _time_module  # Alias für restlichen Code

# =============================================================================
# Audio-Konfiguration
# =============================================================================

# Whisper erwartet Audio mit 16kHz – andere Sampleraten führen zu schlechteren Ergebnissen
WHISPER_SAMPLE_RATE = 16000
WHISPER_CHANNELS = 1
WHISPER_BLOCKSIZE = 1024

# =============================================================================
# Standard-Modelle pro Provider
# =============================================================================

DEFAULT_API_MODEL = "gpt-4o-transcribe"
DEFAULT_LOCAL_MODEL = "turbo"
DEFAULT_DEEPGRAM_MODEL = "nova-3"
DEFAULT_GROQ_MODEL = "whisper-large-v3"
DEFAULT_REFINE_MODEL = "gpt-5-nano"
DEFAULT_GROQ_REFINE_MODEL = "llama-3.3-70b-versatile"

# =============================================================================
# API-Endpunkte
# =============================================================================

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# =============================================================================
# Dateipfade für IPC und Konfiguration
# =============================================================================

# IPC-Dateien für Kommunikation zwischen Prozessen (Raycast, Hotkey-Daemon, Menübar)
# Alle Dateien liegen in /tmp für schnellen Zugriff und automatische Bereinigung
TEMP_RECORDING_FILENAME = "whisper_recording.wav"
PID_FILE = Path("/tmp/whisper_go.pid")           # Aktive Aufnahme-PID → für SIGUSR1 Stop
TRANSCRIPT_FILE = Path("/tmp/whisper_go.transcript")  # Fertiges Transkript
ERROR_FILE = Path("/tmp/whisper_go.error")       # Fehlermeldungen für UI-Feedback
STATE_FILE = Path("/tmp/whisper_go.state")       # Aktueller Status (recording/transcribing/done/error)
INTERIM_FILE = Path("/tmp/whisper_go.interim")   # Live-Transkript während Aufnahme

# Streaming-Timeouts
INTERIM_THROTTLE_MS = 150    # Max. Update-Rate für Interim-File (Menübar pollt 200ms)
FINALIZE_TIMEOUT = 2.0       # Warten auf finale Transkripte nach Deepgram-Finalize
DEEPGRAM_WS_URL = "wss://api.deepgram.com/v1/listen"
DEEPGRAM_CLOSE_TIMEOUT = 0.5 # Schneller WebSocket-Shutdown (SDK Default: 10s)

# Lokale Pfade
SCRIPT_DIR = Path(__file__).resolve().parent
LOG_DIR = SCRIPT_DIR / "logs"
LOG_FILE = LOG_DIR / "whisper_go.log"
VOCABULARY_FILE = Path.home() / ".whisper_go" / "vocabulary.json"

# =============================================================================
# Laufzeit-State (modulglobal)
# =============================================================================

logger = logging.getLogger("whisper_go")
_session_id: str = ""  # Wird pro Durchlauf in setup_logging() gesetzt
_custom_app_contexts_cache: dict | None = None  # Cache für WHISPER_GO_APP_CONTEXTS

# API-Client Singletons (Lazy Init) – spart ~100-300ms pro Aufruf
_openai_client = None
_deepgram_client = None
_groq_client = None


def _generate_session_id() -> str:
    """Erzeugt kurze, lesbare Session-ID (8 Zeichen)."""
    return uuid.uuid4().hex[:8]


def _format_duration(milliseconds: float) -> str:
    """Formatiert Dauer menschenlesbar: ms für kurze, s für längere Zeiten."""
    if milliseconds >= 1000:
        return f"{milliseconds / 1000:.2f}s"
    return f"{milliseconds:.0f}ms"


def _log_preview(text: str, max_length: int = 100) -> str:
    """Kürzt Text für Log-Ausgabe mit Ellipsis wenn nötig."""
    if len(text) <= max_length:
        return text
    return f"{text[:max_length]}..."


@contextmanager
def timed_operation(name: str):
    """Kontextmanager für Zeitmessung mit automatischem Logging."""
    start = time.perf_counter()
    try:
        yield
    finally:
        elapsed_ms = (time.perf_counter() - start) * 1000
        logger.info(f"[{_session_id}] {name}: {_format_duration(elapsed_ms)}")


def setup_logging(debug: bool = False) -> None:
    """Konfiguriert Logging: Datei mit Rotation + optional stderr."""
    global _session_id
    
    # Session-ID nur einmal generieren
    if not _session_id:
        _session_id = _generate_session_id()

    # Verhindere doppelte Handler bei mehrfachem Aufruf
    if logger.handlers:
        logger.setLevel(logging.DEBUG if debug else logging.INFO)
        return

    logger.setLevel(logging.DEBUG if debug else logging.INFO)

    # Log-Verzeichnis erstellen falls nicht vorhanden
    LOG_DIR.mkdir(exist_ok=True)

    # Datei-Handler mit Rotation (max 1MB, 3 Backups)
    file_handler = RotatingFileHandler(
        LOG_FILE, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%H:%M:%S")
    )
    logger.addHandler(file_handler)

    # Stderr-Handler (nur im Debug-Modus)
    if debug:
        stderr_handler = logging.StreamHandler(sys.stderr)
        stderr_handler.setLevel(logging.DEBUG)
        stderr_handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
        logger.addHandler(stderr_handler)


def log(message: str) -> None:
    """Status-Meldung auf stderr.

    Warum stderr? Hält stdout sauber für Pipes (z.B. `transcribe.py | pbcopy`).
    """
    print(message, file=sys.stderr)


def error(message: str) -> None:
    """Fehlermeldung auf stderr."""
    print(f"Fehler: {message}", file=sys.stderr)


def load_environment() -> None:
    """Lädt .env-Datei falls python-dotenv installiert ist."""
    try:
        from dotenv import load_dotenv

        env_file = SCRIPT_DIR / ".env"
        load_dotenv(env_file if env_file.exists() else None)
    except ImportError:
        pass


def copy_to_clipboard(text: str) -> bool:
    """Kopiert Text in die Zwischenablage. Gibt True bei Erfolg zurück."""
    try:
        import pyperclip

        pyperclip.copy(text)
        return True
    except Exception:
        return False


# =============================================================================
# Sound-Playback via CoreAudio (ultra-low latency: ~0.2ms statt ~500ms mit afplay)
# =============================================================================


class _CoreAudioPlayer:
    """
    CoreAudio-Sound-Playback mit Fallback auf afplay.

    Cached Sound-IDs für schnelles Abspielen (~0.2ms).
    Singleton-Instanz via _get_sound_player().
    """

    def __init__(self) -> None:
        self._sound_ids: dict[str, int] = {}
        self._audio_toolbox = None
        self._core_foundation = None
        self._use_fallback = False

        try:
            import ctypes

            self._ctypes = ctypes
            self._audio_toolbox = ctypes.CDLL(
                "/System/Library/Frameworks/AudioToolbox.framework/AudioToolbox"
            )
            self._core_foundation = ctypes.CDLL(
                "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
            )

            # CFStringCreateWithCString
            self._core_foundation.CFStringCreateWithCString.restype = ctypes.c_void_p
            self._core_foundation.CFStringCreateWithCString.argtypes = [
                ctypes.c_void_p,
                ctypes.c_char_p,
                ctypes.c_uint32,
            ]

            # CFURLCreateWithFileSystemPath
            self._core_foundation.CFURLCreateWithFileSystemPath.restype = (
                ctypes.c_void_p
            )
            self._core_foundation.CFURLCreateWithFileSystemPath.argtypes = [
                ctypes.c_void_p,
                ctypes.c_void_p,
                ctypes.c_int,
                ctypes.c_bool,
            ]

            # AudioServicesCreateSystemSoundID
            self._audio_toolbox.AudioServicesCreateSystemSoundID.restype = (
                ctypes.c_int32
            )
            self._audio_toolbox.AudioServicesCreateSystemSoundID.argtypes = [
                ctypes.c_void_p,
                ctypes.POINTER(ctypes.c_uint32),
            ]

            # CFRelease für Memory Management
            self._core_foundation.CFRelease.restype = None
            self._core_foundation.CFRelease.argtypes = [ctypes.c_void_p]
        except (OSError, AttributeError) as e:
            logger.debug(f"CoreAudio nicht verfügbar, nutze Fallback: {e}")
            self._use_fallback = True

    def _load_sound(self, path: str) -> int | None:
        """Lädt Sound-Datei und gibt Sound-ID zurück."""
        if self._use_fallback or self._core_foundation is None:
            return None

        cf_string = None
        cf_url = None
        try:
            # CFString aus Pfad erstellen (kCFStringEncodingUTF8 = 0x08000100)
            cf_string = self._core_foundation.CFStringCreateWithCString(
                None, path.encode(), 0x08000100
            )
            if not cf_string:
                return None

            # CFURL erstellen (kCFURLPOSIXPathStyle = 0)
            cf_url = self._core_foundation.CFURLCreateWithFileSystemPath(
                None, cf_string, 0, False
            )
            if not cf_url:
                return None

            # Sound-ID erstellen
            sound_id = self._ctypes.c_uint32(0)
            result = self._audio_toolbox.AudioServicesCreateSystemSoundID(
                cf_url, self._ctypes.byref(sound_id)
            )

            if result == 0:
                return sound_id.value
            return None
        except Exception:
            return None
        finally:
            # WICHTIG: CF-Objekte freigeben um Memory Leaks zu vermeiden
            if cf_url:
                self._core_foundation.CFRelease(cf_url)
            if cf_string:
                self._core_foundation.CFRelease(cf_string)

    def play(self, sound_name: str, sound_path: str) -> None:
        """Spielt Sound ab (lädt bei Bedarf)."""
        # Fallback auf subprocess
        if self._use_fallback:
            self._play_fallback(sound_path)
            return

        # Sound-ID aus Cache oder neu laden
        if sound_name not in self._sound_ids:
            sound_id = self._load_sound(sound_path)
            if sound_id is None:
                self._play_fallback(sound_path)
                return
            self._sound_ids[sound_name] = sound_id

        # Sound abspielen (non-blocking, ~0.2ms)
        try:
            self._audio_toolbox.AudioServicesPlaySystemSound(
                self._sound_ids[sound_name]
            )
        except Exception:
            self._play_fallback(sound_path)

    def _play_fallback(self, sound_path: str) -> None:
        """Fallback auf afplay wenn CoreAudio nicht funktioniert."""
        import subprocess

        try:
            subprocess.Popen(
                ["afplay", sound_path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError:
            pass


# Singleton-Instanz (lazy init beim ersten Aufruf)
_sound_player: _CoreAudioPlayer | None = None


def _get_sound_player() -> _CoreAudioPlayer:
    """Gibt Sound-Player Singleton zurück (lazy init)."""
    global _sound_player
    if _sound_player is None:
        _sound_player = _CoreAudioPlayer()
    return _sound_player


# Sound-Registry: Name → System-Sound-Pfad
SYSTEM_SOUNDS = {
    "ready": "/System/Library/Sounds/Tink.aiff",
    "stop": "/System/Library/Sounds/Pop.aiff",
    "error": "/System/Library/Sounds/Basso.aiff",
}


def play_sound(name: str) -> None:
    """Spielt System-Sound ab (macOS, ~0.2ms Latenz)."""
    if path := SYSTEM_SOUNDS.get(name):
        _get_sound_player().play(name, path)


# =============================================================================
# Audio-Aufnahme
# =============================================================================


def record_audio() -> Path:
    """
    Nimmt Audio vom Mikrofon auf (Enter startet, Enter stoppt).
    Gibt Pfad zur temporären WAV-Datei zurück.
    """
    import numpy as np
    import sounddevice as sd
    import soundfile as sf

    recorded_chunks: list = []

    def on_audio_chunk(indata, _frames, _time, _status):
        recorded_chunks.append(indata.copy())

    log("🎤 Drücke ENTER um die Aufnahme zu starten...")
    input()

    play_sound("ready")
    log("🔴 Aufnahme läuft... Drücke ENTER zum Beenden.")
    with sd.InputStream(
        samplerate=WHISPER_SAMPLE_RATE,
        channels=1,
        dtype="float32",
        callback=on_audio_chunk,
    ):
        input()

    log("✅ Aufnahme beendet.")
    play_sound("stop")

    if not recorded_chunks:
        raise ValueError("Keine Audiodaten aufgenommen. Bitte länger aufnehmen.")

    audio_data = np.concatenate(recorded_chunks)
    output_path = Path(tempfile.gettempdir()) / TEMP_RECORDING_FILENAME
    sf.write(output_path, audio_data, WHISPER_SAMPLE_RATE)

    return output_path


# =============================================================================
# Daemon-Hilfsfunktionen (Raycast-Integration)
# =============================================================================


def _is_whisper_go_process(pid: int) -> bool:
    """Prüft ob die PID zu einem whisper_go Prozess gehört.

    Einfache, sichere Implementierung. Die ~50ms sind akzeptabel,
    da diese Funktion nur im seltenen Edge-Case aufgerufen wird
    (Cleanup nach Crash oder bei schnellem Doppelklick).
    """
    import subprocess

    try:
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "command="],
            capture_output=True,
            text=True,
            timeout=1,
        )
        command = result.stdout.strip()
        return "transcribe.py" in command and "--record-daemon" in command
    except Exception:
        return False


def _daemonize() -> None:
    """
    Double-Fork für echte Daemon-Prozesse (verhindert Zombies).

    Wenn Raycast spawn(detached) + unref() nutzt, wird wait() nie aufgerufen.
    Der beendete Python-Prozess bleibt als Zombie. Lösung: Double-Fork.

    Nach dem Double-Fork ist launchd (PID 1) der Parent, der automatisch
    beendete Prozesse aufräumt.
    """
    # Erster Fork: Parent kann sofort exit() machen
    pid = os.fork()
    if pid > 0:
        # Parent: Warte auf Child und exit (Raycast kann jetzt wait() aufrufen)
        os.waitpid(pid, 0)
        sys.exit(0)

    # Child: Neue Session starten (löst von Terminal/Raycast)
    os.setsid()

    # Zweiter Fork: Verhindert Terminal-Übernahme
    pid = os.fork()
    if pid > 0:
        # Erstes Child: Exit sofort (wird von launchd adoptiert)
        os._exit(0)

    # Grandchild: Wir sind jetzt der echte Daemon mit launchd als Parent


def _cleanup_stale_pid_file() -> None:
    """
    Entfernt PID-File und killt alten Prozess falls nötig (Crash-Recovery).

    Sicherheit:
    - Prüft ob PID wirklich zu einem whisper_go Prozess gehört
    - Verhindert versehentliches Killen fremder Prozesse bei PID-Recycling
    """
    if not PID_FILE.exists():
        return

    try:
        old_pid = int(PID_FILE.read_text().strip())

        # Eigene PID? Dann nicht killen!
        if old_pid == os.getpid():
            return

        # Signal 0 ist ein "Ping" – prüft Existenz ohne Seiteneffekte
        os.kill(old_pid, 0)

        # SICHERHEIT: Nur killen wenn es wirklich ein whisper_go Prozess ist!
        if not _is_whisper_go_process(old_pid):
            logger.warning(
                f"[{_session_id}] PID {old_pid} ist kein whisper_go Prozess, "
                f"lösche nur PID-File (PID-Recycling?)"
            )
            PID_FILE.unlink(missing_ok=True)
            return

        # Prozess läuft noch und ist whisper_go → KILL
        logger.warning(
            f"[{_session_id}] Alter Daemon-Prozess {old_pid} läuft noch, beende ihn..."
        )

        # Erst freundlich (SIGTERM), dann hart (SIGKILL)
        try:
            os.kill(old_pid, signal.SIGTERM)
            time.sleep(0.1)
            try:
                os.kill(old_pid, 0)
                os.kill(old_pid, signal.SIGKILL)
                logger.info(
                    f"[{_session_id}] Alter Prozess {old_pid} gekillt (SIGKILL)"
                )
            except ProcessLookupError:
                logger.info(
                    f"[{_session_id}] Alter Prozess {old_pid} beendet (SIGTERM)"
                )
        except ProcessLookupError:
            pass

        PID_FILE.unlink(missing_ok=True)

    except (ValueError, ProcessLookupError):
        # PID ungültig oder Prozess existiert nicht mehr → aufräumen
        logger.info(f"[{_session_id}] Stale PID-File gelöscht: {PID_FILE}")
        PID_FILE.unlink(missing_ok=True)
    except PermissionError:
        logger.warning(f"[{_session_id}] PID-File existiert, keine Berechtigung")


def record_audio_daemon() -> Path:
    """
    Daemon-Modus: Nimmt Audio auf bis SIGUSR1 empfangen wird.
    Schreibt PID-File für externe Steuerung (Raycast).
    Kein globaler State – verwendet Closure für Signal-Flag.
    """
    import numpy as np
    import sounddevice as sd
    import soundfile as sf

    # Stale PID-File von vorherigem Crash aufräumen
    _cleanup_stale_pid_file()

    recorded_chunks: list = []
    recording_start = time.perf_counter()
    should_stop = False

    def on_audio_chunk(indata, _frames, _time_info, _status):
        """Callback: Sammelt Audio-Chunks während der Aufnahme."""
        recorded_chunks.append(indata.copy())

    def handle_stop_signal(_signum: int, _frame) -> None:
        """Signal-Handler: Setzt Stop-Flag bei SIGUSR1."""
        nonlocal should_stop
        logger.debug(f"[{_session_id}] SIGUSR1 empfangen")
        should_stop = True

    pid = os.getpid()
    logger.info(f"[{_session_id}] Daemon gestartet (PID: {pid})")

    # PID-File schreiben für Raycast
    PID_FILE.write_text(str(pid))

    # Signal-Handler registrieren
    signal.signal(signal.SIGUSR1, handle_stop_signal)

    play_sound("ready")
    log("🎤 Daemon: Aufnahme gestartet (warte auf SIGUSR1)...")
    logger.info(f"[{_session_id}] Aufnahme gestartet")

    try:
        with sd.InputStream(
            samplerate=WHISPER_SAMPLE_RATE,
            channels=1,
            dtype="float32",
            callback=on_audio_chunk,
        ):
            while not should_stop:
                sd.sleep(100)  # 100ms warten, dann Signal prüfen
    finally:
        # PID-File aufräumen
        if PID_FILE.exists():
            PID_FILE.unlink()

    recording_duration = time.perf_counter() - recording_start
    logger.info(f"[{_session_id}] Aufnahme: {recording_duration:.1f}s")
    log("✅ Daemon: Aufnahme beendet.")
    play_sound("stop")

    if not recorded_chunks:
        logger.error(f"[{_session_id}] Keine Audiodaten aufgenommen")
        raise ValueError("Keine Audiodaten aufgenommen.")

    audio_data = np.concatenate(recorded_chunks)
    output_path = Path(tempfile.gettempdir()) / TEMP_RECORDING_FILENAME
    sf.write(output_path, audio_data, WHISPER_SAMPLE_RATE)
    return output_path


# =============================================================================
# Custom Vocabulary (Fachbegriffe, Namen)
# =============================================================================


def load_vocabulary() -> dict:
    """Lädt Custom Vocabulary aus JSON-Datei (~/.whisper_go/vocabulary.json).

    Format:
        {
            "keywords": ["Anthropic", "Claude", "Kubernetes"]
        }

    Returns:
        Dict mit "keywords" (Liste). Bei Fehler leeres Dict.
    """
    if not VOCABULARY_FILE.exists():
        return {"keywords": []}
    try:
        data = json.loads(VOCABULARY_FILE.read_text())
        # Validierung: keywords muss Liste sein
        if not isinstance(data.get("keywords"), list):
            data["keywords"] = []
        return data
    except (json.JSONDecodeError, IOError) as e:
        logger.warning(f"[{_session_id}] Vocabulary-Datei fehlerhaft: {e}")
        return {"keywords": []}


# =============================================================================
# API-Client Getter (Singleton-Pattern für Performance)
# =============================================================================


def _get_openai_client():
    """Gibt OpenAI-Client Singleton zurück (Lazy Init).

    Spart ~50-100ms pro Aufruf durch Connection-Reuse.
    """
    global _openai_client
    if _openai_client is None:
        from openai import OpenAI

        _openai_client = OpenAI()
        logger.debug(f"[{_session_id}] OpenAI-Client initialisiert")
    return _openai_client


def _get_deepgram_client():
    """Gibt Deepgram-Client Singleton zurück (Lazy Init).

    Spart ~30-50ms pro Aufruf durch Connection-Reuse.
    """
    global _deepgram_client
    if _deepgram_client is None:
        from deepgram import DeepgramClient

        api_key = os.getenv("DEEPGRAM_API_KEY")
        if not api_key:
            raise ValueError("DEEPGRAM_API_KEY nicht gesetzt")
        _deepgram_client = DeepgramClient(api_key=api_key)
        logger.debug(f"[{_session_id}] Deepgram-Client initialisiert")
    return _deepgram_client


def transcribe_with_api(
    audio_path: Path,
    model: str,
    language: str | None = None,
    response_format: str = "text",
) -> str:
    """Transkribiert Audio über die OpenAI API."""
    audio_kb = audio_path.stat().st_size // 1024
    logger.info(
        f"[{_session_id}] API: {model}, {audio_kb}KB, lang={language or 'auto'}"
    )

    client = _get_openai_client()

    with timed_operation("API-Transkription"):
        with audio_path.open("rb") as audio_file:
            params = {
                "model": model,
                "file": audio_file,
                "response_format": response_format,
            }
            if language:
                params["language"] = language
            response = client.audio.transcriptions.create(**params)

    # API gibt bei format="text" String zurück, sonst Objekt
    if response_format == "text":
        result = response
    else:
        result = response.text if hasattr(response, "text") else str(response)

    logger.debug(f"[{_session_id}] Ergebnis: {_log_preview(result)}")

    return result


def transcribe_with_deepgram(
    audio_path: Path,
    model: str,
    language: str | None = None,
) -> str:
    """Transkribiert Audio über Deepgram API (smart_format aktiviert)."""
    audio_kb = audio_path.stat().st_size // 1024

    # Deepgram Limits: 100 keywords (Nova-2), 500 tokens (Nova-3 keyterm)
    MAX_DEEPGRAM_KEYWORDS = 100
    vocab = load_vocabulary()
    keywords = vocab.get("keywords", [])[:MAX_DEEPGRAM_KEYWORDS]

    logger.info(
        f"[{_session_id}] Deepgram: {model}, {audio_kb}KB, lang={language or 'auto'}, "
        f"vocab={len(keywords)}"
    )

    client = _get_deepgram_client()

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

    with timed_operation("Deepgram-Transkription"):
        response = client.listen.v1.media.transcribe_file(
            request=audio_data,
            model=model,
            language=language,
            smart_format=True,
            punctuate=True,
            **vocab_params,
        )

    result = response.results.channels[0].alternatives[0].transcript

    logger.debug(f"[{_session_id}] Ergebnis: {_log_preview(result)}")

    return result


# Konstante für Audio-Konvertierung (float32 → int16)
INT16_MAX = 32767


def _extract_transcript(result) -> str | None:
    """
    Extrahiert Transkript aus Deepgram-Response.

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
# Deepgram WebSocket Connection (ohne SDK Context Manager)
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
    """
    Deepgram WebSocket mit kontrollierbarem close_timeout.

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
    # damit Caller diese Features gezielt deaktivieren können
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


async def _deepgram_stream_core(
    model: str,
    language: str | None,
    *,
    early_buffer: list[bytes] | None = None,
    play_ready: bool = True,
    external_stop_event: threading.Event | None = None,
) -> str:
    """
    Gemeinsamer Streaming-Core für Deepgram (SDK v5.3).

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
    import asyncio

    import numpy as np
    import sounddevice as sd
    from deepgram.core.events import EventType
    from deepgram.extensions.types.sockets import ListenV1ControlMessage

    api_key = os.getenv("DEEPGRAM_API_KEY")
    if not api_key:
        raise ValueError("DEEPGRAM_API_KEY nicht gesetzt")

    stream_start = time.perf_counter()
    mode_str = "mit Buffer" if early_buffer else "Buffering"
    buffer_info = f", {len(early_buffer)} early chunks" if early_buffer else ""
    logger.info(
        f"[{_session_id}] Deepgram-Stream ({mode_str}): {model}, "
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
            logger.info(f"[{_session_id}] Final: {_log_preview(transcript)}")
        else:
            # Throttling: Max alle INTERIM_THROTTLE_MS schreiben
            now = time.perf_counter()
            if (now - last_interim_write) * 1000 >= INTERIM_THROTTLE_MS:
                try:
                    INTERIM_FILE.write_text(transcript)
                    last_interim_write = now
                    logger.debug(
                        f"[{_session_id}] Interim: {_log_preview(transcript, 30)}"
                    )
                except OSError as e:
                    # I/O-Fehler nicht den Stream abbrechen lassen
                    logger.warning(f"[{_session_id}] Interim-Write fehlgeschlagen: {e}")

    def on_error(error):
        nonlocal stream_error
        logger.error(f"[{_session_id}] Deepgram Error: {error}")
        stream_error = error if isinstance(error, Exception) else Exception(str(error))

    def on_close(_data):
        logger.debug(f"[{_session_id}] Connection closed")
        stop_event.set()

    # Stop-Mechanismus: SIGUSR1 oder externes Event
    loop = asyncio.get_running_loop()

    if external_stop_event is not None:
        # Unified-Daemon-Mode: Externes threading.Event überwachen
        def _watch_external_stop():
            external_stop_event.wait()
            loop.call_soon_threadsafe(stop_event.set)

        stop_watcher = threading.Thread(target=_watch_external_stop, daemon=True)
        stop_watcher.start()
        logger.debug(f"[{_session_id}] External stop event watcher gestartet")
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
        logger.info(f"[{_session_id}] {len(early_buffer)} early chunks in Queue")

        def audio_callback(indata, _frames, _time_info, status):
            """Sendet Audio direkt an Queue (WebSocket bereits verbunden)."""
            if status:
                logger.warning(f"[{_session_id}] Audio-Status: {status}")
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
                logger.warning(f"[{_session_id}] Audio-Status: {status}")
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
    logger.info(f"[{_session_id}] Mikrofon bereit nach {mic_init_ms:.0f}ms")

    if play_ready:
        play_sound("ready")

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
                    f"[{_session_id}] WebSocket verbunden nach {ws_time:.0f}ms, "
                    f"{buffered_count} gepufferte Chunks"
                )
            else:
                logger.info(f"[{_session_id}] WebSocket verbunden nach {ws_time:.0f}ms")

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
                    logger.error(f"[{_session_id}] Audio-Send Fehler: {e}")
                    stream_error = e
                    stop_event.set()

            async def listen_for_messages():
                """Empfängt Transkripte von Deepgram."""
                try:
                    await connection.start_listening()
                except asyncio.CancelledError:
                    pass
                except Exception as e:
                    logger.debug(f"[{_session_id}] Listener beendet: {e}")

            send_task = asyncio.create_task(send_audio())
            listen_task = asyncio.create_task(listen_for_messages())

            # --- Warten auf Stop (SIGUSR1 von Raycast oder CTRL+C) ---
            await stop_event.wait()
            logger.info(f"[{_session_id}] Stop-Signal empfangen")

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
            logger.info(f"[{_session_id}] Sende Finalize...")
            try:
                await connection.send_control(ListenV1ControlMessage(type="Finalize"))
            except Exception as e:
                logger.warning(f"[{_session_id}] Finalize fehlgeschlagen: {e}")

            # 3. Warten auf finale Transkripte (from_finalize=True Event)
            try:
                await asyncio.wait_for(finalize_done.wait(), timeout=FINALIZE_TIMEOUT)
                logger.info(f"[{_session_id}] Finalize abgeschlossen")
            except asyncio.TimeoutError:
                logger.warning(
                    f"[{_session_id}] Finalize-Timeout ({FINALIZE_TIMEOUT}s)"
                )

            # 4. CloseStream: Erzwingt sofortiges Verbindungs-Ende
            #    Ohne CloseStream wartet der async-with Exit ~10s auf Server-Close
            logger.info(f"[{_session_id}] Sende CloseStream...")
            try:
                await connection.send_control(
                    ListenV1ControlMessage(type="CloseStream")
                )
                logger.info(f"[{_session_id}] CloseStream gesendet")
            except Exception as e:
                logger.warning(f"[{_session_id}] CloseStream fehlgeschlagen: {e}")

            # 5. Listener Task beenden
            logger.info(f"[{_session_id}] Beende Listener...")
            listen_task.cancel()
            await asyncio.gather(listen_task, return_exceptions=True)
            logger.info(f"[{_session_id}] Listener beendet, verlasse async-with...")

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
    logger.info(f"[{_session_id}] Streaming abgeschlossen: {len(result)} Zeichen")
    return result


async def _transcribe_with_deepgram_stream_async(
    model: str = DEFAULT_DEEPGRAM_MODEL,
    language: str | None = None,
) -> str:
    """Async Deepgram Streaming für CLI-Nutzung (Wrapper um Core)."""
    return await _deepgram_stream_core(model, language, play_ready=True)


def _transcribe_with_deepgram_stream_with_buffer(
    model: str,
    language: str | None,
    early_buffer: list[bytes],
) -> str:
    """Streaming mit vorgepuffertem Audio (Daemon-Mode, Wrapper um Core)."""
    import asyncio

    return asyncio.run(
        _deepgram_stream_core(
            model, language, early_buffer=early_buffer, play_ready=False
        )
    )


def transcribe_with_deepgram_stream(
    model: str = DEFAULT_DEEPGRAM_MODEL,
    language: str | None = None,
) -> str:
    """
    Sync Wrapper für async Deepgram Streaming.

    Verwendet asyncio.run() um die async Implementierung auszuführen.
    Für Raycast-Integration: SIGUSR1 stoppt die Aufnahme sauber.
    """
    import asyncio

    return asyncio.run(_transcribe_with_deepgram_stream_async(model, language))


def _get_groq_client():
    """Gibt Groq-Client Singleton zurück (Lazy Init).

    Spart ~30-50ms pro Aufruf durch Connection-Reuse.
    """
    global _groq_client
    if _groq_client is None:
        from groq import Groq

        api_key = os.getenv("GROQ_API_KEY")
        if not api_key:
            raise ValueError("GROQ_API_KEY nicht gesetzt")
        _groq_client = Groq(api_key=api_key)
        logger.debug(f"[{_session_id}] Groq-Client initialisiert")
    return _groq_client


def transcribe_with_groq(
    audio_path: Path,
    model: str,
    language: str | None = None,
) -> str:
    """Transkribiert Audio über Groq API (Whisper auf LPU).

    Groq nutzt spezielle LPU-Chips für extrem schnelle Whisper-Inferenz
    (~300x Echtzeit) bei gleicher Qualität wie OpenAI.
    """
    audio_kb = audio_path.stat().st_size // 1024
    logger.info(
        f"[{_session_id}] Groq: {model}, {audio_kb}KB, lang={language or 'auto'}"
    )

    client = _get_groq_client()

    with timed_operation("Groq-Transkription"):
        with audio_path.open("rb") as audio_file:
            params = {
                # File-Handle statt .read() – spart Speicher bei großen Dateien
                "file": (audio_path.name, audio_file),
                "model": model,
                "response_format": "text",
                "temperature": 0.0,  # Konsistente Ergebnisse ohne Kreativität
            }
            if language:
                params["language"] = language
            response = client.audio.transcriptions.create(**params)

    # Groq gibt bei response_format="text" String zurück
    # Explizite Typprüfung statt hasattr für robustere Integration
    if isinstance(response, str):
        result = response
    elif hasattr(response, "text"):
        result = response.text
    else:
        raise TypeError(f"Unerwarteter Groq-Response-Typ: {type(response)}")

    logger.debug(f"[{_session_id}] Ergebnis: {_log_preview(result)}")

    return result


def transcribe_locally(
    audio_path: Path,
    model: str,
    language: str | None = None,
) -> str:
    """Transkribiert Audio lokal mit openai-whisper."""
    import whisper

    log(f"Lade Modell '{model}'...")
    whisper_model = whisper.load_model(model)

    log(f"Transkribiere {audio_path.name}...")
    options: dict = {"language": language} if language else {}

    # Custom Vocabulary als initial_prompt für bessere Erkennung
    # Limit: Whisper initial_prompt sollte nicht zu lang werden
    MAX_WHISPER_KEYWORDS = 50
    vocab = load_vocabulary()
    keywords = vocab.get("keywords", [])[:MAX_WHISPER_KEYWORDS]
    if keywords:
        options["initial_prompt"] = f"Fachbegriffe: {', '.join(keywords)}"
        logger.debug(f"[{_session_id}] Lokales Whisper mit {len(keywords)} Keywords")

    result = whisper_model.transcribe(str(audio_path), **options)

    return result["text"]


def _extract_message_content(content) -> str:
    """Extrahiert Text aus OpenAI/OpenRouter Message-Content (String, Liste oder None)."""
    if content is None:
        return ""
    if isinstance(content, list):
        # Liste von Content-Parts → Text-Parts extrahieren
        return "".join(
            part.get("text", "") if isinstance(part, dict) else str(part)
            for part in content
        ).strip()
    return content.strip()


# =============================================================================
# Kontext-Erkennung (Auto-Detection der aktiven App)
# =============================================================================


def _get_frontmost_app() -> str | None:
    """Ermittelt aktive App via NSWorkspace (macOS only).

    Warum NSWorkspace statt AppleScript? Performance: ~0.2ms vs ~207ms.
    """
    if sys.platform != "darwin":
        return None

    try:
        from AppKit import NSWorkspace

        app = NSWorkspace.sharedWorkspace().frontmostApplication()
        return app.localizedName() if app else None
    except ImportError:
        logger.debug(f"[{_session_id}] PyObjC/AppKit nicht verfügbar")
        return None
    except Exception as e:
        logger.debug(f"[{_session_id}] App-Detection fehlgeschlagen: {e}")
        return None


def _get_custom_app_contexts() -> dict:
    """Lädt und cached custom app contexts aus WHISPER_GO_APP_CONTEXTS."""
    global _custom_app_contexts_cache

    if _custom_app_contexts_cache is not None:
        return _custom_app_contexts_cache

    custom = os.getenv("WHISPER_GO_APP_CONTEXTS")
    if custom:
        try:
            _custom_app_contexts_cache = json.loads(custom)
            logger.debug(
                f"[{_session_id}] Custom app contexts geladen: "
                f"{list(_custom_app_contexts_cache.keys())}"
            )
        except json.JSONDecodeError as e:
            logger.warning(
                f"[{_session_id}] WHISPER_GO_APP_CONTEXTS ungültiges JSON: {e}"
            )
            _custom_app_contexts_cache = {}
    else:
        _custom_app_contexts_cache = {}

    return _custom_app_contexts_cache


def _app_to_context(app_name: str) -> str:
    """Mappt App-Name auf Kontext-Typ."""
    custom_map = _get_custom_app_contexts()
    if app_name in custom_map:
        return custom_map[app_name]

    return DEFAULT_APP_CONTEXTS.get(app_name, "default")


def detect_context(override: str | None = None) -> tuple[str, str | None, str]:
    """
    Ermittelt Kontext: CLI > ENV > App-Detection > default.

    Returns:
        Tuple (context, app_name, source) - source zeigt woher der Kontext kommt
    """
    # 1. CLI-Override (höchste Priorität)
    if override:
        return override, None, "CLI"

    # 2. ENV-Override
    env_context = os.getenv("WHISPER_GO_CONTEXT")
    if env_context:
        return env_context.lower(), None, "ENV"

    # 3. Auto-Detection via NSWorkspace (nur macOS)
    if sys.platform == "darwin":
        app_name = _get_frontmost_app()
        if app_name:
            return _app_to_context(app_name), app_name, "App"

    return "default", None, "Default"


# =============================================================================
# LLM-Nachbearbeitung (Refine)
# =============================================================================


def _get_refine_client(provider: str):
    """Erstellt Client für Nachbearbeitung (OpenAI, OpenRouter oder Groq)."""
    if provider == "groq":
        return _get_groq_client()

    from openai import OpenAI

    if provider == "openrouter":
        api_key = os.getenv("OPENROUTER_API_KEY")
        if not api_key:
            raise ValueError("OPENROUTER_API_KEY nicht gesetzt")
        return OpenAI(base_url=OPENROUTER_BASE_URL, api_key=api_key)

    # Default: OpenAI (nutzt OPENAI_API_KEY automatisch)
    return OpenAI()


def refine_transcript(
    transcript: str,
    model: str | None = None,
    prompt: str | None = None,
    provider: str | None = None,
    context: str | None = None,
) -> str:
    """Nachbearbeitung mit LLM (Flow-Style). Kontext-aware Prompts."""
    # Leeres Transkript → nichts zu tun
    if not transcript or not transcript.strip():
        logger.debug(f"[{_session_id}] Leeres Transkript, überspringe Nachbearbeitung")
        return transcript

    # Kontext-spezifischen Prompt wählen (falls nicht explizit übergeben)
    # Auch leere Strings werden wie None behandelt (Fallback auf Kontext-Prompt)
    if not prompt:
        effective_context, app_name, source = detect_context(context)
        prompt = get_prompt_for_context(effective_context)
        # Detailliertes Logging mit Quelle
        if app_name:
            logger.info(
                f"[{_session_id}] Kontext: {effective_context} (Quelle: {source}, App: {app_name})"
            )
        else:
            logger.info(
                f"[{_session_id}] Kontext: {effective_context} (Quelle: {source})"
            )

    # Provider und Modell zur Laufzeit bestimmen (CLI > ENV > Default)
    effective_provider = (
        provider or os.getenv("WHISPER_GO_REFINE_PROVIDER", "openai")
    ).lower()

    # Provider-spezifisches Default-Modell (Groq nutzt Llama, andere GPT-5)
    if model:
        effective_model = model
    elif os.getenv("WHISPER_GO_REFINE_MODEL"):
        effective_model = os.getenv("WHISPER_GO_REFINE_MODEL")
    elif effective_provider == "groq":
        effective_model = DEFAULT_GROQ_REFINE_MODEL
    else:
        effective_model = DEFAULT_REFINE_MODEL

    logger.info(
        f"[{_session_id}] LLM-Nachbearbeitung: provider={effective_provider}, model={effective_model}"
    )
    logger.debug(f"[{_session_id}] Input: {len(transcript)} Zeichen")

    client = _get_refine_client(effective_provider)
    full_prompt = f"{prompt}\n\nTranskript:\n{transcript}"

    with timed_operation("LLM-Nachbearbeitung"):
        if effective_provider == "groq":
            # Groq nutzt chat.completions API (wie OpenRouter)
            response = client.chat.completions.create(
                model=effective_model,
                messages=[{"role": "user", "content": full_prompt}],
            )
            result = _extract_message_content(response.choices[0].message.content)
        elif effective_provider == "openrouter":
            # OpenRouter API-Aufruf vorbereiten
            create_kwargs = {
                "model": effective_model,
                "messages": [{"role": "user", "content": full_prompt}],
            }

            # Provider-Routing konfigurieren (optional)
            provider_order = os.getenv("OPENROUTER_PROVIDER_ORDER")
            if provider_order:
                providers = [p.strip() for p in provider_order.split(",")]
                allow_fallbacks = (
                    os.getenv("OPENROUTER_ALLOW_FALLBACKS", "true").lower() == "true"
                )
                create_kwargs["extra_body"] = {
                    "provider": {
                        "order": providers,
                        "allow_fallbacks": allow_fallbacks,
                    }
                }
                logger.info(
                    f"[{_session_id}] OpenRouter Provider: {', '.join(providers)} "
                    f"(fallbacks: {allow_fallbacks})"
                )

            response = client.chat.completions.create(**create_kwargs)
            result = _extract_message_content(response.choices[0].message.content)
        else:
            # OpenAI responses API
            api_params = {"model": effective_model, "input": full_prompt}
            # GPT-5 nutzt "reasoning" API – "minimal" für schnelle Korrekturen
            # statt tiefgehender Analyse (spart Tokens und Latenz)
            if effective_model.startswith("gpt-5"):
                api_params["reasoning"] = {"effort": "minimal"}
            response = client.responses.create(**api_params)
            result = response.output_text.strip()

    logger.debug(f"[{_session_id}] Output: {_log_preview(result)}")
    return result


def maybe_refine_transcript(transcript: str, args: argparse.Namespace) -> str:
    """Wendet LLM-Nachbearbeitung an, falls aktiviert. Gibt Rohtext bei Fehler zurück."""
    from openai import APIError, APIConnectionError, RateLimitError

    if not args.refine or args.no_refine:
        return transcript

    try:
        return refine_transcript(
            transcript,
            model=args.refine_model,
            provider=args.refine_provider,
            context=getattr(args, "context", None),
        )
    except ValueError as e:
        # Fehlende API-Keys (z.B. OPENROUTER_API_KEY)
        logger.warning(f"LLM-Nachbearbeitung übersprungen: {e}")
        return transcript
    except (APIError, APIConnectionError, RateLimitError) as e:
        logger.warning(f"LLM-Nachbearbeitung fehlgeschlagen: {e}")
        return transcript


# Standard-Modelle pro Modus
DEFAULT_MODELS = {
    "openai": DEFAULT_API_MODEL,
    "deepgram": DEFAULT_DEEPGRAM_MODEL,
    "groq": DEFAULT_GROQ_MODEL,
    "local": DEFAULT_LOCAL_MODEL,
}


def transcribe(
    audio_path: Path,
    mode: str,
    model: str | None = None,
    language: str | None = None,
    response_format: str = "text",
) -> str:
    """
    Zentrale Transkriptions-Funktion – wählt API, Deepgram, Groq oder lokal.

    Dies ist der einzige Einstiegspunkt für Transkription,
    unabhängig vom gewählten Modus.
    """
    default_model = DEFAULT_MODELS.get(mode)
    if default_model is None:
        supported = ", ".join(sorted(DEFAULT_MODELS.keys()))
        raise ValueError(f"Ungültiger Modus '{mode}'. Unterstützt: {supported}")
    effective_model = model or default_model

    if mode == "openai":
        return transcribe_with_api(
            audio_path, effective_model, language, response_format
        )

    # Deepgram, Groq und lokal unterstützen kein response_format
    if response_format != "text":
        log(f"Hinweis: --format wird im {mode}-Modus ignoriert")

    if mode == "deepgram":
        return transcribe_with_deepgram(audio_path, effective_model, language)

    if mode == "groq":
        return transcribe_with_groq(audio_path, effective_model, language)

    return transcribe_locally(audio_path, effective_model, language)


def parse_args() -> argparse.Namespace:
    """Parst und validiert CLI-Argumente."""
    parser = argparse.ArgumentParser(
        description="Audio transkribieren mit Whisper, Deepgram oder Groq",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Beispiele:
  %(prog)s audio.mp3
  %(prog)s audio.mp3 --mode local --model large
  %(prog)s audio.mp3 --mode deepgram --language de
  %(prog)s audio.mp3 --mode groq --language de
  %(prog)s --record --copy --language de
        """,
    )

    parser.add_argument("audio", type=Path, nargs="?", help="Pfad zur Audiodatei")
    parser.add_argument(
        "-r", "--record", action="store_true", help="Vom Mikrofon aufnehmen"
    )
    parser.add_argument(
        "--record-daemon",
        action="store_true",
        help="Daemon-Modus: Aufnahme bis SIGUSR1 (für Raycast)",
    )
    parser.add_argument(
        "-c", "--copy", action="store_true", help="Ergebnis in Zwischenablage"
    )
    parser.add_argument(
        "--mode",
        choices=["openai", "local", "deepgram", "groq"],
        default=os.getenv("WHISPER_GO_MODE", "openai"),
        help="Transkriptions-Modus (auch via WHISPER_GO_MODE env)",
    )
    parser.add_argument(
        "--model",
        default=os.getenv("WHISPER_GO_MODEL"),
        help="Modellname (CLI > WHISPER_GO_MODEL env > Provider-Default). Defaults: API=gpt-4o-transcribe, Deepgram=nova-3, Groq=whisper-large-v3, Lokal=turbo",
    )
    parser.add_argument(
        "--language",
        default=os.getenv("WHISPER_GO_LANGUAGE"),
        help="Sprachcode z.B. 'de', 'en' (auch via WHISPER_GO_LANGUAGE env)",
    )
    parser.add_argument(
        "--format",
        dest="response_format",
        choices=["text", "json", "srt", "vtt"],
        default="text",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Debug-Logging aktivieren (auch auf stderr)",
    )
    parser.add_argument(
        "--refine",
        action="store_true",
        default=os.getenv("WHISPER_GO_REFINE", "").lower() == "true",
        help="LLM-Nachbearbeitung aktivieren (auch via WHISPER_GO_REFINE env)",
    )
    parser.add_argument(
        "--no-refine",
        action="store_true",
        help="LLM-Nachbearbeitung deaktivieren (überschreibt env)",
    )
    parser.add_argument(
        "--refine-model",
        default=None,
        help=f"Modell für LLM-Nachbearbeitung (default: {DEFAULT_REFINE_MODEL}, auch via WHISPER_GO_REFINE_MODEL env)",
    )
    parser.add_argument(
        "--refine-provider",
        choices=["openai", "openrouter", "groq"],
        default=None,
        help="LLM-Provider für Nachbearbeitung (auch via WHISPER_GO_REFINE_PROVIDER env)",
    )
    parser.add_argument(
        "--context",
        choices=["email", "chat", "code", "default"],
        default=None,
        help="Kontext für LLM-Nachbearbeitung (auto-detect wenn nicht gesetzt)",
    )
    parser.add_argument(
        "--no-streaming",
        action="store_true",
        help="WebSocket-Streaming deaktivieren (nur für deepgram, auch via WHISPER_GO_STREAMING=false)",
    )

    args = parser.parse_args()

    # Validierung: genau eine Audio-Quelle erforderlich
    has_audio_source = args.record or args.record_daemon or args.audio is not None
    if not has_audio_source:
        parser.error("Entweder Audiodatei, --record oder --record-daemon verwenden")

    # Gegenseitiger Ausschluss
    if args.audio and (args.record or args.record_daemon):
        parser.error("Audiodatei und Aufnahme-Modi schließen sich aus")
    if args.record and args.record_daemon:
        parser.error("--record und --record-daemon schließen sich aus")

    return args


def _schedule_state_cleanup(delay: float = 2.0) -> None:
    """Löscht STATE_FILE nach Verzögerung in Background-Thread.

    Warum Verzögerung? Die Menübar-App pollt alle 200ms – ohne Delay
    würde sie den "done"/"error"-Status verpassen.
    """
    import threading

    def cleanup():
        time.sleep(delay)
        STATE_FILE.unlink(missing_ok=True)

    # Daemon-Thread: Beendet sich automatisch wenn Hauptprozess endet
    thread = threading.Thread(target=cleanup, daemon=True)
    thread.start()


def _should_use_streaming(args: argparse.Namespace) -> bool:
    """Prüft ob Streaming für den aktuellen Modus aktiviert ist."""
    if args.mode != "deepgram":
        return False
    if getattr(args, "no_streaming", False):
        return False
    return os.getenv("WHISPER_GO_STREAMING", "true").lower() != "false"


def run_daemon_mode_streaming(args: argparse.Namespace) -> int:
    """
    Daemon-Modus mit Deepgram Streaming (SDK v5.3).

    Audio wird parallel zur Transkription gesendet - minimale Latenz.
    Transkription läuft während der Aufnahme, nicht erst danach.

    Optimierung: Mikrofon startet SOFORT nach numpy/sounddevice Import,
    Deepgram lädt parallel im Hintergrund.
    """
    pipeline_start = time.perf_counter()

    # WICHTIG: Alte Zombie-Prozesse killen bevor neuer Daemon startet
    _cleanup_stale_pid_file()

    # Alte IPC-Dateien aufräumen
    ERROR_FILE.unlink(missing_ok=True)
    INTERIM_FILE.unlink(missing_ok=True)

    # ═══════════════════════════════════════════════════════════════════════════
    # ULTRA-FAST STARTUP: Mikrofon SOFORT starten, Deepgram parallel laden
    # ═══════════════════════════════════════════════════════════════════════════

    # 1. Nur numpy + sounddevice laden (schnell: ~170ms)
    import numpy as np
    import sounddevice as sd

    # Zeit seit PROZESSSTART (nicht pipeline_start)
    since_process_start = (time.perf_counter() - _PROCESS_START) * 1000
    logger.info(
        f"[{_session_id}] numpy+sounddevice geladen: "
        f"{(time.perf_counter() - pipeline_start)*1000:.0f}ms "
        f"(seit Prozessstart: {since_process_start:.0f}ms)"
    )

    # Audio-Buffer für frühe Aufnahme
    early_audio_buffer: list[bytes] = []
    early_buffer_lock = threading.Lock()
    early_stop_event = threading.Event()

    def early_audio_callback(indata, _frames, _time_info, status):
        """Nimmt Audio auf während Deepgram noch lädt."""
        if status:
            logger.warning(f"[{_session_id}] Early-Audio-Status: {status}")
        if not early_stop_event.is_set():
            audio_bytes = (indata * 32767).astype(np.int16).tobytes()
            with early_buffer_lock:
                early_audio_buffer.append(audio_bytes)

    # 2. Mikrofon SOFORT starten
    logger.info(f"[{_session_id}] Starte Mikrofon (ultra-early)...")
    early_mic_stream = sd.InputStream(
        samplerate=WHISPER_SAMPLE_RATE,
        channels=WHISPER_CHANNELS,
        blocksize=WHISPER_BLOCKSIZE,
        dtype=np.float32,
        callback=early_audio_callback,
    )
    early_mic_stream.start()

    # 2b. Deepgram-Import parallel starten (~100ms) während User spricht
    deepgram_ready = threading.Event()
    deepgram_error: Exception | None = None

    def _preload_deepgram():
        """Lädt Deepgram SDK im Hintergrund."""
        nonlocal deepgram_error
        try:
            from deepgram import AsyncDeepgramClient  # noqa: F401

            logger.debug(f"[{_session_id}] Deepgram SDK vorgeladen")
        except Exception as e:
            deepgram_error = e
        finally:
            deepgram_ready.set()

    preload_thread = threading.Thread(target=_preload_deepgram, daemon=True)
    preload_thread.start()

    # Ready-Sound SOFORT - User kann sprechen!
    mic_ready_ms = (time.perf_counter() - pipeline_start) * 1000
    since_process = (time.perf_counter() - _PROCESS_START) * 1000
    logger.info(
        f"[{_session_id}] Mikrofon bereit nach {mic_ready_ms:.0f}ms "
        f"(seit Prozessstart: {since_process:.0f}ms) → READY SOUND!"
    )
    play_sound("ready")

    # State + PID für Raycast
    STATE_FILE.write_text("recording")
    PID_FILE.write_text(str(os.getpid()))
    logger.info(f"[{_session_id}] Streaming-Daemon gestartet (PID: {os.getpid()})")

    try:
        # 3. Early-Mikrofon stoppen und Buffer übergeben
        early_stop_event.set()
        early_mic_stream.stop()
        early_mic_stream.close()

        with early_buffer_lock:
            early_chunks = list(early_audio_buffer)
            early_audio_buffer.clear()

        logger.info(
            f"[{_session_id}] Early-Buffer: {len(early_chunks)} Chunks gepuffert"
        )

        # 4. Warten auf Deepgram-Preload (sollte längst fertig sein)
        deepgram_ready.wait(timeout=5.0)
        if deepgram_error:
            raise deepgram_error

        # 5. Streaming mit vorgepuffertem Audio starten
        transcript = _transcribe_with_deepgram_stream_with_buffer(
            model=args.model or DEFAULT_DEEPGRAM_MODEL,
            language=args.language,
            early_buffer=early_chunks,
        )

        play_sound("stop")
        log("✅ Streaming-Aufnahme beendet.")

        # State: Transcribing (für Refine-Phase)
        STATE_FILE.write_text("transcribing")

        # LLM-Nachbearbeitung (optional)
        transcript = maybe_refine_transcript(transcript, args)

        TRANSCRIPT_FILE.write_text(transcript)
        print(transcript)

        if args.copy:
            copy_to_clipboard(transcript)

        # State: Done
        STATE_FILE.write_text("done")

        # Pipeline-Summary
        total_ms = (time.perf_counter() - pipeline_start) * 1000
        logger.info(
            f"[{_session_id}] ✓ Streaming-Pipeline: {_format_duration(total_ms)}, "
            f"{len(transcript)} Zeichen"
        )
        return 0

    except ImportError as e:
        early_stop_event.set()
        early_mic_stream.stop()
        early_mic_stream.close()
        msg = f"Deepgram-Streaming nicht verfügbar: {e}"
        logger.error(f"[{_session_id}] {msg}")
        error(msg)
        ERROR_FILE.write_text(msg)
        STATE_FILE.write_text("error")
        play_sound("error")
        return 1
    except Exception as e:
        early_stop_event.set()
        try:
            early_mic_stream.stop()
            early_mic_stream.close()
        except Exception:
            pass
        logger.exception(f"[{_session_id}] Streaming-Fehler: {e}")
        error(str(e))
        ERROR_FILE.write_text(str(e))
        STATE_FILE.write_text("error")
        play_sound("error")
        return 1
    finally:
        PID_FILE.unlink(missing_ok=True)
        INTERIM_FILE.unlink(missing_ok=True)
        _schedule_state_cleanup()


def run_daemon_mode(args: argparse.Namespace) -> int:
    """
    Daemon-Modus für Raycast: Aufnahme → Transkription → Datei.
    Schreibt Fehler in ERROR_FILE für besseres Feedback.
    Aktualisiert STATE_FILE für Menübar-Feedback.

    Bei deepgram-Modus wird Streaming verwendet (Standard),
    außer --no-streaming oder WHISPER_GO_STREAMING=false.
    """
    # Double-Fork für echten Daemon (verhindert Zombies bei Raycast spawn+unref)
    _daemonize()

    # Streaming ist Default für Deepgram
    if _should_use_streaming(args):
        return run_daemon_mode_streaming(args)

    # Klassischer Modus: Erst aufnehmen, dann transkribieren
    temp_file: Path | None = None
    pipeline_start = time.perf_counter()

    # Alte Error-Datei aufräumen
    if ERROR_FILE.exists():
        ERROR_FILE.unlink()

    try:
        # State: Recording
        STATE_FILE.write_text("recording")

        audio_path = record_audio_daemon()
        temp_file = audio_path

        # State: Transcribing
        STATE_FILE.write_text("transcribing")

        transcript = transcribe(
            audio_path,
            mode=args.mode,
            model=args.model,
            language=args.language,
            response_format=args.response_format,
        )

        # LLM-Nachbearbeitung (optional)
        transcript = maybe_refine_transcript(transcript, args)

        TRANSCRIPT_FILE.write_text(transcript)
        print(transcript)

        if args.copy:
            copy_to_clipboard(transcript)

        # State: Done
        STATE_FILE.write_text("done")

        # Pipeline-Summary
        total_ms = (time.perf_counter() - pipeline_start) * 1000
        logger.info(
            f"[{_session_id}] ✓ Pipeline: {_format_duration(total_ms)}, "
            f"{len(transcript)} Zeichen"
        )
        return 0

    except ImportError:
        msg = "Für Aufnahme: pip install sounddevice soundfile"
        logger.error(f"[{_session_id}] {msg}")
        error(msg)
        ERROR_FILE.write_text(msg)
        STATE_FILE.write_text("error")
        return 1
    except Exception as e:
        logger.exception(f"[{_session_id}] Fehler im Daemon-Modus: {e}")
        error(str(e))
        ERROR_FILE.write_text(str(e))
        STATE_FILE.write_text("error")
        return 1
    finally:
        if temp_file and temp_file.exists():
            temp_file.unlink()
        # State-Datei nach kurzer Verzögerung aufräumen (non-blocking)
        _schedule_state_cleanup()


def main() -> int:
    """CLI-Einstiegspunkt."""
    load_environment()
    args = parse_args()
    setup_logging(debug=args.debug)

    # Startup-Timing loggen (seit Prozessstart)
    startup_ms = (time.perf_counter() - _PROCESS_START) * 1000
    logger.info(f"[{_session_id}] Startup: {_format_duration(startup_ms)}")

    logger.debug(f"[{_session_id}] Args: {args}")

    # Daemon-Modus hat eigene Logik (für Raycast)
    if args.record_daemon:
        return run_daemon_mode(args)

    # Audio-Quelle bestimmen
    temp_file: Path | None = None

    if args.record:
        try:
            audio_path = record_audio()
            temp_file = audio_path
        except ImportError:
            error("Für Aufnahme: pip install sounddevice soundfile")
            return 1
        except ValueError as e:
            error(str(e))
            return 1
    else:
        audio_path = args.audio
        if not audio_path.exists():
            error(f"Datei nicht gefunden: {audio_path}")
            return 1

    # Transkription durchführen
    try:
        transcript = transcribe(
            audio_path,
            mode=args.mode,
            model=args.model,
            language=args.language,
            response_format=args.response_format,
        )
    except ImportError as e:
        err_str = str(e).lower()
        if "openai" in err_str:
            package = "openai"
        elif "deepgram" in err_str:
            package = "deepgram-sdk"
        else:
            package = "openai-whisper"
        error(f"Modul nicht installiert: pip install {package}")
        return 1
    except Exception as e:
        error(str(e))
        return 1
    finally:
        if temp_file and temp_file.exists():
            temp_file.unlink()

    # LLM-Nachbearbeitung (optional)
    transcript = maybe_refine_transcript(transcript, args)

    # Ausgabe
    print(transcript)

    if args.copy:
        if copy_to_clipboard(transcript):
            log("📋 In Zwischenablage kopiert!")
        else:
            log("⚠️  Zwischenablage nicht verfügbar")

    # Pipeline-Summary
    total_ms = (time.perf_counter() - _PROCESS_START) * 1000
    logger.info(
        f"[{_session_id}] ✓ Pipeline: {_format_duration(total_ms)}, "
        f"{len(transcript)} Zeichen"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
