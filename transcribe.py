#!/usr/bin/env python3
"""
Hauptmodul und CLI-Einstiegspunkt für whisper_go.

Dieses Modul fungiert als zentraler Orchestrator, der die spezialisierten
Sub-Module koordiniert:
- audio/: Audio-Aufnahme und -Verarbeitung
- providers/: Transkriptions-Dienste (Deepgram, OpenAI, etc.)
- refine/: LLM-Nachbearbeitung und Kontext-Erkennung
- utils/: Logging, Timing und Hilfsfunktionen

Es stellt die `main()` Routine bereit und verwaltet den Daemon-Modus
sowie die CLI-Argumente.

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
import sys  # noqa: E402
import threading  # noqa: E402
import asyncio  # noqa: E402
from typing import TYPE_CHECKING  # noqa: E402
from providers.deepgram_stream import deepgram_stream_core  # noqa: E402
from pathlib import Path  # noqa: E402

if TYPE_CHECKING:
    pass

# Import-Zeit messen (alle Standardlib-Imports abgeschlossen)
_IMPORTS_DONE = _time_module.perf_counter()
time = _time_module  # Alias für restlichen Code

# =============================================================================
# Zentrale Konfiguration importieren
# =============================================================================

from config import (
    # Audio
    WHISPER_SAMPLE_RATE,
    WHISPER_CHANNELS,
    WHISPER_BLOCKSIZE,
    # Models
    DEFAULT_API_MODEL,
    DEFAULT_LOCAL_MODEL,
    DEFAULT_DEEPGRAM_MODEL,
    DEFAULT_GROQ_MODEL,
    DEFAULT_REFINE_MODEL,
    # IPC
    PID_FILE,
    TRANSCRIPT_FILE,
    ERROR_FILE,
    STATE_FILE,
    INTERIM_FILE,
    # Paths
    SCRIPT_DIR,
    VOCABULARY_FILE,
)

# =============================================================================
# Laufzeit-State (modulglobal)
# =============================================================================

logger = logging.getLogger("whisper_go")
_custom_app_contexts_cache: dict | None = None  # Cache für WHISPER_GO_APP_CONTEXTS

# API-Client Singletons (Lazy Init) – für LLM-Refine
# Transkriptions-Clients sind jetzt in providers/
_groq_client = None


from utils.logging import setup_logging, log, error, get_session_id as _get_session_id
from utils.timing import format_duration as _format_duration


def load_environment() -> None:
    """Lädt .env-Datei falls python-dotenv installiert ist."""
    try:
        from dotenv import load_dotenv

        env_file = SCRIPT_DIR / ".env"
        load_dotenv(env_file if env_file.exists() else None)
    except ImportError:
        pass


def copy_to_clipboard(text: str) -> bool:
    """Kopiert Text in die Zwischenablage. Gibt True bei Erfolg zurück.

    Delegiert an whisper_platform.clipboard für plattformspezifische Implementierung.
    Deprecated: Nutze stattdessen whisper_platform.get_clipboard().copy()
    """
    try:
        from whisper_platform import get_clipboard
        return get_clipboard().copy(text)
    except Exception:
        # Fallback auf pyperclip für Rückwärtskompatibilität
        try:
            import pyperclip
            pyperclip.copy(text)
            return True
        except Exception:
            return False

# =============================================================================
# Sound-Playback & Audio-Aufnahme
# =============================================================================

from whisper_platform import get_sound_player

def play_sound(name: str) -> None:
    """Delegiert an whisper_platform."""
    try:
        get_sound_player().play(name)
    except Exception:
        pass

from audio.recording import record_audio, record_audio_daemon
# =============================================================================
# Daemon-Hilfsfunktionen (Raycast-Integration)
# =============================================================================


from utils import daemonize as _daemonize, cleanup_stale_pid_file as _cleanup_stale_pid_file


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
        logger.warning(f"[{_get_session_id()}] Vocabulary-Datei fehlerhaft: {e}")
        return {"keywords": []}


# =============================================================================
# Transkription (delegiert an providers/)
# =============================================================================
# Die Transkriptions-Logik wurde in providers/ ausgelagert:
#   - providers/openai.py → OpenAI Whisper API
#   - providers/deepgram.py → Deepgram Nova-3
#   - providers/groq.py → Groq Whisper (LPU)
#   - providers/local.py → Lokales Whisper
#
# Siehe transcribe() Funktion für den zentralen Einstiegspunkt.
# =============================================================================








# =============================================================================
# Kontext-Erkennung (delegiert an refine.context)
# =============================================================================



# =============================================================================
# LLM-Nachbearbeitung (delegiert an refine.llm)
# =============================================================================

from refine.llm import maybe_refine_transcript


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

    Nutzt providers.get_provider() für die eigentliche Transkription.
    """
    from providers import get_provider

    # Provider validieren
    if mode not in DEFAULT_MODELS:
        supported = ", ".join(sorted(DEFAULT_MODELS.keys()))
        raise ValueError(f"Ungültiger Modus '{mode}'. Unterstützt: {supported}")

    # Provider holen und transkribieren
    provider = get_provider(mode)

    # Deepgram, Groq und lokal unterstützen kein response_format
    if response_format != "text" and mode != "openai":
        log(f"Hinweis: --format wird im {mode}-Modus ignoriert")

    # OpenAI unterstützt response_format
    if mode == "openai":
        return provider.transcribe(
            audio_path,
            model=model,
            language=language,
            response_format=response_format,
        )

    # Andere Provider
    return provider.transcribe(
        audio_path,
        model=model,
        language=language,
    )


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
        f"[{_get_session_id()}] numpy+sounddevice geladen: "
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
            logger.warning(f"[{_get_session_id()}] Early-Audio-Status: {status}")
        if not early_stop_event.is_set():
            audio_bytes = (indata * 32767).astype(np.int16).tobytes()
            with early_buffer_lock:
                early_audio_buffer.append(audio_bytes)

    # 2. Mikrofon SOFORT starten
    logger.info(f"[{_get_session_id()}] Starte Mikrofon (ultra-early)...")
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

            logger.debug(f"[{_get_session_id()}] Deepgram SDK vorgeladen")
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
        f"[{_get_session_id()}] Mikrofon bereit nach {mic_ready_ms:.0f}ms "
        f"(seit Prozessstart: {since_process:.0f}ms) → READY SOUND!"
    )
    play_sound("ready")

    # State + PID für Raycast
    STATE_FILE.write_text("recording")
    PID_FILE.write_text(str(os.getpid()))
    logger.info(f"[{_get_session_id()}] Streaming-Daemon gestartet (PID: {os.getpid()})")

    try:
        # 3. Early-Mikrofon stoppen und Buffer übergeben
        early_stop_event.set()
        early_mic_stream.stop()
        early_mic_stream.close()

        with early_buffer_lock:
            early_chunks = list(early_audio_buffer)
            early_audio_buffer.clear()

        logger.info(
            f"[{_get_session_id()}] Early-Buffer: {len(early_chunks)} Chunks gepuffert"
        )

        # 4. Warten auf Deepgram-Preload (sollte längst fertig sein)
        deepgram_ready.wait(timeout=5.0)
        if deepgram_error:
            raise deepgram_error

        # 5. Streaming mit vorgepuffertem Audio starten
        transcript = asyncio.run(
            deepgram_stream_core(
                model=args.model or DEFAULT_DEEPGRAM_MODEL,
                language=args.language,
                early_buffer=early_chunks,
            )
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
            f"[{_get_session_id()}] ✓ Streaming-Pipeline: {_format_duration(total_ms)}, "
            f"{len(transcript)} Zeichen"
        )
        return 0

    except ImportError as e:
        early_stop_event.set()
        early_mic_stream.stop()
        early_mic_stream.close()
        msg = f"Deepgram-Streaming nicht verfügbar: {e}"
        logger.error(f"[{_get_session_id()}] {msg}")
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
        logger.exception(f"[{_get_session_id()}] Streaming-Fehler: {e}")
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
    # Vor Daemon-Start aufräumen
    _cleanup_stale_pid_file()

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
            f"[{_get_session_id()}] ✓ Pipeline: {_format_duration(total_ms)}, "
            f"{len(transcript)} Zeichen"
        )
        return 0

    except ImportError:
        msg = "Für Aufnahme: pip install sounddevice soundfile"
        logger.error(f"[{_get_session_id()}] {msg}")
        error(msg)
        ERROR_FILE.write_text(msg)
        STATE_FILE.write_text("error")
        return 1
    except Exception as e:
        logger.exception(f"[{_get_session_id()}] Fehler im Daemon-Modus: {e}")
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
    logger.info(f"[{_get_session_id()}] Startup: {_format_duration(startup_ms)}")

    logger.debug(f"[{_get_session_id()}] Args: {args}")

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
        f"[{_get_session_id()}] ✓ Pipeline: {_format_duration(total_ms)}, "
        f"{len(transcript)} Zeichen"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
