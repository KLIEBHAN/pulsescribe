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

import argparse
import logging
import os
import signal
import sys
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from logging.handlers import RotatingFileHandler
from pathlib import Path

# Whisper erwartet Audio mit 16kHz – andere Sampleraten führen zu schlechteren Ergebnissen
WHISPER_SAMPLE_RATE = 16000

DEFAULT_API_MODEL = "gpt-4o-transcribe"
DEFAULT_LOCAL_MODEL = "turbo"
DEFAULT_DEEPGRAM_MODEL = "nova-3"
DEFAULT_REFINE_MODEL = "gpt-5-nano"  # Wird von WHISPER_GO_REFINE_MODEL überschrieben

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


@contextmanager
def timed_operation(name: str):
    """Kontextmanager für Zeitmessung mit automatischem Logging."""
    start = time.perf_counter()
    logger.debug(f"[{_session_id}] {name} gestartet")
    try:
        yield
    finally:
        elapsed_ms = (time.perf_counter() - start) * 1000
        if elapsed_ms >= 1000:
            logger.info(f"[{_session_id}] {name}: {elapsed_ms / 1000:.2f}s")
        else:
            logger.info(f"[{_session_id}] {name}: {elapsed_ms:.0f}ms")


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
    """
    Lädt .env-Datei falls python-dotenv installiert ist.
    Sucht in: 1) Script-Verzeichnis (Symlinks aufgelöst), 2) Aktuelles Verzeichnis
    """
    try:
        from dotenv import load_dotenv

        # .env im Script-Verzeichnis (resolve() folgt Symlinks)
        script_dir = Path(__file__).resolve().parent
        env_file = script_dir / ".env"
        if env_file.exists():
            load_dotenv(env_file)
        else:
            # Fallback: aktuelles Verzeichnis
            load_dotenv()
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


def record_audio() -> Path:
    """
    Nimmt Audio vom Mikrofon auf (Enter startet, Enter stoppt).
    Gibt Pfad zur temporären WAV-Datei zurück.
    """
    import numpy as np
    import sounddevice as sd
    import soundfile as sf

    recorded_chunks: list = []

    def on_audio_chunk(indata, frames, time, status):
        recorded_chunks.append(indata.copy())

    log("🎤 Drücke ENTER um die Aufnahme zu starten...")
    input()

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
        # Prüfen ob Prozess noch läuft (Signal 0 = nur prüfen, nicht senden)
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


# Globaler State für SDK-Preload
_sdk_preload_thread: threading.Thread | None = None
_sdk_preload_start: float = 0


def _preload_transcription_sdk(mode: str) -> None:
    """
    Lädt Transkriptions-SDK im Hintergrund vor.
    Wird während der Aufnahme gestartet, damit das SDK bereit ist wenn die Aufnahme endet.
    """
    global _sdk_preload_start
    _sdk_preload_start = time.perf_counter()

    try:
        if mode == "deepgram":
            logger.debug(f"[{_session_id}] SDK-Preload: Deepgram wird geladen...")
            from deepgram import DeepgramClient  # noqa: F401

            elapsed = (time.perf_counter() - _sdk_preload_start) * 1000
            logger.info(
                f"[{_session_id}] SDK-Preload: Deepgram geladen ({elapsed:.0f}ms)"
            )
        elif mode == "api":
            logger.debug(f"[{_session_id}] SDK-Preload: OpenAI wird geladen...")
            from openai import OpenAI  # noqa: F401

            elapsed = (time.perf_counter() - _sdk_preload_start) * 1000
            logger.info(
                f"[{_session_id}] SDK-Preload: OpenAI geladen ({elapsed:.0f}ms)"
            )
        else:
            logger.debug(
                f"[{_session_id}] SDK-Preload: Lokaler Modus, kein Preload nötig"
            )
    except Exception as e:
        logger.warning(f"[{_session_id}] SDK-Preload fehlgeschlagen: {e}")


def start_sdk_preload(mode: str) -> None:
    """Startet SDK-Preload in Background-Thread."""
    global _sdk_preload_thread
    _sdk_preload_thread = threading.Thread(
        target=_preload_transcription_sdk,
        args=(mode,),
        daemon=True,
        name="sdk-preload",
    )
    _sdk_preload_thread.start()
    logger.debug(f"[{_session_id}] SDK-Preload gestartet für mode={mode}")


