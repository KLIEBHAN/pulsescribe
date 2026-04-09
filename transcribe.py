#!/usr/bin/env python3
"""
Hauptmodul und CLI-Einstiegspunkt für PulseScribe.

Dieses Modul fungiert als zentraler Orchestrator, der die spezialisierten
Sub-Module koordiniert:
- audio/: Audio-Aufnahme und -Verarbeitung
- providers/: Transkriptions-Dienste (Deepgram, OpenAI, etc.)
- refine/: LLM-Nachbearbeitung und Kontext-Erkennung
- utils/: Logging, Timing und Hilfsfunktionen

Es stellt die `main()` Routine bereit und verwaltet die CLI-Argumente.

Transkripte werden auf stdout ausgegeben, Status auf stderr.

Usage:
    python transcribe.py audio.mp3
    python transcribe.py audio.mp3 --mode local
    python transcribe.py --record --copy
"""

# Startup-Timing: Zeit erfassen BEVOR andere Imports laden
import time as _time_module  # noqa: E402 - muss vor anderen Imports sein

_PROCESS_START = _time_module.perf_counter()

import typer  # noqa: E402
from typing import Annotated, TYPE_CHECKING  # noqa: E402
import logging  # noqa: E402
import os  # noqa: E402
from enum import Enum  # noqa: E402

from pathlib import Path  # noqa: E402

if TYPE_CHECKING:
    pass

# Import-Zeit messen (alle Standardlib-Imports abgeschlossen)
_IMPORTS_DONE = _time_module.perf_counter()
time = _time_module  # Alias für restlichen Code

# =============================================================================
# Zentrale Konfiguration importieren
# =============================================================================

from config import (  # noqa: E402
    # Models
    DEFAULT_API_MODEL,
    DEFAULT_LOCAL_MODEL,
    DEFAULT_DEEPGRAM_MODEL,
    DEFAULT_GROQ_MODEL,
    DEFAULT_REFINE_MODEL,
    # Paths
    VOCABULARY_FILE,
)

from cli.types import (  # noqa: E402
    TranscriptionMode,
    Context,
    RefineProvider,
    ResponseFormat,
)

# Typer-App
app = typer.Typer(
    help="Audio transkribieren mit Whisper, Deepgram oder Groq",
    add_completion=False,
)

# =============================================================================
# Laufzeit-State (modulglobal)
# =============================================================================

logger = logging.getLogger("pulsescribe")

from utils.logging import (  # noqa: E402
    setup_logging,
    log,
    error,
    get_session_id as _get_session_id,
)
from utils.env import load_environment, parse_bool  # noqa: E402
from utils.timing import (  # noqa: E402
    format_duration as _format_duration,
    log_preview as _shared_log_preview,
)
from utils.vocabulary import load_vocabulary as _load_vocabulary_shared  # noqa: E402


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

from whisper_platform import get_sound_player  # noqa: E402


def play_sound(name: str) -> None:
    """Delegiert an whisper_platform."""
    try:
        get_sound_player().play(name)
    except Exception:
        pass


from audio.recording import record_audio  # noqa: E402

# =============================================================================
# Logging-Helfer
# =============================================================================


def _log_preview(text: str, max_length: int = 100) -> str:
    """Kürzt Logtexte, um Logfiles schlank zu halten.

    Wrapper um utils.timing.log_preview für vereinheitlichte Log-Formatierung.
    """
    return _shared_log_preview(text, max_length)


# =============================================================================
# Custom Vocabulary (Fachbegriffe, Namen)
# =============================================================================


def load_vocabulary() -> dict:
    """Lädt Custom Vocabulary aus JSON-Datei.

    Wrapper für utils.vocabulary.load_vocabulary(), damit die öffentliche
    API von transcribe.py stabil bleibt und Tests weiter greifen.
    """
    return _load_vocabulary_shared(VOCABULARY_FILE)


def _resolve_env_string(value: str | None, env_name: str) -> str | None:
    """Resolve CLI string options after `.env` loading."""
    if value is not None:
        return value

    env_value = os.getenv(env_name)
    if env_value is None:
        return None

    stripped = env_value.strip()
    return stripped or None


