"""Zentrale Konfiguration für PulseScribe.

Gemeinsame Konstanten für Audio, Streaming und IPC.
Vermeidet Duplikation zwischen Modulen.
"""

import logging
import os
import tempfile
from pathlib import Path
from typing import Any, cast

logger = logging.getLogger("pulsescribe")


def _preload_env_for_import_time_config() -> None:
    """Load `.env` values before import-time constants are evaluated.

    Several config values are derived from environment variables during module import.
    Entry points like `transcribe.py` and `pulsescribe_daemon.py` import `config`
    before calling `load_environment()`, so we preload here with the same precedence:
    process env > user `.env` > project `.env`.
    """
    from utils.env import collect_env_values, _remember_loaded_env_values

    user_config_dir = Path.home() / ".pulsescribe"
    preloaded: dict[str, str] = {}
    for key, value in collect_env_values(user_config_dir=user_config_dir).items():
        if key in os.environ:
            continue
        os.environ[key] = value
        preloaded[key] = value

    _remember_loaded_env_values(preloaded)


_preload_env_for_import_time_config()

# =============================================================================
# Audio-Konfiguration
# =============================================================================

# Whisper erwartet Audio mit 16kHz – andere Sampleraten führen zu schlechteren Ergebnissen
WHISPER_SAMPLE_RATE = 16000
WHISPER_CHANNELS = 1
WHISPER_BLOCKSIZE = 1024

# Warmup-Dauer für lokale Modelle (MLX/Lightning Metal-Compilation, Whisper/Faster GPU-Init)
PRELOAD_WARMUP_DURATION = 0.5

# Keep-Alive Warmup ist kürzer als initiales Preload (nur Shader warm halten)
KEEPALIVE_WARMUP_DURATION = 0.1

# Konstante für Audio-Konvertierung (float32 → int16)
INT16_MAX = 32767


# Cache für erkanntes Audio-Gerät (vermeidet wiederholte Tests)
_cached_input_device: tuple[int | None, int] | None = None



def _return_input_device_result(
    result: tuple[int | None, int], *, cache: bool = True
) -> tuple[int | None, int]:
    """Gibt Ergebnis zurück und cached nur verlässliche Erkennungen."""
    global _cached_input_device
    if cache:
        _cached_input_device = result
    return result



def _get_default_input_index(sd: Any) -> int:
    """Liest den konfigurierten sounddevice-Default-Input robust aus."""
    default_devices = cast(tuple[object, object], sd.default.device)
    default_input = default_devices[0]
    return default_input if isinstance(default_input, int) else -1



def _build_input_device_info(index: int, raw_device: object) -> dict[str, Any]:
    """Normalisiert ein sounddevice-Gerät auf die Felder, die wir wirklich nutzen."""
    device = cast(dict[str, Any], raw_device)
    return {
        "idx": index,
        "name": str(device.get("name", "")),
        "samplerate": int(device.get("default_samplerate", WHISPER_SAMPLE_RATE)),
    }



def _list_input_devices(sd: Any) -> list[dict[str, Any]]:
    """Sammelt alle verfügbaren Input-Geräte mit einheitlicher Struktur."""
    input_devices: list[dict[str, Any]] = []
    for index, raw_device in enumerate(sd.query_devices()):
        device = cast(dict[str, Any], raw_device)
        if int(device.get("max_input_channels", 0) or 0) <= 0:
            continue
        input_devices.append(_build_input_device_info(index, raw_device))
    return input_devices



def _device_name_matches(name: str, keywords: tuple[str, ...]) -> bool:
    """Prüft Keywords case-insensitiv gegen einen Gerätenamen."""
    lower_name = name.lower()
    return any(keyword in lower_name for keyword in keywords)



def _should_skip_windows_device(name: str) -> bool:
    """Filtert bekannte Output-/Monitor-Geräte aus der Windows-Autodetektion."""
    return _device_name_matches(name, ("lautsprecher", "speaker", "output", "monitor"))



def _log_selected_input_device(device: dict[str, Any]) -> None:
    """Protokolliert ein ausgewähltes Eingabegerät konsistent."""
    logger.info("Verwende: %s (%sHz)", device["name"], device["samplerate"])



def _probe_windows_input_device(sd: Any, device: dict[str, Any]) -> bool:
    """Testet, ob ein Windows-Input-Device geöffnet wird und Audio liefert."""
    import time
    import numpy as np

    received = [False]

    def callback(indata, frames, time_info, status):
        received[0] = True

    try:
        with sd.InputStream(
            device=device["idx"],
            samplerate=device["samplerate"],
            channels=1,
            blocksize=1024,
            dtype=np.int16,
            callback=callback,
        ) as stream:
            stream.start()
            time.sleep(0.05)  # 50ms reicht um Audio-Callback zu testen
        return received[0]
    except Exception:
        return False