def wait_for_sdk_preload() -> None:
    """Wartet auf SDK-Preload falls noch nicht fertig."""
    global _sdk_preload_thread
    if _sdk_preload_thread and _sdk_preload_thread.is_alive():
        logger.debug(f"[{_session_id}] Warte auf SDK-Preload...")
        _sdk_preload_thread.join(timeout=5.0)
        if _sdk_preload_thread.is_alive():
            logger.warning(f"[{_session_id}] SDK-Preload Timeout nach 5s")


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
    stop_flag = {"stop": False}  # Mutable Container statt global
    recording_start = time.perf_counter()

    def on_audio_chunk(indata, frames, time_info, status):
        recorded_chunks.append(indata.copy())

    def handle_stop_signal(signum: int, frame) -> None:
        logger.debug(f"[{_session_id}] SIGUSR1 empfangen")
        stop_flag["stop"] = True

    pid = os.getpid()
    logger.info(f"[{_session_id}] Daemon gestartet (PID: {pid})")

    # PID-File schreiben für Raycast
    PID_FILE.write_text(str(pid))
    logger.debug(f"[{_session_id}] PID-File geschrieben: {PID_FILE}")

    # Signal-Handler registrieren
    signal.signal(signal.SIGUSR1, handle_stop_signal)

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

    logger.debug(
        f"[{_session_id}] Ergebnis: {result[:100]}{'...' if len(result) > 100 else ''}"
    )

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

    logger.debug(
        f"[{_session_id}] Ergebnis: {result[:100]}{'...' if len(result) > 100 else ''}"
    )

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


def refine_transcript(
    transcript: str,
    model: str | None = None,
    prompt: str | None = None,
) -> str:
    """Nachbearbeitung mit LLM (Flow-Style)."""
    from openai import OpenAI

    # Leeres Transkript → nichts zu tun
    if not transcript or not transcript.strip():
        logger.debug(f"[{_session_id}] Leeres Transkript, überspringe Nachbearbeitung")
        return transcript

    # ENV zur Laufzeit lesen (nach load_environment)
    effective_model = model or os.getenv(
        "WHISPER_GO_REFINE_MODEL", DEFAULT_REFINE_MODEL
    )
    logger.info(f"[{_session_id}] LLM-Nachbearbeitung: model={effective_model}")
    logger.debug(f"[{_session_id}] Input: {len(transcript)} Zeichen")

    client = OpenAI()

    # API-Parameter je nach Modell anpassen
    api_params = {
        "model": effective_model,
        "input": f"{prompt or DEFAULT_REFINE_PROMPT}\n\nTranskript:\n{transcript}",
    }

    # Reasoning nur für GPT-5 Modelle (minimal), andere nutzen low/medium/high
    if effective_model.startswith("gpt-5"):
        api_params["reasoning"] = {"effort": "minimal"}

    with timed_operation("LLM-Nachbearbeitung"):
        response = client.responses.create(**api_params)

    result = response.output_text.strip()
    logger.debug(
        f"[{_session_id}] Output: {result[:100]}{'...' if len(result) > 100 else ''}"
    )

    return result


def maybe_refine_transcript(transcript: str, args: argparse.Namespace) -> str:
    """Wendet LLM-Nachbearbeitung an, falls aktiviert. Gibt Rohtext bei Fehler zurück."""
    from openai import APIError, APIConnectionError, RateLimitError

    if not args.refine or args.no_refine:
        return transcript

    try:
        return refine_transcript(transcript, model=args.refine_model)
    except (APIError, APIConnectionError, RateLimitError) as e:
        logger.warning(f"LLM-Nachbearbeitung fehlgeschlagen: {e}")
        return transcript


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
    if model:
        effective_model = model
    elif mode == "api":
        effective_model = DEFAULT_API_MODEL
    elif mode == "deepgram":
        effective_model = DEFAULT_DEEPGRAM_MODEL
    else:
        effective_model = DEFAULT_LOCAL_MODEL

    if mode == "api":
        return transcribe_with_api(
            audio_path, effective_model, language, response_format
        )

    if mode == "deepgram":
        if response_format != "text":
            log("Hinweis: --format wird im Deepgram-Modus ignoriert")
        return transcribe_with_deepgram(audio_path, effective_model, language)

    if response_format != "text":
        log("Hinweis: --format wird im lokalen Modus ignoriert")

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
        # SDK im Hintergrund vorladen während Aufnahme läuft
        start_sdk_preload(args.mode)

        audio_path = record_audio_daemon()
        temp_file = audio_path

        # Sicherstellen dass SDK geladen ist bevor Transkription startet
        wait_for_sdk_preload()

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
    load_environment()
    args = parse_args()
    setup_logging(debug=args.debug)

    logger.debug(f"Args: {args}")

    # Daemon-Modus hat eigene Logik (für Raycast)
    if args.record_daemon:
        return run_daemon_mode(args)

    # Audio-Quelle bestimmen
    temp_file: Path | None = None

    if args.record:
        try:
            # SDK im Hintergrund vorladen während Aufnahme läuft
            start_sdk_preload(args.mode)
            audio_path = record_audio()
            temp_file = audio_path
            wait_for_sdk_preload()
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
