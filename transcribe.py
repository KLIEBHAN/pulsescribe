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
import uuid  # noqa: E402
from contextlib import contextmanager  # noqa: E402
from logging.handlers import RotatingFileHandler  # noqa: E402
from pathlib import Path  # noqa: E402

# Import-Zeit messen (alle Standardlib-Imports abgeschlossen)
_IMPORTS_DONE = _time_module.perf_counter()
time = _time_module  # Alias für restlichen Code

# =============================================================================
# Audio-Konfiguration
# =============================================================================

# Whisper erwartet Audio mit 16kHz – andere Sampleraten führen zu schlechteren Ergebnissen
WHISPER_SAMPLE_RATE = 16000

# =============================================================================
# Standard-Modelle pro Provider
# =============================================================================

DEFAULT_API_MODEL = "gpt-4o-transcribe"
DEFAULT_LOCAL_MODEL = "turbo"
DEFAULT_DEEPGRAM_MODEL = "nova-3"
DEFAULT_REFINE_MODEL = "gpt-5-nano"

# =============================================================================
# LLM-Nachbearbeitung (Refine)
# =============================================================================

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

DEFAULT_REFINE_PROMPT = """Korrigiere dieses Transkript:
- Entferne Füllwörter (ähm, also, quasi, sozusagen)
- Korrigiere Grammatik und Rechtschreibung
- Formatiere in saubere Absätze
- Behalte den originalen Inhalt und Stil bei

Gib NUR den korrigierten Text zurück, keine Erklärungen."""

# Kontext-spezifische Prompts für LLM-Nachbearbeitung
CONTEXT_PROMPTS = {
    "email": """Korrigiere dieses Transkript für eine E-Mail:
- Formeller, professioneller Ton
- Vollständige, grammatikalisch korrekte Sätze
- Grußformeln und Anrede beibehalten
- Klar strukturierte Absätze

Gib NUR den korrigierten Text zurück.""",
    "chat": """Korrigiere dieses Transkript für eine Chat-Nachricht:
- Lockerer, natürlicher Ton
- Kurz und prägnant
- Emojis können beibehalten werden
- Keine übermäßige Formalisierung

Gib NUR den korrigierten Text zurück.""",
    "code": """Korrigiere dieses Transkript für technischen Kontext:
- Technische Fachbegriffe exakt beibehalten
- Code-Snippets, Variablennamen und Befehle nicht ändern
- Camel/Snake-Case erkennen und beibehalten
- Englische Begriffe nicht eindeutschen

Gib NUR den korrigierten Text zurück.""",
    "default": DEFAULT_REFINE_PROMPT,
}

# App-zu-Kontext Mapping für automatische Erkennung
DEFAULT_APP_CONTEXTS = {
    # Email-Clients
    "Mail": "email",
    "Outlook": "email",
    "Spark": "email",
    "Thunderbird": "email",
    # Chat/Messenger
    "Slack": "chat",
    "Discord": "chat",
    "Telegram": "chat",
    "WhatsApp": "chat",
    "Messages": "chat",
    "Signal": "chat",
    # Code-Editoren
    "Code": "code",
    "VS Code": "code",
    "Visual Studio Code": "code",
    "Cursor": "code",
    "Zed": "code",
    "PyCharm": "code",
    "IntelliJ IDEA": "code",
    "Xcode": "code",
    "Terminal": "code",
    "iTerm2": "code",
    "Ghostty": "code",
}

# =============================================================================
# Dateipfade für IPC und Konfiguration
# =============================================================================

# Temporäre Dateien für Raycast-Integration
TEMP_RECORDING_FILENAME = "whisper_recording.wav"
PID_FILE = Path("/tmp/whisper_go.pid")
TRANSCRIPT_FILE = Path("/tmp/whisper_go.transcript")
ERROR_FILE = Path("/tmp/whisper_go.error")
STATE_FILE = Path("/tmp/whisper_go.state")