def _resolve_env_enum(
    value,
    *,
    env_name: str,
    enum_type: type[Enum],
    default=None,
):
    """Resolve CLI enum options after `.env` loading."""
    if value is not None:
        return value

    env_value = _resolve_env_string(None, env_name)
    if env_value is None:
        return default

    try:
        return enum_type(env_value.lower())
    except ValueError as exc:
        supported = ", ".join(member.value for member in enum_type)
        raise typer.BadParameter(
            f"Ungültiger Wert in {env_name}: {env_value!r}. Unterstützt: {supported}"
        ) from exc


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

from refine.llm import maybe_refine_transcript  # noqa: E402


# Standard-Modelle pro Modus
DEFAULT_MODELS = {
    "openai": DEFAULT_API_MODEL,
    "deepgram": DEFAULT_DEEPGRAM_MODEL,
    "groq": DEFAULT_GROQ_MODEL,
    "local": DEFAULT_LOCAL_MODEL,
}


def _validate_transcription_mode(mode: str) -> None:
    """Raise a stable error for unsupported transcription modes."""
    if mode in DEFAULT_MODELS:
        return
    supported = ", ".join(sorted(DEFAULT_MODELS.keys()))
    raise ValueError(f"Ungültiger Modus '{mode}'. Unterstützt: {supported}")


def _build_provider_transcribe_kwargs(
    mode: str,
    provider,
    *,
    model: str | None,
    language: str | None,
    response_format: str,
) -> dict[str, str | None]:
    """Build provider kwargs while keeping OpenAI's response-format special case."""
    kwargs: dict[str, str | None] = {
        "model": model,
        "language": language,
    }
    if mode != "openai":
        if response_format != "text":
            log(f"Hinweis: --format wird im {mode}-Modus ignoriert")
        return kwargs

    from providers.openai import OpenAIProvider

    if not isinstance(provider, OpenAIProvider):
        raise TypeError(
            f"Expected OpenAIProvider for mode='openai', got {type(provider).__name__}"
        )

    return {
        **kwargs,
        "response_format": response_format,
    }


def _resolve_audio_source(
    audio: Path | None,
    *,
    record: bool,
) -> tuple[Path, Path | None]:
    """Resolve the requested audio input and track temporary recordings."""
    if record:
        try:
            audio_path = record_audio()
            return audio_path, audio_path
        except ImportError:
            error("Für Aufnahme: pip install sounddevice soundfile")
            raise typer.Exit(1)
        except ValueError as e:
            error(str(e))
            raise typer.Exit(1)

    assert audio is not None  # Validated by CLI input checks
    if not audio.exists():
        error(f"Datei nicht gefunden: {audio}")
        raise typer.Exit(1)
    return audio, None


def _cleanup_temp_audio_file(temp_file: Path | None) -> None:
    """Best-effort cleanup for temporary recording files."""
    if temp_file is None or not temp_file.exists():
        return
    try:
        temp_file.unlink()
    except OSError as cleanup_error:
        logger.warning(
            "Temporäre Aufnahme konnte nicht gelöscht werden: %s",
            cleanup_error,
        )


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

    _validate_transcription_mode(mode)

    # Provider holen und transkribieren
    provider = get_provider(mode)
    kwargs = _build_provider_transcribe_kwargs(
        mode,
        provider,
        model=model,
        language=language,
        response_format=response_format,
    )
    return provider.transcribe(audio_path, **kwargs)


