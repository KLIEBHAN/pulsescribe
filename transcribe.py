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
import logging  # noqa: E402
import os  # noqa: E402
import signal  # noqa: E402
import sys  # noqa: E402
import tempfile  # noqa: E402
import uuid  # noqa: E402
from contextlib import contextmanager  # noqa: E402
from logging.handlers import RotatingFileHandler  # noqa: E402
from pathlib import Path  # noqa: E402

# Import-Zeit messen (alle Standardlib-Imports abgeschlossen)
_IMPORTS_DONE = _time_module.perf_counter()

# Alias für Konsistenz im restlichen Code
time = _time_module

# Whisper erwartet Audio mit 16kHz – andere Sampleraten führen zu schlechteren Ergebnissen
WHISPER_SAMPLE_RATE = 16000

DEFAULT_API_MODEL = "gpt-4o-transcribe"
DEFAULT_LOCAL_MODEL = "turbo"
DEFAULT_DEEPGRAM_MODEL = "nova-3"
DEFAULT_REFINE_MODEL = "gpt-5-nano"  # Wird von WHISPER_GO_REFINE_MODEL überschrieben

# OpenRouter API (Alternative zu OpenAI für Nachbearbeitung)
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

DEFAULT_REFINE_PROMPT = """Korrigiere dieses Transkript:
- Entferne Füllwörter (ähm, also, quasi, sozusagen)
- Korrigiere Grammatik und Rechtschreibung
- Formatiere in saubere Absätze
- Behalte den originalen Inhalt und Stil bei

Gib NUR den korrigierten Text zurück, keine Erklärungen."""

TEMP_RECORDING_FILENAME = "whisper_recording.wav"

# Daemon-Modus: Dateien für IPC mit Raycast
PID_FILE = Path("/tmp/whisper_go.pid")
TRANSCRIPT_FILE = Path("/tmp/whisper_go.transcript")
ERROR_FILE = Path("/tmp/whisper_go.error")

# Log-Verzeichnis im Script-Ordner
SCRIPT_DIR = Path(__file__).resolve().parent
LOG_DIR = SCRIPT_DIR / "logs"
LOG_FILE = LOG_DIR / "whisper_go.log"

# Logger konfigurieren
logger = logging.getLogger("whisper_go")

# Session-ID für Log-Korrelation (wird pro Durchlauf gesetzt)
_session_id: str = ""


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
    logger.debug(f"[{_session_id}] {name} gestartet")
    try:
        yield
    finally:
        elapsed_ms = (time.perf_counter() - start) * 1000
        logger.info(f"[{_session_id}] {name}: {_format_duration(elapsed_ms)}")


def setup_logging(debug: bool = False) -> None:
    """Konfiguriert Logging: Datei mit Rotation + optional stderr."""
    global _session_id
    _session_id = _generate_session_id()

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
    """Status-Meldung auf stderr (hält stdout sauber für Pipes)."""
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