# Konfiguration und Logs
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
        logger.warning(
            f"[{_session_id}] PID-File existiert, Prozess {old_pid} läuft noch"
        )
    except (ValueError, ProcessLookupError):
        # PID ungültig oder Prozess existiert nicht mehr → aufräumen
        logger.info(f"[{_session_id}] Stale PID-File gelöscht: {PID_FILE}")
        PID_FILE.unlink()
    except PermissionError:
        # Prozess existiert, gehört aber anderem User
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
            while not should_stop:
                sd.sleep(100)  # 100ms warten, dann Signal prüfen
    finally:
        # PID-File aufräumen
        if PID_FILE.exists():
            PID_FILE.unlink()

    recording_duration = time.perf_counter() - recording_start
    logger.info(f"[{_session_id}] Aufnahme: {recording_duration:.1f}s")
    log("✅ Daemon: Aufnahme beendet.")

    if not recorded_chunks:
        logger.error(f"[{_session_id}] Keine Audiodaten aufgenommen")
        raise ValueError("Keine Audiodaten aufgenommen.")

    audio_data = np.concatenate(recorded_chunks)
    output_path = Path(tempfile.gettempdir()) / TEMP_RECORDING_FILENAME
    sf.write(output_path, audio_data, WHISPER_SAMPLE_RATE)
    return output_path


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


def transcribe_with_api(
    audio_path: Path,
    model: str,
    language: str | None = None,
    response_format: str = "text",
) -> str:
    """Transkribiert Audio über die OpenAI API."""
    from openai import OpenAI

    audio_kb = audio_path.stat().st_size // 1024
    logger.info(
        f"[{_session_id}] API: {model}, {audio_kb}KB, lang={language or 'auto'}"
    )

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

    audio_kb = audio_path.stat().st_size // 1024

    # Deepgram Limits: 100 keywords (Nova-2), 500 tokens (Nova-3 keyterm)
    MAX_DEEPGRAM_KEYWORDS = 100
    vocab = load_vocabulary()
    keywords = vocab.get("keywords", [])[:MAX_DEEPGRAM_KEYWORDS]

    logger.info(
        f"[{_session_id}] Deepgram: {model}, {audio_kb}KB, lang={language or 'auto'}, "
        f"vocab={len(keywords)}"
    )

    api_key = os.getenv("DEEPGRAM_API_KEY")
    if not api_key:
        raise ValueError("DEEPGRAM_API_KEY nicht gesetzt")

    client = DeepgramClient(api_key=api_key)

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
        # Validierung: Ungültiger Kontext → Warnung und Fallback
        if effective_context not in CONTEXT_PROMPTS:
            logger.warning(
                f"[{_session_id}] Ungültiger Kontext '{effective_context}', verwende 'default'"
            )
            effective_context = "default"
            source = "Fallback"
        prompt = CONTEXT_PROMPTS[effective_context]
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
    effective_model = model or os.getenv(
        "WHISPER_GO_REFINE_MODEL", DEFAULT_REFINE_MODEL
    )

    logger.info(
        f"[{_session_id}] LLM-Nachbearbeitung: provider={effective_provider}, model={effective_model}"
    )
    logger.debug(f"[{_session_id}] Input: {len(transcript)} Zeichen")

    client = _get_refine_client(effective_provider)
    full_prompt = f"{prompt}\n\nTranskript:\n{transcript}"

    with timed_operation("LLM-Nachbearbeitung"):
        if effective_provider == "openrouter":
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
    default_model = DEFAULT_MODELS.get(mode)
    if default_model is None:
        supported = ", ".join(sorted(DEFAULT_MODELS.keys()))
        raise ValueError(f"Ungültiger Modus '{mode}'. Unterstützt: {supported}")
    effective_model = model or default_model

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
    parser.add_argument(
        "--context",
        choices=["email", "chat", "code", "default"],
        default=None,
        help="Kontext für LLM-Nachbearbeitung (auto-detect wenn nicht gesetzt)",
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


def run_daemon_mode(args: argparse.Namespace) -> int:
    """
    Daemon-Modus für Raycast: Aufnahme → Transkription → Datei.
    Schreibt Fehler in ERROR_FILE für besseres Feedback.
    Aktualisiert STATE_FILE für Menübar-Feedback.
    """
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
