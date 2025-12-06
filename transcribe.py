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

# ─────────────────────────────────────────────────────────────────────────────
# Konstanten
# ─────────────────────────────────────────────────────────────────────────────

# Whisper erwartet Audio mit 16kHz – andere Sampleraten führen zu schlechteren Ergebnissen
WHISPER_SAMPLE_RATE = 16000

# Default-Modelle für die jeweiligen Modi
DEFAULT_API_MODEL = "gpt-4o-transcribe"
DEFAULT_LOCAL_MODEL = "turbo"

TEMP_RECORDING_FILENAME = "whisper_recording.wav"

# ─────────────────────────────────────────────────────────────────────────────
# Hilfsfunktionen
# ─────────────────────────────────────────────────────────────────────────────


def log(message: str) -> None:
    """Gibt Status-Meldungen auf stderr aus (hält stdout sauber für Pipes)."""
    print(message, file=sys.stderr)


def error(message: str) -> None:
    """Gibt Fehlermeldungen auf stderr aus."""
    print(f"Fehler: {message}", file=sys.stderr)


def copy_to_clipboard(text: str) -> bool:
    """Kopiert Text in die Zwischenablage. Gibt True bei Erfolg zurück."""
    try:
        import pyperclip

        pyperclip.copy(text)
        return True
    except Exception:
        return False


def load_environment() -> None:
    """Lädt .env-Datei falls python-dotenv installiert ist."""
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass  # Optionale Dependency – kein Fehler


# ─────────────────────────────────────────────────────────────────────────────
# Aufnahme
# ─────────────────────────────────────────────────────────────────────────────


def record_audio() -> Path:
    """
    Nimmt Audio vom Mikrofon auf.

    Der User startet und stoppt die Aufnahme jeweils mit Enter.
    Die Aufnahme wird als WAV in einer temporären Datei gespeichert.
    """
    import numpy as np
    import sounddevice as sd
    import soundfile as sf

    recorded_chunks: list = []

    def on_audio_chunk(indata, frames, time, status):
        """Callback: Speichert jeden Audio-Chunk während der Aufnahme."""
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

    # Leere Aufnahme abfangen – passiert wenn User sofort Enter drückt
    if not recorded_chunks:
        raise ValueError("Keine Audiodaten aufgenommen. Bitte länger aufnehmen.")

    # Chunks zusammenfügen und als WAV speichern
    audio_data = np.concatenate(recorded_chunks)
    output_path = Path(tempfile.gettempdir()) / TEMP_RECORDING_FILENAME
    sf.write(output_path, audio_data, WHISPER_SAMPLE_RATE)

    return output_path


# ─────────────────────────────────────────────────────────────────────────────
# Transkription
# ─────────────────────────────────────────────────────────────────────────────


def transcribe_with_api(
    audio_path: Path,
    model: str = DEFAULT_API_MODEL,
    language: str | None = None,
    response_format: str = "text",
) -> str:
    """
    Transkribiert Audio über die OpenAI API.

    Benötigt OPENAI_API_KEY als Umgebungsvariable.
    """
    from openai import OpenAI

    client = OpenAI()

    with audio_path.open("rb") as audio_file:
        request_params = {
            "model": model,
            "file": audio_file,
            "response_format": response_format,
        }
        if language:
            request_params["language"] = language

        response = client.audio.transcriptions.create(**request_params)

    # API gibt bei format="text" einen String zurück, sonst ein Objekt
    if response_format == "text":
        return response
    return response.text if hasattr(response, "text") else str(response)


def transcribe_locally(
    audio_path: Path,
    model: str = DEFAULT_LOCAL_MODEL,
    language: str | None = None,
) -> str:
    """
    Transkribiert Audio lokal mit openai-whisper.

    Beim ersten Aufruf wird das Modell heruntergeladen (~1-3 GB je nach Größe).
    """
    import whisper

    log(f"Lade Modell '{model}'...")
    whisper_model = whisper.load_model(model)

    log(f"Transkribiere {audio_path.name}...")
    transcribe_options = {"language": language} if language else {}
    result = whisper_model.transcribe(str(audio_path), **transcribe_options)

    return result["text"]


