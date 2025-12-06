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
import sys
import tempfile
from pathlib import Path

# Whisper erwartet Audio mit 16kHz – andere Sampleraten führen zu schlechteren Ergebnissen
WHISPER_SAMPLE_RATE = 16000

DEFAULT_API_MODEL = "gpt-4o-transcribe"
DEFAULT_LOCAL_MODEL = "turbo"

TEMP_RECORDING_FILENAME = "whisper_recording.wav"


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


def transcribe_with_api(
    audio_path: Path,
    model: str,
    language: str | None = None,
    response_format: str = "text",
) -> str:
    """Transkribiert Audio über die OpenAI API."""
    from openai import OpenAI

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
        return response
    return response.text if hasattr(response, "text") else str(response)


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
    Zentrale Transkriptions-Funktion – wählt API oder lokal.

    Dies ist der einzige Einstiegspunkt für Transkription,
    unabhängig vom gewählten Modus.
    """
    effective_model = model or (
        DEFAULT_API_MODEL if mode == "api" else DEFAULT_LOCAL_MODEL
    )

    if mode == "api":
        return transcribe_with_api(
            audio_path, effective_model, language, response_format
        )

    if response_format != "text":
        log("Hinweis: --format wird im lokalen Modus ignoriert")

    return transcribe_locally(audio_path, effective_model, language)


def parse_args() -> argparse.Namespace:
    """Parst und validiert CLI-Argumente."""
    parser = argparse.ArgumentParser(
        description="Audio transkribieren mit Whisper (API oder lokal)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Beispiele:
  %(prog)s audio.mp3
  %(prog)s audio.mp3 --mode local --model large
  %(prog)s --record --copy --language de
        """,
    )

    parser.add_argument("audio", type=Path, nargs="?", help="Pfad zur Audiodatei")
    parser.add_argument(
        "-r", "--record", action="store_true", help="Vom Mikrofon aufnehmen"
    )
    parser.add_argument(
        "-c", "--copy", action="store_true", help="Ergebnis in Zwischenablage"
    )
    parser.add_argument(
        "--mode", choices=["api", "local"], default="api", help="Transkriptions-Modus"
    )
    parser.add_argument(
        "--model",
        help="Modellname (API: gpt-4o-transcribe; Lokal: tiny, base, small, medium, large, turbo)",
    )
    parser.add_argument("--language", help="Sprachcode z.B. 'de', 'en'")
    parser.add_argument(
        "--format",
        dest="response_format",
        choices=["text", "json", "srt", "vtt"],
        default="text",
    )

    args = parser.parse_args()

    # Validierung: genau eine Audio-Quelle erforderlich
    if not args.record and args.audio is None:
        parser.error("Entweder Audiodatei angeben oder --record verwenden")
    if args.record and args.audio is not None:
        parser.error("--record und Audiodatei schließen sich aus")

    return args


def main() -> int:
    """CLI-Einstiegspunkt."""
    load_environment()
    args = parse_args()

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