def _select_windows_input_device(
    sd: Any,
    input_devices: list[dict[str, Any]],
) -> tuple[int | None, int]:
    """Wählt unter Windows das bestgeeignete funktionierende Input-Device."""
    mic_array_keywords = ("mikrofonarray", "mic array")
    mic_keywords = ("mikrofon", "mic", "microphone")

    # Priorität 1: Mikrofonarray-Geräte (funktionieren meist gut auf Windows)
    for device in input_devices:
        if _device_name_matches(device["name"], mic_array_keywords):
            if _probe_windows_input_device(sd, device):
                _log_selected_input_device(device)
                return _return_input_device_result((device["idx"], device["samplerate"]))

    # Priorität 2: Mikrofon-Geräte (außer Lautsprecher)
    for device in input_devices:
        if _should_skip_windows_device(device["name"]):
            continue
        if _device_name_matches(device["name"], mic_keywords):
            if _probe_windows_input_device(sd, device):
                _log_selected_input_device(device)
                return _return_input_device_result((device["idx"], device["samplerate"]))

    # Priorität 3: Beliebiges funktionierendes Gerät (außer Lautsprecher)
    for device in input_devices:
        if _should_skip_windows_device(device["name"]):
            continue
        if _probe_windows_input_device(sd, device):
            _log_selected_input_device(device)
            return _return_input_device_result((device["idx"], device["samplerate"]))

    # Fallback ohne Test (kann fehlschlagen)
    fallback_device = input_devices[0]
    logger.warning(
        "Kein funktionierendes Gerät gefunden, versuche: %s",
        fallback_device["name"],
    )
    return _return_input_device_result(
        (fallback_device["idx"], fallback_device["samplerate"]),
        cache=False,
    )



def _select_non_windows_input_device(
    input_devices: list[dict[str, Any]],
) -> tuple[int | None, int]:
    """Wählt auf Nicht-Windows-Systemen bevorzugt ein Mikrofon-Gerät."""
    mic_keywords = ("mikrofon", "mic", "microphone")
    for device in input_devices:
        if _device_name_matches(device["name"], mic_keywords):
            _log_selected_input_device(device)
            return _return_input_device_result((device["idx"], device["samplerate"]))

    fallback_device = input_devices[0]
    _log_selected_input_device(fallback_device)
    return _return_input_device_result(
        (fallback_device["idx"], fallback_device["samplerate"])
    )



def get_input_device() -> tuple[int | None, int]:
    """Ermittelt das zu verwendende Eingabegerät und dessen Sample Rate.

    Auf manchen Windows-Systemen ist kein Standard-Eingabegerät gesetzt (device=-1).
    In diesem Fall wird automatisch ein geeignetes Eingabegerät erkannt.

    Windows WDM-KS Treiber sind strikt bei Sample Rates - wir müssen die native
    Rate des Geräts verwenden (kein Resampling im Treiber).

    Das Ergebnis wird gecacht um wiederholte Device-Tests zu vermeiden.

    Priorität:
    1. Mikrofonarray-Geräte (funktionieren meist gut auf Windows)
    2. Mikrofon-Geräte (außer Lautsprecher)
    3. Beliebiges funktionierendes Gerät

    Returns:
        Tuple (device_index, sample_rate):
        - device_index: int oder None für sounddevice-Default
        - sample_rate: Native Sample Rate des Geräts (oder WHISPER_SAMPLE_RATE als Default)
    """
    if _cached_input_device is not None:
        return _cached_input_device

    import sys

    try:
        import sounddevice as sd  # type: ignore[import-not-found]

        default_input = _get_default_input_index(sd)
        if default_input >= 0:
            device = cast(dict[str, Any], sd.query_devices(default_input))
            samplerate = int(device.get("default_samplerate", WHISPER_SAMPLE_RATE))
            return _return_input_device_result((None, samplerate))

        input_devices = _list_input_devices(sd)
        if not input_devices:
            return _return_input_device_result((None, WHISPER_SAMPLE_RATE), cache=False)

        if sys.platform == "win32":
            return _select_windows_input_device(sd, input_devices)
        return _select_non_windows_input_device(input_devices)
    except Exception:
        return _return_input_device_result((None, WHISPER_SAMPLE_RATE), cache=False)



def reset_input_device_cache() -> None:
    """Verwirft die gecachte Input-Device-Erkennung."""
    global _cached_input_device
    _cached_input_device = None

# =============================================================================
# Streaming-Konfiguration
# =============================================================================

INTERIM_THROTTLE_MS = 150  # Max. Update-Rate für Interim-File (Menübar pollt 200ms)
FINALIZE_TIMEOUT = (
    5.0  # Warten auf finale Transkripte (erhöht für Windows/Netzwerk-Latenz)
)
DEEPGRAM_WS_URL = "wss://api.deepgram.com/v1/listen"