@app.command()
def main(
    audio: Annotated[
        Path | None,
        typer.Argument(help="Pfad zur Audiodatei"),
    ] = None,
    record: Annotated[
        bool,
        typer.Option("-r", "--record", help="Vom Mikrofon aufnehmen"),
    ] = False,
    copy: Annotated[
        bool,
        typer.Option("-c", "--copy", help="Ergebnis in Zwischenablage"),
    ] = False,
    mode: Annotated[
        TranscriptionMode | None,
        typer.Option(
            help="Transkriptions-Modus",
            envvar="PULSESCRIBE_MODE",
        ),
    ] = None,
    model: Annotated[
        str | None,
        typer.Option(
            help="Modellname (CLI > ENV > Provider-Default)",
            envvar="PULSESCRIBE_MODEL",
        ),
    ] = None,
    language: Annotated[
        str | None,
        typer.Option(
            help="Sprachcode z.B. 'de', 'en'",
            envvar="PULSESCRIBE_LANGUAGE",
        ),
    ] = None,
    response_format: Annotated[
        ResponseFormat,
        typer.Option("--format", help="Ausgabeformat (nur OpenAI)"),
    ] = ResponseFormat.text,
    debug: Annotated[
        bool,
        typer.Option(help="Debug-Logging aktivieren"),
    ] = False,
    refine: Annotated[
        bool,
        typer.Option(
            help="LLM-Nachbearbeitung aktivieren",
            envvar="PULSESCRIBE_REFINE",
        ),
    ] = False,
    no_refine: Annotated[
        bool,
        typer.Option(help="LLM-Nachbearbeitung deaktivieren"),
    ] = False,
    refine_model: Annotated[
        str | None,
        typer.Option(
            help=f"Modell fuer LLM-Nachbearbeitung (default: {DEFAULT_REFINE_MODEL})",
            envvar="PULSESCRIBE_REFINE_MODEL",
        ),
    ] = None,
    refine_provider: Annotated[
        RefineProvider | None,
        typer.Option(
            help="LLM-Provider fuer Nachbearbeitung",
            envvar="PULSESCRIBE_REFINE_PROVIDER",
        ),
    ] = None,
    context: Annotated[
        Context | None,
        typer.Option(help="Kontext fuer LLM-Nachbearbeitung"),
    ] = None,
) -> None:
    """Audio transkribieren mit Whisper, Deepgram oder Groq.

    Beispiele:
        transcribe.py audio.mp3
        transcribe.py audio.mp3 --mode local --model large
        transcribe.py --record --copy --language de
    """
    load_environment()
    setup_logging(debug=debug)
    mode = _resolve_env_enum(
        mode,
        env_name="PULSESCRIBE_MODE",
        enum_type=TranscriptionMode,
        default=TranscriptionMode.deepgram,
    )
    model = _resolve_env_string(model, "PULSESCRIBE_MODEL")
    language = _resolve_env_string(language, "PULSESCRIBE_LANGUAGE")
    refine_model = _resolve_env_string(refine_model, "PULSESCRIBE_REFINE_MODEL")
    refine_provider = _resolve_env_enum(
        refine_provider,
        env_name="PULSESCRIBE_REFINE_PROVIDER",
        enum_type=RefineProvider,
    )
    if not refine and not no_refine:
        refine = bool(parse_bool(os.getenv("PULSESCRIBE_REFINE")))

    # Validierung: genau eine Audio-Quelle erforderlich
    if not record and audio is None:
        raise typer.BadParameter("Entweder Audiodatei oder --record verwenden")
    if record and audio is not None:
        raise typer.BadParameter("Audiodatei und --record schliessen sich aus")

    # Startup-Timing loggen (seit Prozessstart)
    startup_ms = (time.perf_counter() - _PROCESS_START) * 1000
    logger.info(f"[{_get_session_id()}] Startup: {_format_duration(startup_ms)}")

    logger.debug(
        f"[{_get_session_id()}] Args: mode={mode.value}, model={model}, "
        f"record={record}, refine={refine}"
    )

    # Audio-Quelle bestimmen
    audio_path, temp_file = _resolve_audio_source(audio, record=record)

    # Transkription durchführen
    try:
        transcript = transcribe(
            audio_path,
            mode=mode.value,
            model=model,
            language=language,
            response_format=response_format.value,
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
        raise typer.Exit(1)
    except Exception as e:
        error(str(e))
        raise typer.Exit(1)
    finally:
        _cleanup_temp_audio_file(temp_file)

    # LLM-Nachbearbeitung (optional)
    if response_format == ResponseFormat.text:
        transcript = maybe_refine_transcript(
            transcript,
            refine=refine,
            no_refine=no_refine,
            refine_model=refine_model,
            refine_provider=refine_provider.value if refine_provider else None,
            context=context.value if context else None,
        )
    elif refine and not no_refine:
        log(
            f"Hinweis: LLM-Nachbearbeitung wird für --format {response_format.value} übersprungen"
        )

    # Ausgabe
    print(transcript)

    if copy:
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


if __name__ == "__main__":
    app()