def play_ready_sound() -> None:
    """Spielt einen kurzen Ton ab wenn die Aufnahme bereit ist (macOS)."""
    import subprocess

    try:
        subprocess.Popen(
            ["afplay", "/System/Library/Sounds/Tink.aiff"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as e:
        # Sound ist optional – Fehler nur loggen, nicht abbrechen
        logger.debug(f"[{_session_id}] Ready-Sound fehlgeschlagen: {e}")


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

    play_ready_sound()
    log("🔴 Aufnahme läuft... Drücke ENTER zum Beenden.")
    with sd.InputStream(
        samplerate=WHISPER_SAMPLE_RATE,
        channels=1,
        dtype="float32",
        callback=on_audio_chunk,
    ):
        input()

    log("✅ Aufnahme beendet.")

    if not recorded_chunks:
        raise ValueError("Keine Audiodaten aufgenommen. Bitte länger aufnehmen.")

    audio_data = np.concatenate(recorded_chunks)
    output_path = Path(tempfile.gettempdir()) / TEMP_RECORDING_FILENAME
    sf.write(output_path, audio_data, WHISPER_SAMPLE_RATE)

    return output_path


def _cleanup_stale_pid_file() -> None:
    """Entfernt PID-File falls der Prozess nicht mehr läuft (Crash-Recovery)."""
    if not PID_FILE.exists():
        return

    try:
        old_pid = int(PID_FILE.read_text().strip())
        # Signal 0 ist ein "Ping" – prüft Existenz ohne Seiteneffekte
        os.kill(old_pid, 0)
        # Prozess läuft noch - könnte legitim sein oder Zombie
        logger.warning(f"PID-File existiert, Prozess {old_pid} läuft noch")
    except (ValueError, ProcessLookupError):
        # PID ungültig oder Prozess existiert nicht mehr → aufräumen
        logger.info(f"Stale PID-File gefunden, wird gelöscht: {PID_FILE}")
        PID_FILE.unlink()
    except PermissionError:
        # Prozess existiert, gehört aber anderem User
        logger.warning(
            f"PID-File {PID_FILE} existiert, keine Berechtigung für Prozess-Check"
        )


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
    # Dict statt bool, weil Python-Closures immutable Variablen nicht ändern können
    stop_flag = {"stop": False}
    recording_start = time.perf_counter()

    def on_audio_chunk(indata, _frames, _time_info, _status):
        recorded_chunks.append(indata.copy())

    def handle_stop_signal(_signum: int, _frame) -> None:
        logger.debug(f"[{_session_id}] SIGUSR1 empfangen")
        stop_flag["stop"] = True

    pid = os.getpid()
    logger.info(f"[{_session_id}] Daemon gestartet (PID: {pid})")

    # PID-File schreiben für Raycast
    PID_FILE.write_text(str(pid))
    logger.debug(f"[{_session_id}] PID-File geschrieben: {PID_FILE}")

    # Signal-Handler registrieren
    signal.signal(signal.SIGUSR1, handle_stop_signal)

    play_ready_sound()
    log("🎤 Daemon: Aufnahme gestartet (warte auf SIGUSR1)...")
    logger.info(f"[{_session_id}] Aufnahme gestartet")

    try:
        with sd.InputStream(
            samplerate=WHISPER_SAMPLE_RATE,
            channels=1,
            dtype="float32",
            callback=on_audio_chunk,
        ):
            while not stop_flag["stop"]:
                sd.sleep(100)  # 100ms warten, dann Signal prüfen
    finally:
        # PID-File aufräumen
        if PID_FILE.exists():
            PID_FILE.unlink()
            logger.debug(f"[{_session_id}] PID-File gelöscht")

    recording_duration = time.perf_counter() - recording_start
    logger.info(f"[{_session_id}] Aufnahme: {recording_duration:.1f}s")
    log("✅ Daemon: Aufnahme beendet.")

    if not recorded_chunks:
        logger.error(f"[{_session_id}] Keine Audiodaten aufgenommen")
        raise ValueError("Keine Audiodaten aufgenommen.")

    audio_data = np.concatenate(recorded_chunks)
    output_path = Path(tempfile.gettempdir()) / TEMP_RECORDING_FILENAME
    sf.write(output_path, audio_data, WHISPER_SAMPLE_RATE)
    logger.debug(f"[{_session_id}] Audio gespeichert: {output_path}")

    return output_path


def transcribe_with_api(
    audio_path: Path,
    model: str,
    language: str | None = None,
    response_format: str = "text",
) -> str:
    """Transkribiert Audio über die OpenAI API."""
    from openai import OpenAI

    logger.info(
        f"[{_session_id}] API-Transkription: model={model}, language={language or 'auto'}"
    )
    logger.debug(f"[{_session_id}] Audio: {audio_path.stat().st_size} bytes")

    client = OpenAI()

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
    from deepgram import DeepgramClient

    logger.info(
        f"[{_session_id}] Deepgram-Transkription: model={model}, language={language or 'auto'}"
    )
    logger.debug(f"[{_session_id}] Audio: {audio_path.stat().st_size} bytes")

    api_key = os.getenv("DEEPGRAM_API_KEY")
    if not api_key:
        raise ValueError("DEEPGRAM_API_KEY nicht gesetzt")

    client = DeepgramClient(api_key=api_key)

    with audio_path.open("rb") as f:
        audio_data = f.read()

    with timed_operation("Deepgram-Transkription"):
        response = client.listen.v1.media.transcribe_file(
            request=audio_data,
            model=model,
            language=language,
            smart_format=True,
            punctuate=True,
        )

    result = response.results.channels[0].alternatives[0].transcript

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
    options = {"language": language} if language else {}
    result = whisper_model.transcribe(str(audio_path), **options)

    return result["text"]


def _get_refine_client(provider: str):
    """Erstellt OpenAI-Client für Nachbearbeitung (OpenAI oder OpenRouter)."""
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
) -> str:
    """Nachbearbeitung mit LLM (Flow-Style). Unterstützt OpenAI und OpenRouter."""
    # Leeres Transkript → nichts zu tun
    if not transcript or not transcript.strip():
        logger.debug(f"[{_session_id}] Leeres Transkript, überspringe Nachbearbeitung")
        return transcript

    # Provider und Modell zur Laufzeit bestimmen (CLI > ENV > Default)
    effective_provider = (
        provider or os.getenv("WHISPER_GO_REFINE_PROVIDER", "openai")
    ).lower()
    effective_model = model or os.getenv(
        "WHISPER_GO_REFINE_MODEL", DEFAULT_REFINE_MODEL
    )

    logger.info(
        f"[{_session_id}] LLM-Nachbearbeitung: provider={effective_provider}, model={effective_model}"
    )
    logger.debug(f"[{_session_id}] Input: {len(transcript)} Zeichen")

    client = _get_refine_client(effective_provider)
    full_prompt = f"{prompt or DEFAULT_REFINE_PROMPT}\n\nTranskript:\n{transcript}"

    with timed_operation("LLM-Nachbearbeitung"):
        if effective_provider == "openrouter":
            # OpenRouter nutzt Chat Completions API
            response = client.chat.completions.create(
                model=effective_model,
                messages=[{"role": "user", "content": full_prompt}],
            )
            result = response.choices[0].message.content.strip()
        else:
            # OpenAI responses API
            api_params = {"model": effective_model, "input": full_prompt}
            # GPT-5 nutzt "reasoning" API mit effort-Level
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
        )
    except (APIError, APIConnectionError, RateLimitError) as e:
        logger.warning(f"LLM-Nachbearbeitung fehlgeschlagen: {e}")
        return transcript


