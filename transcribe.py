#!/usr/bin/env python3
"""
Whisper Transcription CLI - API und lokaler Modus.

Usage:
    python transcribe.py audio.mp3
    python transcribe.py audio.mp3 --mode local
    python transcribe.py --record --copy
"""

import argparse
import sys
import tempfile
from pathlib import Path

# .env laden (falls vorhanden)
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass  # python-dotenv nicht installiert, kein Problem


def record_audio() -> Path:
    """Nimmt Audio vom Mikrofon auf. Enter startet, Enter stoppt."""
    import sounddevice as sd
    import soundfile as sf

    sample_rate = 16000  # Whisper erwartet 16kHz
    recording = []

    def callback(indata, frames, time, status):
        recording.append(indata.copy())

    print("🎤 Drücke ENTER um die Aufnahme zu starten...", file=sys.stderr)
    input()

    print("🔴 Aufnahme läuft... Drücke ENTER zum Beenden.", file=sys.stderr)
    with sd.InputStream(
        samplerate=sample_rate,
        channels=1,
        dtype="float32",
        callback=callback,
    ):
        input()

    print("✅ Aufnahme beendet.", file=sys.stderr)

    # In temporäre Datei speichern
    import numpy as np

    audio_data = np.concatenate(recording)
    temp_path = Path(tempfile.gettempdir()) / "whisper_recording.wav"
    sf.write(temp_path, audio_data, sample_rate)

    return temp_path


def transcribe_api(
    audio_path: Path,
    model: str = "gpt-4o-transcribe",
    language: str | None = None,
    response_format: str = "text",
) -> str:
    """Transkribiert via OpenAI API."""
    from openai import OpenAI

    client = OpenAI()

    with audio_path.open("rb") as audio_file:
        kwargs = {
            "model": model,
            "file": audio_file,
            "response_format": response_format,
        }
        if language:
            kwargs["language"] = language

        transcript = client.audio.transcriptions.create(**kwargs)

    # Bei text-Format ist transcript ein String, sonst ein Objekt
    if response_format == "text":
        return transcript
    return transcript.text if hasattr(transcript, "text") else str(transcript)


def transcribe_local(
    audio_path: Path,
    model: str = "turbo",
    language: str | None = None,
) -> str:
    """Transkribiert lokal mit openai-whisper."""
    import whisper

    print(f"Lade Modell '{model}'...", file=sys.stderr)
    whisper_model = whisper.load_model(model)

    print(f"Transkribiere {audio_path.name}...", file=sys.stderr)
    kwargs = {"language": language} if language else {}
    result = whisper_model.transcribe(str(audio_path), **kwargs)

    return result["text"]


def main() -> int:
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

    parser.add_argument(
        "audio", type=Path, nargs="?", default=None, help="Pfad zur Audiodatei"
    )
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
    parser.add_argument(
        "--mode",
        choices=["api", "local"],
        default="api",
        help="'api' für OpenAI API (default), 'local' für lokales Whisper",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Modellname (API: gpt-4o-transcribe, whisper-1; Lokal: tiny, base, small, medium, large, turbo)",
    )
    parser.add_argument(
        "--language",
        default=None,
        help="Sprachcode z.B. 'de', 'en' (optional)",
    )
    parser.add_argument(
        "--format",
        dest="response_format",
        choices=["text", "json", "srt", "vtt"],
        default="text",
        help="Output-Format (nur API-Modus, default: text)",
    )

    args = parser.parse_args()

    # Validierung: entweder audio oder --record
    if not args.record and args.audio is None:
        parser.error("Entweder eine Audiodatei angeben oder --record verwenden")
    if args.record and args.audio is not None:
        parser.error("--record und Audiodatei können nicht kombiniert werden")

    # Audio-Quelle bestimmen
    temp_recording = None
    if args.record:
        try:
            audio_path = record_audio()
            temp_recording = audio_path  # Merken für Cleanup
        except ImportError:
            print(
                "Fehler: Für Aufnahme: pip install sounddevice soundfile",
                file=sys.stderr,
            )
            return 1
    else:
        audio_path = args.audio
        if not audio_path.exists():
            print(f"Fehler: Datei nicht gefunden: {audio_path}", file=sys.stderr)
            return 1

    # Default-Modelle setzen
    if args.model is None:
        args.model = "gpt-4o-transcribe" if args.mode == "api" else "turbo"

    try:
        if args.mode == "api":
            result = transcribe_api(
                audio_path,
                model=args.model,
                language=args.language,
                response_format=args.response_format,
            )
        else:
            if args.response_format != "text":
                print(
                    "Hinweis: --format wird im lokalen Modus ignoriert",
                    file=sys.stderr,
                )
            result = transcribe_local(
                audio_path,
                model=args.model,
                language=args.language,
            )

        print(result)

        # Bei --copy: in Zwischenablage kopieren
        if args.copy:
            try:
                import pyperclip

                pyperclip.copy(result)
                print("📋 In Zwischenablage kopiert!", file=sys.stderr)
            except Exception:
                print("⚠️  Zwischenablage nicht verfügbar", file=sys.stderr)

        return 0

    except ImportError as e:
        missing = "openai" if "openai" in str(e) else "openai-whisper"
        print(
            f"Fehler: Modul nicht installiert. Bitte: pip install {missing}",
            file=sys.stderr,
        )
        return 1
    except Exception as e:
        print(f"Fehler: {e}", file=sys.stderr)
        return 1
    finally:
        # Temp-Datei aufräumen
        if temp_recording and temp_recording.exists():
            temp_recording.unlink()


if __name__ == "__main__":
    sys.exit(main())