def _get_float_env(name: str, default: float) -> float:
    """Liest Float-ENV mit Fallback auf Default bei ungültigen Werten."""
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        logger.warning(f"Ungültiger Wert für {name}='{raw}', verwende Default {default}")
        return default


DEEPGRAM_CLOSE_TIMEOUT = _get_float_env(
    "PULSESCRIBE_DEEPGRAM_CLOSE_TIMEOUT", 0.5
)  # Schneller WebSocket-Shutdown (SDK Default: 10s)

# Keep-Alive Interval für lokale Modelle (Sekunden)
# Verhindert Metal Shader Cache Eviction bei Inaktivität
# 0 = deaktiviert, empfohlen: 45-90s
LOCAL_KEEPALIVE_INTERVAL = _get_float_env("PULSESCRIBE_LOCAL_KEEPALIVE_INTERVAL", 60.0)

# Buffer-Konfiguration für Streaming
def _get_bounded_int_env(
    var_name: str,
    default: int,
    min_value: int,
    max_value: int,
) -> int:
    """Liest Integer-ENV und begrenzt auf sinnvollen Bereich. Fallback auf Default."""
    raw = os.getenv(var_name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return max(min_value, min(value, max_value))


CLI_BUFFER_LIMIT = _get_bounded_int_env(
    "PULSESCRIBE_CLI_BUFFER_LIMIT", default=500, min_value=1, max_value=5000
)  # Max. gepufferte Chunks während WebSocket-Handshake (~10s Audio bei 20ms Chunks)
WARM_STREAM_QUEUE_SIZE = _get_bounded_int_env(
    "PULSESCRIBE_WARM_STREAM_QUEUE_SIZE", default=300, min_value=1, max_value=5000
)  # Queue-Größe für Warm-Stream (~6s Audio bei 20ms Chunks)

# Watchdog: Automatisches Timeout wenn TRANSCRIBING zu lange dauert
# Verhindert "hängendes Overlay" bei Worker-Problemen (z.B. WebSocket-Hänger)
TRANSCRIBING_TIMEOUT = 45.0  # Sekunden (Deepgram + Refine sollten < 30s dauern)

# Deepgram Streaming Timeouts
AUDIO_QUEUE_POLL_INTERVAL = 0.1  # Sekunden zwischen Queue-Polls
SEND_MEDIA_TIMEOUT = 5.0  # Max. Wartezeit für WebSocket send_media()
FORWARDER_THREAD_JOIN_TIMEOUT = 0.5  # Timeout beim Beenden des Forwarder-Threads

# Drain-Konfiguration: Leeren der Audio-Queue nach Aufnahme-Stop
# Pre-Drain: Callback läuft noch, gibt sounddevice Zeit Buffer zu leeren
PRE_DRAIN_DURATION = 0.1  # Pre-Drain Phase bevor Callback gestoppt wird (100ms)
DRAIN_POLL_INTERVAL = 0.01  # Timeout pro Queue.get() während Drain (10ms)
DRAIN_MAX_DURATION = 0.2  # Maximale Drain-Dauer als Safety-Limit (200ms)
DRAIN_EMPTY_THRESHOLD = 2  # Anzahl leerer Polls bevor Drain beendet wird

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
DEFAULT_GEMINI_REFINE_MODEL = "gemini-3-flash-preview"

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

# Temporäre Dateien/IPC
# Plattformunabhängig: Windows nutzt %TEMP%, Unix nutzt /tmp
TEMP_RECORDING_FILENAME = "pulsescribe_recording.wav"
INTERIM_FILE = Path(tempfile.gettempdir()) / "pulsescribe.interim"  # Live-Transkript während Aufnahme

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
    "PRELOAD_WARMUP_DURATION",
    "LOCAL_KEEPALIVE_INTERVAL",
    "KEEPALIVE_WARMUP_DURATION",
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
    "AUDIO_QUEUE_POLL_INTERVAL",
    "SEND_MEDIA_TIMEOUT",
    "FORWARDER_THREAD_JOIN_TIMEOUT",
    "PRE_DRAIN_DURATION",
    "DRAIN_POLL_INTERVAL",
    "DRAIN_MAX_DURATION",
    "DRAIN_EMPTY_THRESHOLD",
    # Models
    "DEFAULT_API_MODEL",
    "DEFAULT_LOCAL_MODEL",
    "DEFAULT_DEEPGRAM_MODEL",
    "DEFAULT_GROQ_MODEL",
    "DEFAULT_REFINE_MODEL",
    "DEFAULT_GEMINI_REFINE_MODEL",
    # IPC
    "TEMP_RECORDING_FILENAME",
    "INTERIM_FILE",
    # API
    "OPENROUTER_BASE_URL",
    # Paths
    "SCRIPT_DIR",
    "LOG_DIR",
    "LOG_FILE",
    "VOCABULARY_FILE",
    "PROMPTS_FILE",
    "reset_input_device_cache",
]
