"""Zentrale Konfiguration für PulseScribe.

Gemeinsame Konstanten für Audio, Streaming und IPC.
Vermeidet Duplikation zwischen Modulen.
"""

import logging
import os
import tempfile
from pathlib import Path

logger = logging.getLogger("pulsescribe")

# =============================================================================
# Audio-Konfiguration
# =============================================================================

# Whisper erwartet Audio mit 16kHz – andere Sampleraten führen zu schlechteren Ergebnissen
WHISPER_SAMPLE_RATE = 16000
WHISPER_CHANNELS = 1
WHISPER_BLOCKSIZE = 1024

# Konstante für Audio-Konvertierung (float32 → int16)
INT16_MAX = 32767


# Cache für erkanntes Audio-Gerät (vermeidet wiederholte Tests)
_cached_input_device: tuple[int | None, int] | None = None


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
    global _cached_input_device

    # Cache-Hit
    if _cached_input_device is not None:
        return _cached_input_device

    def _cache_and_return(result: tuple[int | None, int]) -> tuple[int | None, int]:
        """Cached Ergebnis und gibt es zurück."""
        global _cached_input_device
        _cached_input_device = result
        return result

    import sys

    try:
        import sounddevice as sd

        default_input = sd.default.device[0]

        # Default ist gesetzt → verwenden
        if default_input >= 0:
            dev = sd.query_devices(default_input)
            return _cache_and_return((None, int(dev["default_samplerate"])))

        # Default nicht gesetzt → passendes Input-Device suchen
        devices = sd.query_devices()
        input_devices = []

        for i, dev in enumerate(devices):
            if dev["max_input_channels"] > 0:
                input_devices.append({
                    "idx": i,
                    "name": dev["name"],
                    "samplerate": int(dev["default_samplerate"]),
                })

        if not input_devices:
            return _cache_and_return((None, WHISPER_SAMPLE_RATE))

        import logging
        logger = logging.getLogger("pulsescribe")

        # Auf Windows: Geräte testen, da WDM-KS oft "Invalid device" wirft
        if sys.platform == "win32":
            import numpy as np

            def test_device(idx: int, samplerate: int) -> bool:
                """Testet ob Device öffnet und Audio empfängt."""
                try:
                    received = [False]

                    def cb(indata, frames, time_info, status):
                        received[0] = True

                    stream = sd.InputStream(
                        device=idx,
                        samplerate=samplerate,
                        channels=1,
                        blocksize=1024,
                        dtype=np.int16,
                        callback=cb,
                    )
                    stream.start()
                    import time
                    time.sleep(0.05)  # 50ms reicht um Audio-Callback zu testen
                    stream.stop()
                    stream.close()
                    return received[0]
                except Exception:
                    return False

            # Geräte die wahrscheinlich nicht funktionieren überspringen
            skip_keywords = ("lautsprecher", "speaker", "output", "monitor")

            def should_skip(name: str) -> bool:
                lower = name.lower()
                return any(kw in lower for kw in skip_keywords)

            # Priorität 1: Mikrofonarray-Geräte (funktionieren meist gut auf Windows)
            for dev in input_devices:
                if "mikrofonarray" in dev["name"].lower() or "mic array" in dev["name"].lower():
                    if test_device(dev["idx"], dev["samplerate"]):
                        logger.info(
                            f"Verwende: {dev['name']} ({dev['samplerate']}Hz)"
                        )
                        return _cache_and_return((dev["idx"], dev["samplerate"]))

            # Priorität 2: Mikrofon-Geräte (außer Lautsprecher)
            mic_keywords = ("mikrofon", "mic", "microphone")
            for dev in input_devices:
                if should_skip(dev["name"]):
                    continue
                if any(kw in dev["name"].lower() for kw in mic_keywords):
                    if test_device(dev["idx"], dev["samplerate"]):
                        logger.info(
                            f"Verwende: {dev['name']} ({dev['samplerate']}Hz)"
                        )
                        return _cache_and_return((dev["idx"], dev["samplerate"]))

            # Priorität 3: Beliebiges funktionierendes Gerät (außer Lautsprecher)
            for dev in input_devices:
                if should_skip(dev["name"]):
                    continue
                if test_device(dev["idx"], dev["samplerate"]):
                    logger.info(
                        f"Verwende: {dev['name']} ({dev['samplerate']}Hz)"
                    )
                    return _cache_and_return((dev["idx"], dev["samplerate"]))

            # Fallback ohne Test (kann fehlschlagen)
            dev = input_devices[0]
            logger.warning(
                f"Kein funktionierendes Gerät gefunden, versuche: {dev['name']}"
            )
            return _cache_and_return((dev["idx"], dev["samplerate"]))

        else:
            # Nicht-Windows: Erstes Mikrofon-Gerät oder erstes Input-Device
            mic_keywords = ("mikrofon", "mic", "microphone")
            for dev in input_devices:
                if any(kw in dev["name"].lower() for kw in mic_keywords):
                    logger.info(
                        f"Verwende: {dev['name']} ({dev['samplerate']}Hz)"
                    )
                    return _cache_and_return((dev["idx"], dev["samplerate"]))

            dev = input_devices[0]
            logger.info(
                f"Verwende: {dev['name']} ({dev['samplerate']}Hz)"
            )
            return _cache_and_return((dev["idx"], dev["samplerate"]))

    except Exception:
        return _cache_and_return((None, WHISPER_SAMPLE_RATE))

# =============================================================================
# Streaming-Konfiguration
# =============================================================================

INTERIM_THROTTLE_MS = 150  # Max. Update-Rate für Interim-File (Menübar pollt 200ms)
FINALIZE_TIMEOUT = (
    1.0  # Warten auf finale Transkripte (Deepgram antwortet meist in 300-500ms)
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
]