# Standard-Modelle pro Modus – zentrale Konfiguration statt verstreuter Defaults
DEFAULT_MODELS = {
    "api": DEFAULT_API_MODEL,
    "deepgram": DEFAULT_DEEPGRAM_MODEL,
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
    Zentrale Transkriptions-Funktion – wählt API, Deepgram oder lokal.

    Dies ist der einzige Einstiegspunkt für Transkription,
    unabhängig vom gewählten Modus.
    """
    effective_model = model or DEFAULT_MODELS[mode]

    if mode == "api":
        return transcribe_with_api(
            audio_path, effective_model, language, response_format
        )

    # Deepgram und lokal unterstützen kein response_format
    if response_format != "text":
        log(f"Hinweis: --format wird im {mode}-Modus ignoriert")

    if mode == "deepgram":
        return transcribe_with_deepgram(audio_path, effective_model, language)

    return transcribe_locally(audio_path, effective_model, language)


def parse_args() -> argparse.Namespace:
    """Parst und validiert CLI-Argumente."""
    parser = argparse.ArgumentParser(
        description="Audio transkribieren mit Whisper oder Deepgram",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Beispiele:
  %(prog)s audio.mp3
  %(prog)s audio.mp3 --mode local --model large
  %(prog)s audio.mp3 --mode deepgram --language de
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
        choices=["api", "local", "deepgram"],
        default=os.getenv("WHISPER_GO_MODE", "api"),
        help="Transkriptions-Modus (auch via WHISPER_GO_MODE env)",
    )
    parser.add_argument(
        "--model",
        help="Modellname (API: gpt-4o-transcribe; Deepgram: nova-3, nova-2; Lokal: tiny, base, small, medium, large, turbo)",
    )
    parser.add_argument("--language", help="Sprachcode z.B. 'de', 'en'")
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
        choices=["openai", "openrouter"],
        default=None,
        help="LLM-Provider für Nachbearbeitung (auch via WHISPER_GO_REFINE_PROVIDER env)",
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


def run_daemon_mode(args: argparse.Namespace) -> int:
    """
    Daemon-Modus für Raycast: Aufnahme → Transkription → Datei.
    Schreibt Fehler in ERROR_FILE für besseres Feedback.
    """
    temp_file: Path | None = None
    pipeline_start = time.perf_counter()

    # Alte Error-Datei aufräumen
    if ERROR_FILE.exists():
        ERROR_FILE.unlink()

    try:
        audio_path = record_audio_daemon()
        temp_file = audio_path

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
        logger.debug(f"[{_session_id}] Transkript geschrieben: {TRANSCRIPT_FILE}")
        print(transcript)

        if args.copy:
            copy_to_clipboard(transcript)

        # Pipeline-Summary
        total_ms = (time.perf_counter() - pipeline_start) * 1000
        logger.info(
            f"[{_session_id}] ✓ Pipeline abgeschlossen: {total_ms / 1000:.2f}s, "
            f"{len(transcript)} Zeichen"
        )
        return 0

    except ImportError:
        msg = "Für Aufnahme: pip install sounddevice soundfile"
        logger.error(f"[{_session_id}] {msg}")
        error(msg)
        ERROR_FILE.write_text(msg)
        return 1
    except Exception as e:
        logger.exception(f"[{_session_id}] Fehler im Daemon-Modus: {e}")
        error(str(e))
        ERROR_FILE.write_text(str(e))
        return 1
    finally:
        if temp_file and temp_file.exists():
            temp_file.unlink()
            logger.debug(f"[{_session_id}] Temp-Datei gelöscht: {temp_file}")


def main() -> int:
    """CLI-Einstiegspunkt."""
    # Startup-Phasen messen
    t0 = time.perf_counter()
    load_environment()
    t_env = time.perf_counter()

    args = parse_args()
    t_args = time.perf_counter()

    setup_logging(debug=args.debug)
    t_logging = time.perf_counter()

    # Startup-Timing loggen
    import_ms = (_IMPORTS_DONE - _PROCESS_START) * 1000
    env_ms = (t_env - t0) * 1000
    args_ms = (t_args - t_env) * 1000
    logging_ms = (t_logging - t_args) * 1000
    total_startup_ms = (t_logging - _PROCESS_START) * 1000

    logger.info(
        f"[{_session_id}] Startup: {total_startup_ms:.0f}ms "
        f"(imports={import_ms:.0f}ms, env={env_ms:.0f}ms, "
        f"args={args_ms:.0f}ms, logging={logging_ms:.0f}ms)"
    )

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

    return 0


if __name__ == "__main__":
    sys.exit(main())