# ─────────────────────────────────────────────────────────────────────────────
# Audio-Quelle
# ─────────────────────────────────────────────────────────────────────────────


def get_audio_source(args: argparse.Namespace) -> tuple[Path, bool]:
    """
    Bestimmt die Audio-Quelle basierend auf den CLI-Argumenten.

    Returns:
        (audio_path, is_temporary): Pfad zur Audiodatei und ob sie temporär ist

    Raises:
        SystemExit: Bei fehlender Datei oder fehlenden Dependencies
    """
    if args.record:
        try:
            return record_audio(), True
        except ImportError:
            error("Für Aufnahme: pip install sounddevice soundfile")
            sys.exit(1)

    audio_path = args.audio
    if not audio_path.exists():
        error(f"Datei nicht gefunden: {audio_path}")
        sys.exit(1)

    return audio_path, False


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────


def create_argument_parser() -> argparse.ArgumentParser:
    """Erstellt und konfiguriert den Argument-Parser."""
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

    # Positionales Argument
    parser.add_argument(
        "audio",
        type=Path,
        nargs="?",
        default=None,
        help="Pfad zur Audiodatei",
    )

    # Aufnahme & Ausgabe
    parser.add_argument(
        "--record",
        "-r",
        action="store_true",
        help="Vom Mikrofon aufnehmen (Enter startet/stoppt)",
    )
    parser.add_argument(
        "--copy",
        "-c",
        action="store_true",
        help="Ergebnis in Zwischenablage kopieren",
    )

    # Transkriptions-Optionen
    parser.add_argument(
        "--mode",
        choices=["api", "local"],
        default="api",
        help="'api' für OpenAI API (default), 'local' für lokales Whisper",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Modellname (API: gpt-4o-transcribe; Lokal: tiny, base, small, medium, large, turbo)",
    )
    parser.add_argument(
        "--language",
        default=None,
        help="Sprachcode z.B. 'de', 'en' (optional, auto-detect wenn leer)",
    )
    parser.add_argument(
        "--format",
        dest="response_format",
        choices=["text", "json", "srt", "vtt"],
        default="text",
        help="Output-Format (nur API-Modus, default: text)",
    )

    return parser


def validate_arguments(
    parser: argparse.ArgumentParser, args: argparse.Namespace
) -> None:
    """Validiert die CLI-Argumente auf logische Konsistenz."""
    if not args.record and args.audio is None:
        parser.error("Entweder eine Audiodatei angeben oder --record verwenden")

    if args.record and args.audio is not None:
        parser.error("--record und Audiodatei können nicht kombiniert werden")


def run_transcription(args: argparse.Namespace, audio_path: Path) -> str:
    """Führt die Transkription im gewählten Modus durch."""
    model = args.model or (
        DEFAULT_API_MODEL if args.mode == "api" else DEFAULT_LOCAL_MODEL
    )

    if args.mode == "api":
        return transcribe_with_api(
            audio_path,
            model=model,
            language=args.language,
            response_format=args.response_format,
        )

    # Lokaler Modus
    if args.response_format != "text":
        log("Hinweis: --format wird im lokalen Modus ignoriert")

    return transcribe_locally(
        audio_path,
        model=model,
        language=args.language,
    )


def main() -> int:
    """Haupteinstiegspunkt der CLI."""
    load_environment()

    parser = create_argument_parser()
    args = parser.parse_args()
    validate_arguments(parser, args)

    audio_path, is_temporary = get_audio_source(args)

    try:
        transcript = run_transcription(args, audio_path)
        print(transcript)

        if args.copy:
            if copy_to_clipboard(transcript):
                log("📋 In Zwischenablage kopiert!")
            else:
                log("⚠️  Zwischenablage nicht verfügbar")

        return 0

    except ImportError as e:
        # Hilfreiche Fehlermeldung für fehlende Dependencies
        missing_package = "openai" if "openai" in str(e) else "openai-whisper"
        error(f"Modul nicht installiert. Bitte: pip install {missing_package}")
        return 1

    except Exception as e:
        error(str(e))
        return 1

    finally:
        # Temporäre Aufnahmen aufräumen
        if is_temporary and audio_path.exists():
            audio_path.unlink()


if __name__ == "__main__":
    sys.exit(main())
