"""Zentrale Konfiguration für PulseScribe.

Gemeinsame Konstanten für Audio, Streaming und IPC.
Vermeidet Duplikation zwischen Modulen.
"""

from pathlib import Path

# =============================================================================
# Audio-Konfiguration
# =============================================================================

# Whisper erwartet Audio mit 16kHz – andere Sampleraten führen zu schlechteren Ergebnissen
WHISPER_SAMPLE_RATE = 16000
WHISPER_CHANNELS = 1
WHISPER_BLOCKSIZE = 1024

# Konstante für Audio-Konvertierung (float32 → int16)
INT16_MAX = 32767

# =============================================================================
# Streaming-Konfiguration
# =============================================================================

INTERIM_THROTTLE_MS = 150  # Max. Update-Rate für Interim-File (Menübar pollt 200ms)
FINALIZE_TIMEOUT = (
    1.0  # Warten auf finale Transkripte (Deepgram antwortet meist in 300-500ms)
)
DEEPGRAM_WS_URL = "wss://api.deepgram.com/v1/listen"
DEEPGRAM_CLOSE_TIMEOUT = 0.5  # Schneller WebSocket-Shutdown (SDK Default: 10s)

# Watchdog: Automatisches Timeout wenn TRANSCRIBING zu lange dauert
# Verhindert "hängendes Overlay" bei Worker-Problemen (z.B. WebSocket-Hänger)
TRANSCRIBING_TIMEOUT = 45.0  # Sekunden (Deepgram + Refine sollten < 30s dauern)

# LLM-Refine Timeout: Maximale Wartezeit für API-Calls
# Verhindert "hängende" Requests bei Netzwerkproblemen
LLM_REFINE_TIMEOUT = 30.0  # Sekunden (typische Refine-Calls: 2-5s)

# =============================================================================
# Default-Modelle
# =============================================================================

DEFAULT_API_MODEL = "gpt-4o-transcribe"
DEFAULT_LOCAL_MODEL = "turbo"
DEFAULT_DEEPGRAM_MODEL = "nova-3"
DEFAULT_GROQ_MODEL = "whisper-large-v3"
DEFAULT_REFINE_MODEL = "openai/gpt-oss-120b"

# =============================================================================
# Audio-Analyse
# =============================================================================

VAD_THRESHOLD = 0.015  # Trigger recording (RMS)
# Visualisierung ist etwas empfindlicher als VAD, damit auch leise Sprache sichtbar ist.
VISUAL_NOISE_GATE = 0.002  # UI silence floor (RMS)
VISUAL_GAIN = 2.0  # Visual scaling factor (post-AGC, boosts quiet speech)

# =============================================================================
# IPC-Dateipfade
# =============================================================================

# IPC-Dateien für Kommunikation zwischen Prozessen (Raycast, Hotkey-Daemon, Menübar)
# Alle Dateien liegen in /tmp für schnellen Zugriff und automatische Bereinigung
TEMP_RECORDING_FILENAME = "pulsescribe_recording.wav"
PID_FILE = Path("/tmp/pulsescribe.pid")  # Aktive Aufnahme-PID → für SIGUSR1 Stop
TRANSCRIPT_FILE = Path("/tmp/pulsescribe.transcript")  # Fertiges Transkript
ERROR_FILE = Path("/tmp/pulsescribe.error")  # Fehlermeldungen für UI-Feedback
STATE_FILE = Path(
    "/tmp/pulsescribe.state"
)  # Aktueller Status (recording/transcribing/done/error)
INTERIM_FILE = Path("/tmp/pulsescribe.interim")  # Live-Transkript während Aufnahme

# =============================================================================
# API-Endpunkte
# =============================================================================

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# =============================================================================
# Lokale Pfade
# =============================================================================

# User-Verzeichnis für Konfiguration und Logs
USER_CONFIG_DIR = Path.home() / ".pulsescribe"
USER_CONFIG_DIR.mkdir(parents=True, exist_ok=True)

# Logs im User-Verzeichnis speichern
LOG_DIR = USER_CONFIG_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "pulsescribe.log"

VOCABULARY_FILE = USER_CONFIG_DIR / "vocabulary.json"
PROMPTS_FILE = USER_CONFIG_DIR / "prompts.toml"

# Resource path helper import must happen after core constants to avoid circular imports
# (utils imports config for IPC paths and config dir).
from utils.paths import get_resource_path  # noqa: E402

# Basis-Verzeichnis für Ressourcen (Code, Assets)
SCRIPT_DIR = Path(get_resource_path("."))


__all__ = [
    # Audio
    "WHISPER_SAMPLE_RATE",
    "WHISPER_CHANNELS",
    "WHISPER_BLOCKSIZE",
    "INT16_MAX",
    # Audio Analysis
    "VAD_THRESHOLD",
    "VISUAL_NOISE_GATE",
    "VISUAL_GAIN",
    # Streaming
    "INTERIM_THROTTLE_MS",
    "FINALIZE_TIMEOUT",
    "DEEPGRAM_WS_URL",
    "DEEPGRAM_CLOSE_TIMEOUT",
    "TRANSCRIBING_TIMEOUT",
    "LLM_REFINE_TIMEOUT",
    # Models
    "DEFAULT_API_MODEL",
    "DEFAULT_LOCAL_MODEL",
    "DEFAULT_DEEPGRAM_MODEL",
    "DEFAULT_GROQ_MODEL",
    "DEFAULT_REFINE_MODEL",
    # IPC
    "TEMP_RECORDING_FILENAME",
    "PID_FILE",
    "TRANSCRIPT_FILE",
    "ERROR_FILE",
    "STATE_FILE",
    "INTERIM_FILE",
    # API
    "OPENROUTER_BASE_URL",
    # Paths
    "SCRIPT_DIR",
    "LOG_DIR",
    "LOG_FILE",
    "VOCABULARY_FILE",
    "PROMPTS_FILE",
]
