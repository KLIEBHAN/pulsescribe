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
from pathlib import Path

# Whisper erwartet Audio mit 16kHz – andere Sampleraten führen zu schlechteren Ergebnissen
WHISPER_SAMPLE_RATE = 16000

DEFAULT_API_MODEL = "gpt-4o-transcribe"
DEFAULT_LOCAL_MODEL = "turbo"
DEFAULT_DEEPGRAM_MODEL = "nova-3"

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


def setup_logging(debug: bool = False) -> None:
    """Konfiguriert Logging: Datei + optional stderr."""
    logger.setLevel(logging.DEBUG if debug else logging.INFO)

    # Log-Verzeichnis erstellen falls nicht vorhanden
    LOG_DIR.mkdir(exist_ok=True)

    # Datei-Handler (immer aktiv)
    file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
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


def record_audio_daemon() -> Path:
    """
    Daemon-Modus: Nimmt Audio auf bis SIGUSR1 empfangen wird.
    Schreibt PID-File für externe Steuerung (Raycast).
    Kein globaler State – verwendet Closure für Signal-Flag.
    """
    import numpy as np
    import sounddevice as sd
    import soundfile as sf

    recorded_chunks: list = []
    stop_flag = {"stop": False}  # Mutable Container statt global

    def on_audio_chunk(indata, frames, time, status):
        recorded_chunks.append(indata.copy())

    def handle_stop_signal(signum: int, frame) -> None:
        logger.debug("SIGUSR1 empfangen")
        stop_flag["stop"] = True

    pid = os.getpid()
    logger.info(f"Daemon gestartet (PID: {pid})")

    # PID-File schreiben für Raycast
    PID_FILE.write_text(str(pid))
    logger.debug(f"PID-File geschrieben: {PID_FILE}")

    # Signal-Handler registrieren
    signal.signal(signal.SIGUSR1, handle_stop_signal)

    log("🎤 Daemon: Aufnahme gestartet (warte auf SIGUSR1)...")
    logger.info("Aufnahme gestartet")

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
            logger.debug("PID-File gelöscht")

    duration = len(recorded_chunks) * 0.1  # ~100ms pro Chunk
    logger.info(f"Aufnahme beendet ({duration:.1f}s, {len(recorded_chunks)} Chunks)")
    log("✅ Daemon: Aufnahme beendet.")

    if not recorded_chunks:
        logger.error("Keine Audiodaten aufgenommen")
        raise ValueError("Keine Audiodaten aufgenommen.")

    audio_data = np.concatenate(recorded_chunks)
    output_path = Path(tempfile.gettempdir()) / TEMP_RECORDING_FILENAME
    sf.write(output_path, audio_data, WHISPER_SAMPLE_RATE)
    logger.debug(f"Audio gespeichert: {output_path}")

    return output_path


def transcribe_with_api(
    audio_path: Path,
    model: str,
    language: str | None = None,
    response_format: str = "text",
) -> str:
    """Transkribiert Audio über die OpenAI API."""
    from openai import OpenAI

    logger.info(f"API-Transkription: model={model}, language={language or 'auto'}")
    logger.debug(f"Audio-Datei: {audio_path} ({audio_path.stat().st_size} bytes)")

    client = OpenAI()

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

    logger.info(f"Transkription abgeschlossen ({len(result)} Zeichen)")
    logger.debug(f"Ergebnis: {result[:100]}{'...' if len(result) > 100 else ''}")

    return result


def transcribe_with_deepgram(
    audio_path: Path,
    model: str,
    language: str | None = None,
) -> str:
    """Transkribiert Audio über Deepgram API (smart_format aktiviert)."""
    from deepgram import DeepgramClient, PrerecordedOptions, FileSource

    logger.info(f"Deepgram-Transkription: model={model}, language={language or 'auto'}")
    logger.debug(f"Audio-Datei: {audio_path} ({audio_path.stat().st_size} bytes)")

    api_key = os.getenv("DEEPGRAM_API_KEY")
    if not api_key:
        raise ValueError("DEEPGRAM_API_KEY nicht gesetzt")

    client = DeepgramClient(api_key)

    with audio_path.open("rb") as f:
        payload: FileSource = {"buffer": f.read()}

    options = PrerecordedOptions(
        model=model,
        language=language,
        smart_format=True,
        punctuate=True,
    )

    response = client.listen.rest.v("1").transcribe_file(payload, options)
    result = response.results.channels[0].alternatives[0].transcript

    logger.info(f"Transkription abgeschlossen ({len(result)} Zeichen)")
    logger.debug(f"Ergebnis: {result[:100]}{'...' if len(result) > 100 else ''}")

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

        TRANSCRIPT_FILE.write_text(transcript)
        logger.debug(f"Transkript geschrieben: {TRANSCRIPT_FILE}")
        print(transcript)

        if args.copy:
            copy_to_clipboard(transcript)

        logger.info("Daemon erfolgreich beendet")
        return 0

    except ImportError:
        msg = "Für Aufnahme: pip install sounddevice soundfile"
        logger.error(msg)
        error(msg)
        ERROR_FILE.write_text(msg)
        return 1
    except Exception as e:
        logger.exception(f"Fehler im Daemon-Modus: {e}")
        error(str(e))
        ERROR_FILE.write_text(str(e))
        return 1
    finally:
        if temp_file and temp_file.exists():
            temp_file.unlink()
            logger.debug(f"Temp-Datei gelöscht: {temp_file}")


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
        package = "openai" if "openai" in str(e) else "openai-whisper"
        error(f"Modul nicht installiert: pip install {package}")
        return 1
    except Exception as e:
        error(str(e))
        return 1
    finally:
        if temp_file and temp_file.exists():
            temp_file.unlink()

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
