"""Zentrale Konfiguration für whisper_go.

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

INTERIM_THROTTLE_MS = 150    # Max. Update-Rate für Interim-File (Menübar pollt 200ms)
FINALIZE_TIMEOUT = 2.0       # Warten auf finale Transkripte nach Deepgram-Finalize
DEEPGRAM_WS_URL = "wss://api.deepgram.com/v1/listen"
DEEPGRAM_CLOSE_TIMEOUT = 0.5 # Schneller WebSocket-Shutdown (SDK Default: 10s)

# =============================================================================
# Default-Modelle
# =============================================================================

DEFAULT_API_MODEL = "gpt-4o-transcribe"
DEFAULT_LOCAL_MODEL = "turbo"
DEFAULT_DEEPGRAM_MODEL = "nova-3"
DEFAULT_GROQ_MODEL = "whisper-large-v3"
DEFAULT_REFINE_MODEL = "gpt-5-nano"
DEFAULT_GROQ_REFINE_MODEL = "llama-3.3-70b-versatile"

# =============================================================================
# Audio-Analyse
# =============================================================================

VAD_THRESHOLD = 0.015      # Trigger recording (RMS)
VISUAL_NOISE_GATE = 0.01   # UI silence floor (RMS)
VISUAL_GAIN = 5.0          # Sqrt scaling factor

# =============================================================================
# IPC-Dateipfade
# =============================================================================

# IPC-Dateien für Kommunikation zwischen Prozessen (Raycast, Hotkey-Daemon, Menübar)
# Alle Dateien liegen in /tmp für schnellen Zugriff und automatische Bereinigung
TEMP_RECORDING_FILENAME = "whisper_recording.wav"
PID_FILE = Path("/tmp/whisper_go.pid")           # Aktive Aufnahme-PID → für SIGUSR1 Stop
TRANSCRIPT_FILE = Path("/tmp/whisper_go.transcript")  # Fertiges Transkript
ERROR_FILE = Path("/tmp/whisper_go.error")       # Fehlermeldungen für UI-Feedback
STATE_FILE = Path("/tmp/whisper_go.state")       # Aktueller Status (recording/transcribing/done/error)
INTERIM_FILE = Path("/tmp/whisper_go.interim")   # Live-Transkript während Aufnahme

# =============================================================================
# API-Endpunkte
# =============================================================================

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# =============================================================================
# Lokale Pfade
# =============================================================================

SCRIPT_DIR = Path(__file__).resolve().parent
LOG_DIR = SCRIPT_DIR / "logs"
LOG_FILE = LOG_DIR / "whisper_go.log"
VOCABULARY_FILE = Path.home() / ".whisper_go" / "vocabulary.json"


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
    # Models
    "DEFAULT_API_MODEL",
    "DEFAULT_LOCAL_MODEL",
    "DEFAULT_DEEPGRAM_MODEL",
    "DEFAULT_GROQ_MODEL",
    "DEFAULT_REFINE_MODEL",
    "DEFAULT_GROQ_REFINE_MODEL",
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
]
