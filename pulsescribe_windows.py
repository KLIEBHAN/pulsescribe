"""
PulseScribe Windows Daemon.

Minimaler Windows Entry-Point für Spracheingabe mit:
- Tray-Icon (pystray)
- Globale Hotkeys (pynput) mit Toggle- und/oder Hold-Mode
- Sound-Feedback (Windows System-Sounds)
- Multi-Provider Transkription (Deepgram, Groq, OpenAI, Local)
- WASAPI Warm-Stream für instant-start

Usage:
    python pulsescribe_windows.py                              # Defaults: Toggle=Ctrl+Alt+R, Hold=Ctrl+Win
    python pulsescribe_windows.py --toggle-hotkey "ctrl+alt+r" # Nur Toggle-Mode
    python pulsescribe_windows.py --hold-hotkey "ctrl+win"     # Nur Hold-Mode
    python pulsescribe_windows.py --mode groq

Defaults:
    Toggle-Hotkey: Ctrl+Alt+R (drücken→sprechen→drücken)
    Hold-Hotkey:   Ctrl+Win   (halten→sprechen→loslassen)

Environment Variables (konsistent mit macOS):
    PULSESCRIBE_TOGGLE_HOTKEY - Toggle-Hotkey überschreiben
    PULSESCRIBE_HOLD_HOTKEY   - Hold-Hotkey überschreiben
"""

import sys

if sys.platform != "win32":
    print("Error: This script is Windows-only", file=sys.stderr)
    sys.exit(1)

import argparse
import logging
import os
import queue
import signal
import threading
import time
from pathlib import Path

# Projekt-Root zum Path hinzufügen
PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))

# .env ZUERST laden (vor Logging-Setup, damit PULSESCRIBE_DEBUG wirkt)
from utils.env import load_environment

load_environment()

# Logging Setup (nach .env, damit PULSESCRIBE_DEBUG aus .env funktioniert)
from utils.logging import setup_logging, get_logger

setup_logging(debug=os.getenv("PULSESCRIBE_DEBUG", "").lower() == "true")
logger = get_logger()

# Imports nach Logging-Setup
from utils.state import AppState
from utils.hold_state import HoldHotkeyState
from utils.hotkey import paste_transcript
from whisper_platform import get_clipboard, get_sound_player
from config import INTERIM_FILE, get_input_device, WARM_STREAM_QUEUE_SIZE
from providers import get_provider

# Lazy imports für optionale Features
pystray = None
PIL_Image = None
PIL_ImageDraw = None
WindowsOverlayController = None


def _load_overlay():
    """Lädt Overlay-Controller (lazy). PySide6 bevorzugt, Tkinter als Fallback."""
    global WindowsOverlayController
    # Versuch 1: PySide6 (GPU-beschleunigt, 60 FPS)
    try:
        from ui.overlay_pyside6 import PySide6OverlayController as _Overlay

        WindowsOverlayController = _Overlay
        logger.info("Overlay: PySide6 (GPU-beschleunigt)")
        return True
    except ImportError as e:
        logger.debug(f"PySide6 nicht verfügbar (ImportError): {e}")
    except Exception as e:
        logger.warning(f"PySide6 Fehler: {type(e).__name__}: {e}")

    # Versuch 2: Tkinter (Fallback)
    try:
        from ui.overlay_windows import WindowsOverlayController as _Overlay

        WindowsOverlayController = _Overlay
        logger.info("Overlay: Tkinter (Fallback)")
        return True
    except ImportError as e:
        logger.debug(f"Overlay nicht verfügbar: {e}")
        return False


# =============================================================================
# Hotkey-Helpers (Modul-Level für Wiederverwendung und Testbarkeit)
# =============================================================================

# Virtual Key Codes für Buchstaben A-Z (Windows)
# Mit Ctrl+Alt gedrückt wird 'r' als <82> erkannt, nicht als 'r'
_VK_TO_CHAR = {vk: chr(vk + 32) for vk in range(65, 91)}  # 65='A' -> 'a', etc.

# Debounce-Zeit in Sekunden (verhindert Doppel-Trigger)
_HOTKEY_DEBOUNCE_SEC = 0.3

# Timeout für "stale" Keys (Sekunden) - Keys älter als dies werden entfernt
_KEY_STALE_TIMEOUT_SEC = 2.0

# VAD Threshold: Audio-Level ab dem Sprache erkannt wird
# REST-Modus verwendet Peak (max), Streaming verwendet RMS (niedriger)
_VAD_THRESHOLD_PEAK = 0.01  # Für REST-Modus (float32 peak)
_VAD_THRESHOLD_RMS = 0.003  # Für Streaming-Modus (RMS/INT16_MAX)

# Tail-Padding: Stille am Ende des Audio (verhindert abgeschnittene Wörter bei Whisper)
_TAIL_PADDING_SEC = 0.2

# Provider die gecached werden sollen (stateful, z.B. Model-Caching)
_STATEFUL_PROVIDERS = {"local"}

# Default-Hotkeys (verwendet bei Startup und Reload wenn nichts konfiguriert)
_DEFAULT_TOGGLE_HOTKEY = "ctrl+alt+r"
_DEFAULT_HOLD_HOTKEY = "ctrl+win"


def _resample_audio(audio, from_rate: int, to_rate: int):
    """Resampled Audio-Array von from_rate auf to_rate.

    Verwendet scipy.signal.resample wenn verfügbar, sonst lineare Interpolation.
    Für Downsampling (z.B. 48kHz → 16kHz) ist die Qualität ausreichend für Sprache.
    """
    import numpy as np

    # Edge-Case: Leeres Audio (z.B. VAD trimmt alles)
    if len(audio) == 0:
        return np.array([], dtype=np.float32)

    if from_rate == to_rate:
        return audio

    # Ziel-Länge berechnen
    new_length = int(len(audio) * to_rate / from_rate)

    # Versuch 1: scipy (beste Qualität, Anti-Aliasing)
    try:
        from scipy.signal import resample
        return resample(audio, new_length).astype(np.float32)
    except ImportError:
        pass

    # Fallback: numpy lineare Interpolation (ausreichend für Sprache)
    return np.interp(
        np.linspace(0, len(audio) - 1, new_length),
        np.arange(len(audio)),
        audio,
    ).astype(np.float32)


def _load_tray_dependencies():
    """Lädt pystray und Pillow (lazy)."""
    global pystray, PIL_Image, PIL_ImageDraw
    try:
        import pystray as _pystray
        from PIL import Image as _Image
        from PIL import ImageDraw as _ImageDraw

        pystray = _pystray
        PIL_Image = _Image
        PIL_ImageDraw = _ImageDraw
        return True
    except ImportError as e:
        logger.warning(f"Tray-Icon nicht verfügbar: {e}")
        return False


class PulseScribeWindows:
    """Windows-Daemon mit Tray-Icon, Hotkey und Deepgram-Streaming."""

    # Tray-Icon Farben (RGB)
    COLORS = {
        AppState.IDLE: (128, 128, 128),  # Grau
        AppState.LOADING: (0, 120, 255),  # Blau (Model wird geladen)
        AppState.LISTENING: (255, 165, 0),  # Orange
        AppState.RECORDING: (255, 0, 0),  # Rot
        AppState.TRANSCRIBING: (255, 255, 0),  # Gelb
        AppState.REFINING: (0, 255, 255),  # Cyan
        AppState.DONE: (0, 255, 0),  # Grün
        AppState.ERROR: (255, 0, 0),  # Rot
    }

    # Icon-Cache: Vermeidet Neuzeichnen bei State-Wechsel (key = RGB color tuple)
    _icon_cache: dict[tuple[int, int, int], "PIL_Image.Image"] = {}

    def __init__(
        self,
        toggle_hotkey: str | None = None,
        hold_hotkey: str | None = None,
        mode: str = "deepgram",
        auto_paste: bool = True,
        refine: bool = False,
        refine_model: str | None = None,
        refine_provider: str | None = None,
        context: str | None = None,
        streaming: bool = True,
        overlay: bool = True,
    ):
        self.toggle_hotkey = toggle_hotkey
        self.hold_hotkey = hold_hotkey
        self.mode = mode
        self.auto_paste = auto_paste
        self.refine = refine
        self.refine_model = refine_model
        self.refine_provider = refine_provider
        self.context = context
        self.streaming = streaming
        self.overlay_enabled = overlay

        # State
        self._state = AppState.IDLE
        self._state_lock = threading.Lock()
        self._last_hotkey_time = 0.0  # Für Debouncing

        # Hold-Mode State (wie macOS)
        self._hold_state = HoldHotkeyState()

        # Components
        self._tray = None
        self._hotkey_listeners: list = []  # Mehrere Listener (toggle + hold)
        self._recording_thread = None
        self._stop_event = threading.Event()  # App beenden
        self._recording_stop_event = threading.Event()  # Recording stoppen
        self._prewarm_complete = threading.Event()  # Pre-Warm abgeschlossen
        self._overlay = None
        self._event_loop = None  # Wird in _prewarm_imports() erstellt

        # Watchdog für hängende Transcription (wie macOS)
        self._transcribing_timeout = 30.0  # Sekunden
        self._transcribing_watchdog: threading.Timer | None = None

        # Audio buffer für REST-Modus
        self._audio_buffer = []
        self._audio_sample_rate = 16000  # Default, wird in _recording_loop aktualisiert
        self._audio_lock = threading.Lock()

        # ═══════════════════════════════════════════════════════════════════
        # WARM-STREAM: Mikrofon läuft immer, instant-start beim Hotkey
        # ═══════════════════════════════════════════════════════════════════
        self._warm_stream = None  # sd.InputStream (läuft dauerhaft)
        self._warm_stream_armed = threading.Event()  # Wenn gesetzt: Samples sammeln
        # Queue mit maxsize: via PULSESCRIBE_WARM_STREAM_QUEUE_SIZE (default: 300)
        # Verhindert Memory Leak wenn Forwarder nicht läuft
        self._warm_stream_queue: queue.Queue[bytes] = queue.Queue(maxsize=WARM_STREAM_QUEUE_SIZE)
        self._warm_stream_sample_rate = 16000  # Wird beim Start aktualisiert
        self._is_prewarm_loading = False  # Unterscheidet Pre-Warm von Recording LOADING

        # Provider-Cache (wichtig für LocalProvider - cached Modelle intern)
        self._provider_cache: dict[str, object] = {}

        # Settings-Reload (FileWatcher + Polling-Fallback)
        self._env_observer = None
        self._reload_polling_timer: threading.Timer | None = None
        self._reload_signal_file: Path | None = None

        stream_mode = "Streaming" if streaming else "REST"
        hotkey_info = []
        if toggle_hotkey:
            hotkey_info.append(f"Toggle: {toggle_hotkey}")
        if hold_hotkey:
            hotkey_info.append(f"Hold: {hold_hotkey}")
        hotkey_str = ", ".join(hotkey_info) if hotkey_info else "Keiner"
        logger.info(
            f"PulseScribeWindows initialisiert (Hotkeys: {hotkey_str}, "
            f"Provider: {mode} [{stream_mode}], Refine: {refine}, Overlay: {overlay})"
        )

        # Event Loop Policy einmal setzen (nicht bei jedem Recording)
        # Windows: SelectorEventLoop für bessere Kompatibilität mit asyncio-Libs
        if streaming:
            import asyncio

            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    @property
    def state(self) -> AppState:
        with self._state_lock:
            return self._state

    def _set_state(self, state: AppState, text: str | None = None):
        """Setzt State und aktualisiert Tray-Icon + Overlay."""
        with self._state_lock:
            old_state = self._state
            self._state = state

        if old_state != state:
            logger.info(f"State: {old_state.value} → {state.value}")
            self._update_tray_icon()

            # Watchdog-Management (wie macOS)
            if state == AppState.TRANSCRIBING:
                self._start_transcribing_watchdog()
            elif state in (AppState.DONE, AppState.ERROR, AppState.IDLE):
                self._stop_transcribing_watchdog()

            # Overlay aktualisieren
            if self._overlay:
                self._overlay.update_state(state.name, text)

    def _start_transcribing_watchdog(self):
        """Startet Watchdog-Timer für hängende Transcription."""
        self._stop_transcribing_watchdog()

        def timeout_handler():
            if self.state == AppState.TRANSCRIBING:
                logger.error(
                    f"Transcription-Timeout nach {self._transcribing_timeout}s"
                )
                self._set_state(AppState.ERROR)
                self._play_sound("error")
                threading.Timer(2.0, lambda: self._set_state(AppState.IDLE)).start()

        self._transcribing_watchdog = threading.Timer(
            self._transcribing_timeout, timeout_handler
        )
        self._transcribing_watchdog.daemon = True
        self._transcribing_watchdog.start()

    def _stop_transcribing_watchdog(self):
        """Stoppt Watchdog-Timer."""
        if self._transcribing_watchdog is not None:
            self._transcribing_watchdog.cancel()
            self._transcribing_watchdog = None

    def _update_tray_icon(self):
        """Aktualisiert Tray-Icon basierend auf State."""
        if self._tray is None or PIL_Image is None or PIL_ImageDraw is None:
            return

        color = self.COLORS.get(self.state, (128, 128, 128))
        icon = self._create_icon(color)
        self._tray.icon = icon

        # Tooltip aktualisieren
        state_text = {
            AppState.IDLE: "Bereit",
            AppState.LOADING: "Lade Modell...",
            AppState.LISTENING: "Warte auf Sprache...",
            AppState.RECORDING: "Aufnahme...",
            AppState.TRANSCRIBING: "Transkribiere...",
            AppState.REFINING: "Verfeinere...",
            AppState.DONE: "Fertig",
            AppState.ERROR: "Fehler",
        }
        self._tray.title = f"PulseScribe - {state_text.get(self.state, 'Unbekannt')}"

    def _create_icon(self, color: tuple[int, int, int]) -> "PIL_Image.Image":
        """Erstellt ein Mikrofon-Icon wie bei macOS (mit Caching)."""
        # Cache-Lookup: Gleiches Icon für gleiche Farbe wiederverwenden
        if color in PulseScribeWindows._icon_cache:
            return PulseScribeWindows._icon_cache[color]

        # Fallback auf einfaches farbiges Icon wenn ImageDraw nicht verfügbar
        if PIL_ImageDraw is None:
            icon = PIL_Image.new("RGB", (64, 64), color)
            PulseScribeWindows._icon_cache[color] = icon
            return icon

        icon = self._draw_microphone_icon(color)
        PulseScribeWindows._icon_cache[color] = icon
        return icon

    def _draw_microphone_icon(self, color: tuple[int, int, int]) -> "PIL_Image.Image":
        """Zeichnet das Mikrofon-Icon (interne Methode)."""
        size = 64  # Feste Größe für Windows Tray-Icons
        # Transparenter Hintergrund für sauberes Tray-Icon
        image = PIL_Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = PIL_ImageDraw.Draw(image)

        # Mikrofon-Proportionen (zentriert)
        center_x = size // 2
        mic_width = 20
        mic_height = 28
        mic_top = 8
        mic_bottom = mic_top + mic_height

        # 1. Mikrofon-Körper (abgerundete Kapsel)
        mic_left = center_x - mic_width // 2
        mic_right = center_x + mic_width // 2
        # Oberer Halbkreis
        draw.ellipse(
            [mic_left, mic_top, mic_right, mic_top + mic_width],
            fill=color,
        )
        # Rechteckiger Körper
        draw.rectangle(
            [mic_left, mic_top + mic_width // 2, mic_right, mic_bottom - mic_width // 2],
            fill=color,
        )
        # Unterer Halbkreis
        draw.ellipse(
            [mic_left, mic_bottom - mic_width, mic_right, mic_bottom],
            fill=color,
        )

        # 2. Halterung (U-Form unter dem Mikrofon)
        holder_top = mic_bottom + 2
        holder_width = mic_width + 8
        holder_left = center_x - holder_width // 2
        holder_right = center_x + holder_width // 2
        line_width = 3

        # Linke Seite der Halterung
        draw.rectangle(
            [holder_left, holder_top - 6, holder_left + line_width, holder_top + 8],
            fill=color,
        )
        # Rechte Seite der Halterung
        draw.rectangle(
            [holder_right - line_width, holder_top - 6, holder_right, holder_top + 8],
            fill=color,
        )
        # Unterer Bogen (vereinfacht als Linie)
        draw.rectangle(
            [holder_left, holder_top + 5, holder_right, holder_top + 8],
            fill=color,
        )

        # 3. Ständer (vertikale Linie + Fuß)
        stand_top = holder_top + 8
        stand_bottom = size - 6
        stand_width = 3
        # Vertikale Linie
        draw.rectangle(
            [center_x - stand_width // 2, stand_top, center_x + stand_width // 2, stand_bottom - 3],
            fill=color,
        )
        # Fuß (horizontale Linie)
        foot_width = 16
        draw.rectangle(
            [center_x - foot_width // 2, stand_bottom - 3, center_x + foot_width // 2, stand_bottom],
            fill=color,
        )

        return image

    def _play_sound(self, sound_type: str):
        """Spielt System-Sound ab."""
        try:
            get_sound_player().play(sound_type)
        except Exception as e:
            logger.debug(f"Sound-Fehler: {e}")

    def _get_provider(self, mode: str):
        """Gibt Provider zurück (cached für stateful Provider).

        Stateful Provider (siehe _STATEFUL_PROVIDERS) cachen z.B. Modelle intern.
        Ohne Provider-Cache würde jeder get_provider()-Aufruf eine neue Instanz
        erstellen und das interne Caching umgehen.
        """
        if mode in _STATEFUL_PROVIDERS:
            if mode not in self._provider_cache:
                self._provider_cache[mode] = get_provider(mode)
            return self._provider_cache[mode]
        # Stateless Provider: kein Caching nötig (API-Calls)
        return get_provider(mode)

    def _get_transcription_config(self) -> tuple[str | None, str]:
        """Gibt (model, language) für Transkription zurück.

        Zentralisiert die Konfigurationslogik für alle Provider-Modi.
        Local-Mode verwendet PULSESCRIBE_LOCAL_MODEL (default: base),
        andere Modi verwenden PULSESCRIBE_MODEL (default: Provider-spezifisch).
        """
        language = os.getenv("PULSESCRIBE_LANGUAGE", "de")
        if self.mode == "local":
            # Default "base" für Windows (schneller als turbo)
            model = os.getenv("PULSESCRIBE_LOCAL_MODEL", "base")
        else:
            # None = Provider-Default (z.B. nova-3 für Deepgram)
            model = os.getenv("PULSESCRIBE_MODEL")
        return model, language

    # ═══════════════════════════════════════════════════════════════════════════
    # WARM-STREAM: Mikrofon läuft immer, instant-start beim Hotkey
    # ═══════════════════════════════════════════════════════════════════════════

    def _start_warm_stream(self):
        """Startet den dauerhaft laufenden Audio-Stream.

        Der Stream läuft im Hintergrund und sammelt Audio nur wenn armed.
        Ermöglicht instant-start Recording ohne WASAPI-Cold-Start-Delay.
        """
        import numpy as np
        import sounddevice as sd

        from config import INT16_MAX

        input_device, sample_rate = get_input_device()
        self._warm_stream_sample_rate = sample_rate

        # Blocksize: ~64ms Chunks (wie in deepgram_stream)
        blocksize = int(sample_rate * 0.064)

        def audio_callback(indata, frames, time_info, status):
            """Audio-Callback: Samples sammeln wenn armed, sonst verwerfen."""
            if status:
                logger.debug(f"Warm-Stream Status: {status}")

            # RMS immer berechnen (für VAD, unabhängig von Overlay)
            rms = float(np.sqrt(np.mean(indata.astype(np.float32) ** 2)) / INT16_MAX)

            # Audio-Level für Overlay (optional)
            if self._overlay:
                self._overlay.update_audio_level(rms)

            # VAD: State-Transition LISTENING → RECORDING (nur wenn armed)
            if self._warm_stream_armed.is_set():
                current_state = self.state
                if current_state == AppState.LISTENING and rms > _VAD_THRESHOLD_RMS:
                    logger.debug(f"VAD triggered: level={rms:.4f}")
                    self._set_state(AppState.RECORDING)

            # Audio sammeln nur wenn armed
            if self._warm_stream_armed.is_set():
                audio_bytes = indata.tobytes()
                try:
                    self._warm_stream_queue.put_nowait(audio_bytes)
                except queue.Full:
                    # Queue voll - Audio-Chunk verworfen (z.B. bei langer REST-Transkription)
                    if not hasattr(self, "_warm_stream_overflow_logged"):
                        self._warm_stream_overflow_logged = True
                        logger.warning("Warm-Stream Queue voll, Audio-Chunks werden verworfen")

        try:
            self._warm_stream = sd.InputStream(
                device=input_device,
                samplerate=sample_rate,
                channels=1,
                blocksize=blocksize,
                dtype=np.int16,
                callback=audio_callback,
            )
            self._warm_stream.start()
            logger.info(
                f"Warm-Stream gestartet: Device={input_device}, "
                f"{sample_rate}Hz, blocksize={blocksize}"
            )
        except Exception as e:
            logger.error(f"Warm-Stream konnte nicht gestartet werden: {e}")
            self._warm_stream = None

    def _stop_warm_stream(self):
        """Stoppt den dauerhaft laufenden Audio-Stream."""
        if self._warm_stream is not None:
            try:
                self._warm_stream.stop()
                self._warm_stream.close()
                logger.info("Warm-Stream gestoppt")
            except Exception as e:
                logger.debug(f"Warm-Stream Stop-Fehler: {e}")
            self._warm_stream = None

    def _on_hotkey_press(self):
        """Callback wenn Hotkey gedrückt wird (Toggle-Mode)."""
        if self.state == AppState.IDLE:
            self._start_recording()
        elif self.state == AppState.LOADING and self._is_prewarm_loading:
            # Pre-Warm LOADING: Ignorieren, System noch nicht bereit
            logger.debug("Hotkey ignoriert: Pre-Warm noch nicht abgeschlossen")
        elif self.state in (AppState.LOADING, AppState.LISTENING, AppState.RECORDING):
            self._stop_recording()

    def _start_recording_from_hold(self, source_id: str):
        """Startet Recording nur wenn der Hold-Hotkey noch aktiv ist."""
        # Race-Condition Check: Key wurde losgelassen bevor wir hier ankamen
        if not self._hold_state.is_active(source_id):
            logger.debug(f"Hold abgebrochen (Race): {source_id} nicht mehr aktiv")
            return

        # Bereits am Aufnehmen
        if self.state not in (AppState.IDLE,):
            logger.debug(f"Hold-Recording ignoriert: State={self.state}")
            return

        logger.debug(f"Hold-Recording starten: {source_id}")
        self._start_recording()

        # Flag NUR setzen wenn Recording tatsächlich gestartet
        if self.state in (AppState.LISTENING, AppState.RECORDING, AppState.LOADING):
            self._hold_state.mark_started()

    def _stop_recording_from_hotkey(self):
        """Stoppt Recording (aufgerufen bei Hold-Release).

        Wie macOS: Einheitlicher Name für Stop-Aktion von Hotkey.
        """
        if self.state in (AppState.LISTENING, AppState.RECORDING):
            logger.debug("Hold-Release → Recording stoppen")
            self._stop_recording()  # ruft hold_state.reset() auf

    def _start_recording(self):
        """Startet Aufnahme (Streaming oder REST)."""
        logger.info(f"Starte Aufnahme ({'Streaming' if self.streaming else 'REST'})...")

        # Recording-Stop-Event zurücksetzen
        self._recording_stop_event.clear()

        if self.streaming:
            # Prüfe ob Warm-Stream verfügbar (instant-start)
            if self._warm_stream is not None:
                # ═══════════════════════════════════════════════════════════════
                # WARM-STREAM MODE: Mikrofon läuft bereits, instant-start!
                # ═══════════════════════════════════════════════════════════════
                logger.info("Warm-Stream Mode: instant-start")

                # Queue leeren (alte Samples verwerfen)
                while not self._warm_stream_queue.empty():
                    try:
                        self._warm_stream_queue.get_nowait()
                    except queue.Empty:
                        break

                # Sofort LISTENING setzen und Sound spielen
                self._set_state(AppState.LISTENING)
                self._play_sound("ready")

                # Worker mit Warm-Stream starten
                self._recording_thread = threading.Thread(
                    target=self._streaming_worker_warm, daemon=True
                )
            else:
                # Fallback: Kein Warm-Stream, nutze alten Cold-Start-Pfad
                logger.warning("Kein Warm-Stream - Fallback auf Cold-Start")
                self._set_state(AppState.LOADING)

                if not self._prewarm_complete.is_set():
                    logger.debug("Warte auf Pre-Warm...")
                    if not self._prewarm_complete.wait(timeout=1.0):
                        logger.warning("Pre-Warm Timeout - starte trotzdem")

                self._recording_thread = threading.Thread(
                    target=self._streaming_worker, daemon=True
                )
        else:
            # REST-Mode (Groq, OpenAI, Local)
            # Prüfe ob Warm-Stream verfügbar (instant-start)
            if self._warm_stream is not None:
                logger.info("REST-Mode mit Warm-Stream: instant-start")

                # Queue leeren (alte Samples verwerfen)
                while not self._warm_stream_queue.empty():
                    try:
                        self._warm_stream_queue.get_nowait()
                    except queue.Empty:
                        break

                # Sofort LISTENING setzen und Sound spielen
                self._set_state(AppState.LISTENING)
                self._play_sound("ready")

                # Recording-Loop mit Warm-Stream
                self._recording_thread = threading.Thread(
                    target=self._recording_loop_warm, daemon=True
                )
            else:
                # Fallback: Kein Warm-Stream, Cold-Start
                logger.warning("Kein Warm-Stream - Fallback auf Cold-Start")
                self._set_state(AppState.LISTENING)
                self._play_sound("ready")
                time.sleep(0.1)  # Sound abspielen lassen vor Audio-Stream
                self._recording_thread = threading.Thread(
                    target=self._recording_loop, daemon=True
                )
        self._recording_thread.start()

    def _stop_recording(self):
        """Stoppt Aufnahme und startet Transkription."""
        logger.info("Stoppe Aufnahme...")
        self._play_sound("stop")

        # Hold-Flag zurücksetzen - egal wie Recording gestoppt wurde
        self._hold_state.reset()

        # Signal zum Stoppen (nur Recording, nicht App)
        self._recording_stop_event.set()

        if self.streaming:
            # Streaming: Worker beendet sich selbst via stop_event
            # Ergebnis kommt automatisch (kein separater Transcribe-Thread nötig)
            pass
        else:
            # REST: Auf Recording-Thread warten, dann transcribieren
            if self._recording_thread and self._recording_thread.is_alive():
                self._recording_thread.join(timeout=2.0)
            self._set_state(AppState.TRANSCRIBING)
            threading.Thread(target=self._transcribe_rest, daemon=True).start()

    def _recording_loop(self):
        """Audio-Aufnahme Loop (läuft in separatem Thread)."""
        try:
            import sounddevice as sd
            import numpy as np

            channels = 1
            chunk_duration = 0.1  # 100ms chunks

            # Device und native Sample Rate ermitteln
            input_device, actual_sample_rate = get_input_device()

            with self._audio_lock:
                self._audio_buffer = []
                self._audio_sample_rate = actual_sample_rate  # Für _transcribe_rest

            def audio_callback(indata, frames, time_info, status):
                if status:
                    logger.warning(f"Audio-Status: {status}")
                with self._audio_lock:
                    self._audio_buffer.append(indata.copy())

                # Audio-Level für Overlay (AGC im Overlay normalisiert automatisch)
                if self._overlay:
                    rms = float(np.sqrt(np.mean(indata**2)))
                    self._overlay.update_audio_level(rms)

                # State auf RECORDING setzen wenn Audio erkannt
                if self.state == AppState.LISTENING:
                    # Einfache VAD: Prüfe ob Audio über Threshold (Peak für REST)
                    if np.abs(indata).max() > _VAD_THRESHOLD_PEAK:
                        self._set_state(AppState.RECORDING)

            with sd.InputStream(
                device=input_device,
                samplerate=actual_sample_rate,
                channels=channels,
                dtype="float32",
                callback=audio_callback,
                blocksize=int(actual_sample_rate * chunk_duration),
            ):
                while not self._recording_stop_event.is_set():
                    time.sleep(0.05)

        except ImportError:
            logger.error("sounddevice nicht installiert")
            self._set_state(AppState.ERROR)
            self._play_sound("error")
            time.sleep(1.0)
            self._set_state(AppState.IDLE)
        except Exception as e:
            logger.error(f"Recording-Fehler: {e}")
            self._set_state(AppState.ERROR)
            self._play_sound("error")
            time.sleep(1.0)
            self._set_state(AppState.IDLE)

    def _recording_loop_warm(self):
        """Audio-Aufnahme Loop mit Warm-Stream (instant-start für REST-Modi).

        Nutzt den bereits laufenden Warm-Stream statt einen neuen zu öffnen.
        Sammelt Audio in Buffer für spätere REST-Transkription.
        """
        import numpy as np
        from config import INT16_MAX

        logger.debug("Recording-Loop (Warm) gestartet")

        try:
            # Buffer vorbereiten
            with self._audio_lock:
                self._audio_buffer = []
                self._audio_sample_rate = self._warm_stream_sample_rate

            # Warm-Stream armen
            self._warm_stream_armed.set()
            logger.debug("Warm-Stream armed für REST-Recording")

            # Audio sammeln bis Stop-Signal
            # VAD wird im audio_callback des Warm-Streams gehandhabt (nicht hier)
            while not self._recording_stop_event.is_set():
                try:
                    # Audio-Chunk aus Queue holen (mit Timeout für Stop-Check)
                    chunk = self._warm_stream_queue.get(timeout=0.1)

                    # Chunk zu Buffer hinzufügen (int16 -> float32 für Kompatibilität)
                    audio_int16 = np.frombuffer(chunk, dtype=np.int16)
                    audio_float32 = audio_int16.astype(np.float32) / INT16_MAX

                    with self._audio_lock:
                        self._audio_buffer.append(audio_float32)

                except queue.Empty:
                    continue

            logger.debug("Recording-Loop (Warm) beendet")

        except Exception as e:
            logger.error(f"Recording-Fehler (Warm): {e}")
            self._set_state(AppState.ERROR)
            self._play_sound("error")
            time.sleep(1.0)
            self._set_state(AppState.IDLE)
        finally:
            # Warm-Stream disarmen
            self._warm_stream_armed.clear()

    def _streaming_worker(self):
        """Streaming-Worker: Recording + Transcription via WebSocket."""
        import asyncio

        logger.debug("Streaming-Worker gestartet")
        transcript = ""

        try:
            from providers.deepgram_stream import deepgram_stream_core

            # Event-Loop: Gecachten aus Pre-Warm verwenden oder neu erstellen
            # Policy wurde bereits im __init__ gesetzt
            if self._event_loop is not None:
                loop = self._event_loop
                self._event_loop = None  # Nur einmal verwenden, danach neu erstellen
            else:
                loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            try:
                logger.debug("Starte deepgram_stream_core")

                # Audio-Level Callback für Overlay + State-Transitions
                # Wird alle ~64ms aufgerufen (1024 samples @ 16kHz)
                def on_audio_level(level: float):
                    if self._overlay:
                        self._overlay.update_audio_level(level)

                    # State-Machine: LOADING → LISTENING → RECORDING
                    current_state = self.state
                    if current_state == AppState.LOADING:
                        # Erster Audio-Callback = Mikrofon ist bereit
                        logger.debug("Mikrofon bereit → LISTENING")
                        self._set_state(AppState.LISTENING)
                    elif current_state == AppState.LISTENING and level > _VAD_THRESHOLD_RMS:
                        # VAD: Sprache erkannt
                        logger.debug(f"VAD triggered: level={level:.4f} > threshold={_VAD_THRESHOLD_RMS}")
                        self._set_state(AppState.RECORDING)

                transcript = loop.run_until_complete(
                    deepgram_stream_core(
                        model="nova-3",
                        language=os.getenv("PULSESCRIBE_LANGUAGE", "de"),
                        play_ready=True,  # Sound nach Mic-Init (wie macOS)
                        external_stop_event=self._recording_stop_event,
                        audio_level_callback=on_audio_level,  # Immer übergeben für State-Transitions
                    )
                )
                logger.debug(f"Streaming abgeschlossen: {len(transcript)} Zeichen")

                if transcript:
                    self._set_state(AppState.TRANSCRIBING)

                    # LLM-Nachbearbeitung (optional)
                    if self.refine:
                        self._set_state(AppState.REFINING)
                        from refine.llm import maybe_refine_transcript

                        t_refine_start = time.perf_counter()
                        transcript = maybe_refine_transcript(
                            transcript,
                            refine=True,
                            refine_model=self.refine_model,
                            refine_provider=self.refine_provider,
                            context=self.context,
                        )
                        t_refine = time.perf_counter() - t_refine_start
                        logger.info(
                            f"Refine: provider={self.refine_provider}, "
                            f"model={self.refine_model}, time={t_refine:.2f}s"
                        )

                    self._handle_result(transcript)
                else:
                    logger.warning("Leeres Transkript")
                    self._set_state(AppState.IDLE)

            finally:
                loop.close()

        except ImportError as e:
            logger.error(f"Import-Fehler: {e}")
            self._set_state(AppState.ERROR)
            self._play_sound("error")
            time.sleep(1.0)
            self._set_state(AppState.IDLE)
        except Exception as e:
            logger.error(f"Streaming-Fehler: {e}")
            self._set_state(AppState.ERROR)
            self._play_sound("error")
            time.sleep(1.0)
            self._set_state(AppState.IDLE)

    def _streaming_worker_warm(self):
        """Streaming-Worker mit Warm-Stream (instant-start).

        Nutzt den bereits laufenden Warm-Stream statt einen neuen zu öffnen.
        Reduziert Start-Latenz von ~2-3s auf ~ms.
        """
        import asyncio

        logger.debug("Streaming-Worker (Warm) gestartet")
        transcript = ""

        try:
            from providers.deepgram_stream import deepgram_stream_core, WarmStreamSource

            # WarmStreamSource erstellen mit Referenzen auf unseren Warm-Stream
            warm_source = WarmStreamSource(
                audio_queue=self._warm_stream_queue,
                sample_rate=self._warm_stream_sample_rate,
                arm_event=self._warm_stream_armed,
                stream=self._warm_stream,
            )

            # Event-Loop erstellen
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            try:
                logger.debug("Starte deepgram_stream_core mit Warm-Stream")

                transcript = loop.run_until_complete(
                    deepgram_stream_core(
                        model="nova-3",
                        language=os.getenv("PULSESCRIBE_LANGUAGE", "de"),
                        play_ready=False,  # Sound haben wir schon gespielt!
                        external_stop_event=self._recording_stop_event,
                        warm_stream_source=warm_source,
                    )
                )
                logger.debug(f"Streaming abgeschlossen: {len(transcript)} Zeichen")

                if transcript:
                    self._set_state(AppState.TRANSCRIBING)

                    # LLM-Nachbearbeitung (optional)
                    if self.refine:
                        self._set_state(AppState.REFINING)
                        from refine.llm import maybe_refine_transcript

                        t_refine_start = time.perf_counter()
                        transcript = maybe_refine_transcript(
                            transcript,
                            refine=True,
                            refine_model=self.refine_model,
                            refine_provider=self.refine_provider,
                            context=self.context,
                        )
                        t_refine = time.perf_counter() - t_refine_start
                        logger.info(
                            f"Refine: provider={self.refine_provider}, "
                            f"model={self.refine_model}, time={t_refine:.2f}s"
                        )

                    self._handle_result(transcript)
                else:
                    logger.warning("Leeres Transkript")
                    self._set_state(AppState.IDLE)

            finally:
                loop.close()
                # Disarm wird automatisch in deepgram_stream_core finally gemacht

        except ImportError as e:
            logger.error(f"Import-Fehler: {e}")
            self._warm_stream_armed.clear()  # Safety: Disarm bei frühem Fehler
            self._set_state(AppState.ERROR)
            self._play_sound("error")
            time.sleep(1.0)
            self._set_state(AppState.IDLE)
        except Exception as e:
            logger.error(f"Streaming-Fehler (Warm): {e}")
            self._warm_stream_armed.clear()  # Safety: Disarm bei frühem Fehler
            self._set_state(AppState.ERROR)
            self._play_sound("error")
            time.sleep(1.0)
            self._set_state(AppState.IDLE)

    def _transcribe_rest(self):
        """Transkribiert aufgenommenes Audio via REST API."""
        try:
            import numpy as np

            # Audio-Buffer zusammenfügen
            with self._audio_lock:
                if not self._audio_buffer:
                    logger.warning("Kein Audio aufgenommen")
                    self._set_state(AppState.IDLE)
                    return

                audio_data = np.concatenate(self._audio_buffer)
                sample_rate = self._audio_sample_rate
                self._audio_buffer = []

            duration = len(audio_data) / sample_rate
            logger.info(f"Transkribiere {duration:.1f}s Audio ({sample_rate}Hz)...")

            # Konfiguration holen (zentralisiert für alle Modi)
            model, language = self._get_transcription_config()
            provider = self._get_provider(self.mode)

            # Local-Mode: In-Memory Transkription (kein WAV schreiben)
            if self.mode == "local" and hasattr(provider, "transcribe_audio"):
                from config import WHISPER_SAMPLE_RATE

                # Tail-Padding (verhindert abgeschnittene letzte Wörter bei Whisper)
                tail_samples = int(sample_rate * _TAIL_PADDING_SEC)
                audio_data = np.concatenate(
                    [audio_data, np.zeros(tail_samples, dtype=np.float32)]
                )

                # Resampling auf 16kHz (Whisper erwartet WHISPER_SAMPLE_RATE)
                if sample_rate != WHISPER_SAMPLE_RATE:
                    audio_data = _resample_audio(audio_data, sample_rate, WHISPER_SAMPLE_RATE)
                    logger.debug(
                        f"Audio resampled: {sample_rate}Hz → {WHISPER_SAMPLE_RATE}Hz"
                    )

                transcript = provider.transcribe_audio(
                    audio_data, model=model, language=language
                )
            else:
                # Andere Provider: WAV-Datei schreiben
                import soundfile as sf
                import tempfile
                from pathlib import Path

                with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                    temp_path = Path(f.name)

                try:
                    sf.write(temp_path, audio_data, sample_rate)
                    transcript = provider.transcribe(
                        audio_path=temp_path, model=model, language=language
                    )
                finally:
                    if temp_path.exists():
                        temp_path.unlink()

            if transcript:
                # LLM-Nachbearbeitung (optional)
                if self.refine:
                    self._set_state(AppState.REFINING)
                    from refine.llm import maybe_refine_transcript

                    t_refine_start = time.perf_counter()
                    transcript = maybe_refine_transcript(
                        transcript,
                        refine=True,
                        refine_model=self.refine_model,
                        refine_provider=self.refine_provider,
                        context=self.context,
                    )
                    t_refine = time.perf_counter() - t_refine_start
                    logger.info(
                        f"Refine: provider={self.refine_provider}, "
                        f"model={self.refine_model}, time={t_refine:.2f}s"
                    )

                self._handle_result(transcript)
            else:
                logger.warning("Leeres Transkript")
                self._set_state(AppState.IDLE)

        except ImportError as e:
            logger.error(f"Import-Fehler: {e}")
            self._set_state(AppState.ERROR)
            self._play_sound("error")
            time.sleep(1.0)
            self._set_state(AppState.IDLE)
        except Exception as e:
            logger.error(f"Transkriptions-Fehler: {e}")
            self._set_state(AppState.ERROR)
            self._play_sound("error")
            time.sleep(1.0)
            self._set_state(AppState.IDLE)

    def _save_to_history(self, transcript: str) -> None:
        """Speichert Transkript in der Historie."""
        from utils.history import save_transcript

        try:
            save_transcript(
                transcript,
                mode=self.mode,
                language=os.getenv("PULSESCRIBE_LANGUAGE", "de"),
                refined=self.refine,
            )
        except Exception as e:
            logger.warning(f"History save failed: {e}")

    def _handle_result(self, transcript: str):
        """Verarbeitet Transkriptions-Ergebnis."""
        logger.info(f"Transkript: {transcript[:50]}...")
        self._set_state(AppState.DONE)
        self._play_sound("done")
        self._save_to_history(transcript)

        if self.auto_paste:
            success = paste_transcript(transcript)
            if not success:
                # Fallback: Nur in Clipboard kopieren
                get_clipboard().copy(transcript)
                logger.info(
                    "Text in Zwischenablage kopiert (Auto-Paste fehlgeschlagen)"
                )
        else:
            get_clipboard().copy(transcript)
            logger.info("Text in Zwischenablage kopiert")

        # Nach kurzer Pause zurück zu IDLE (Timer statt sleep, blockiert Thread nicht)
        threading.Timer(1.0, lambda: self._set_state(AppState.IDLE)).start()

    def _setup_hotkey(self):
        """Richtet globale Hotkeys ein (Toggle und/oder Hold-Mode)."""
        try:
            from pynput import keyboard

            # Bindings sammeln: (hotkey_str, mode)
            bindings: list[tuple[str, str]] = []
            if self.toggle_hotkey:
                bindings.append((self.toggle_hotkey, "toggle"))
            if self.hold_hotkey:
                bindings.append((self.hold_hotkey, "hold"))

            if not bindings:
                logger.warning("Keine Hotkeys konfiguriert")
                return

            # Alle Hotkeys parsen
            parsed_hotkeys: list[tuple[set, str, str]] = []  # (keys, mode, source_id)
            for hotkey_str, mode in bindings:
                hotkey_keys = self._parse_hotkey_string(hotkey_str, keyboard)
                if not hotkey_keys:
                    logger.error(f"Ungültiger Hotkey: {hotkey_str}")
                    continue
                source_id = f"pynput:{mode}:{hotkey_str}"
                parsed_hotkeys.append((hotkey_keys, mode, source_id))

            if not parsed_hotkeys:
                logger.error("Keine gültigen Hotkeys konfiguriert")
                return

            # Aktuell gedrückte Tasten mit Zeitstempel (für Stale-Detection)
            # Format: {normalized_key: timestamp}
            current_keys: dict = {}

            # Hold-Mode State wird über self._hold_state verwaltet

            def normalize_key(key):
                """Normalisiert Key zu vergleichbarer Form."""
                # Modifier: ctrl_l/ctrl_r -> ctrl, etc.
                if hasattr(key, "name"):
                    name = key.name
                    if name in ("ctrl_l", "ctrl_r"):
                        return keyboard.Key.ctrl
                    if name in ("alt_l", "alt_r", "alt_gr"):
                        return keyboard.Key.alt
                    if name in ("shift_l", "shift_r"):
                        return keyboard.Key.shift
                    if name in ("cmd_l", "cmd_r"):
                        return keyboard.Key.cmd

                # Buchstaben: VK-Code oder char -> lowercase KeyCode
                if hasattr(key, "vk") and key.vk in _VK_TO_CHAR:
                    return keyboard.KeyCode.from_char(_VK_TO_CHAR[key.vk])
                if hasattr(key, "char") and key.char:
                    return keyboard.KeyCode.from_char(key.char.lower())

                return key

            def cleanup_stale_keys(now: float):
                """Entfernt Keys die länger als Timeout gedrückt sind (missed releases)."""
                stale = [k for k, t in current_keys.items()
                         if now - t > _KEY_STALE_TIMEOUT_SEC]
                for k in stale:
                    del current_keys[k]
                    logger.debug(f"Stale Key entfernt: {k}")

            def on_press(key):
                now = time.monotonic()
                normalized = normalize_key(key)

                # Stale Keys aufräumen
                cleanup_stale_keys(now)

                # Key mit Zeitstempel speichern
                current_keys[normalized] = now

                # Aktive Keys
                active_keys = set(current_keys.keys())

                # Jeden Hotkey prüfen
                for hotkey_keys, mode, source_id in parsed_hotkeys:
                    if hotkey_keys.issubset(active_keys):
                        # Zusätzliche Prüfung: Der gerade gedrückte Key muss Teil des Hotkeys sein
                        if normalized in hotkey_keys:
                            # Debouncing: Verhindere Doppel-Trigger
                            if now - self._last_hotkey_time >= _HOTKEY_DEBOUNCE_SEC:
                                self._last_hotkey_time = now
                                logger.debug(f"Hotkey ausgelöst: {hotkey_keys} (mode: {mode})")

                                if mode == "hold":
                                    # Hold-Mode: Recording starten, bleibt aktiv bis Release
                                    if self.state == AppState.LOADING and self._is_prewarm_loading:
                                        logger.debug("Hold-Hotkey ignoriert: Pre-Warm noch nicht abgeschlossen")
                                    elif self._hold_state.should_start(source_id):
                                        self._start_recording_from_hold(source_id)
                                else:
                                    # Toggle-Mode: Keys leeren und Toggle-Action
                                    current_keys.clear()
                                    self._on_hotkey_press()

            def on_release(key):
                normalized = normalize_key(key)
                current_keys.pop(normalized, None)

                # Aktive Keys nach Release
                active_keys = set(current_keys.keys())

                # Jeden Hold-Hotkey prüfen
                for hotkey_keys, mode, source_id in parsed_hotkeys:
                    if mode == "hold" and self._hold_state.is_active(source_id):
                        if not hotkey_keys.issubset(active_keys):
                            # Mindestens eine Hotkey-Taste wurde losgelassen
                            logger.debug(f"Hotkey losgelassen: {normalized}")
                            if self._hold_state.should_stop(source_id):
                                self._stop_recording_from_hotkey()

            listener = keyboard.Listener(
                on_press=on_press, on_release=on_release
            )
            listener.start()
            self._hotkey_listeners.append(listener)

            # Logging
            for hotkey_str, mode in bindings:
                logger.info(f"Hotkey registriert: {hotkey_str} ({mode.capitalize()}-Mode)")

        except ImportError:
            logger.error("pynput nicht installiert")
        except Exception as e:
            logger.error(f"Hotkey-Fehler: {e}")

    @staticmethod
    def _parse_hotkey_string(hotkey_str: str, keyboard) -> set:
        """Parst Hotkey-String zu Set von pynput Keys."""
        parts = [p.strip().lower() for p in hotkey_str.split("+")]
        hotkey_keys = set()

        for part in parts:
            if part in ("ctrl", "control"):
                hotkey_keys.add(keyboard.Key.ctrl)
            elif part in ("alt", "option"):
                hotkey_keys.add(keyboard.Key.alt)
            elif part in ("shift",):
                hotkey_keys.add(keyboard.Key.shift)
            elif part in ("cmd", "command", "win"):
                hotkey_keys.add(keyboard.Key.cmd)
            elif part.startswith("f") and part[1:].isdigit():
                # F-Tasten: f1-f24
                fn = int(part[1:])
                if 1 <= fn <= 24:
                    try:
                        hotkey_keys.add(getattr(keyboard.Key, part))
                    except AttributeError:
                        logger.warning(f"F-Taste nicht unterstützt: {part}")
                else:
                    logger.warning(f"Ungültige F-Taste: {part}")
            elif len(part) == 1:
                hotkey_keys.add(keyboard.KeyCode.from_char(part))
            else:
                logger.warning(f"Unbekannte Taste ignoriert: {part}")

        return hotkey_keys

    def _setup_tray(self):
        """Richtet Tray-Icon ein."""
        if not _load_tray_dependencies():
            logger.warning("Tray-Icon deaktiviert (pystray/Pillow nicht verfügbar)")
            return

        icon = self._create_icon(self.COLORS[AppState.IDLE])

        # Hotkey-Info für Menü
        hotkey_items = []
        if self.toggle_hotkey:
            hotkey_items.append(f"Toggle: {self.toggle_hotkey}")
        if self.hold_hotkey:
            hotkey_items.append(f"Hold: {self.hold_hotkey}")
        hotkey_text = ", ".join(hotkey_items) if hotkey_items else "Keiner"

        menu = pystray.Menu(
            pystray.MenuItem("PulseScribe", None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(f"Hotkeys: {hotkey_text}", None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Settings...", self._show_settings),
            pystray.MenuItem("Reload Settings", self._reload_settings),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Beenden", self._quit),
        )

        self._tray = pystray.Icon("pulsescribe", icon, "PulseScribe - Bereit", menu)

    def _quit(self):
        """Beendet den Daemon."""
        logger.info("Beende PulseScribe...")

        # Stop-Signal fuer Hauptschleife
        self._stop_event.set()

        # FileWatcher stoppen
        self._stop_env_watcher()

        # Warm-Stream stoppen
        self._stop_warm_stream()

        if self._overlay:
            self._overlay.stop()

        for listener in self._hotkey_listeners:
            try:
                listener.stop()
            except Exception:
                pass
        self._hotkey_listeners.clear()

        if self._tray:
            self._tray.stop()

    def _show_settings(self):
        """Öffnet das Settings-Fenster in einem separaten Prozess.

        Qt-Widgets müssen im Main-Thread laufen. Da pystray-Callbacks in einem
        Thread-Pool ausgeführt werden, starten wir das Settings-Fenster als
        separaten Prozess, um Threading-Probleme zu vermeiden.

        Bei gebündelter App (PyInstaller): Ruft sich selbst mit --settings auf.
        Bei Entwicklung: Startet Python mit ui/settings_windows.py.

        Fallback: Wenn PySide6 nicht verfügbar ist, wird die .env Datei
        im Standard-Editor geöffnet.
        """
        import subprocess

        try:
            # PyInstaller Bundle: sich selbst mit --settings aufrufen
            if getattr(sys, 'frozen', False):
                process = subprocess.Popen(
                    [sys.executable, "--settings"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0,
                )

                # Kurz warten und prüfen ob Prozess sofort stirbt
                import time
                time.sleep(0.5)
                if process.poll() is not None:
                    _, stderr = process.communicate(timeout=1)
                    error_msg = stderr.decode("utf-8", errors="replace").strip()
                    logger.error(f"Settings-Fenster fehlgeschlagen: {error_msg[:200]}")
                    self._open_env_in_editor()
                    return

                logger.info("Settings-Fenster gestartet (--settings)")
                return

            # Entwicklung: Python-Interpreter mit settings_windows.py starten
            # Bevorzuge venv-Python (dort ist PySide6 installiert)
            venv_python = PROJECT_ROOT / "venv" / "Scripts" / "python.exe"
            dotvenv_python = PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"
            if venv_python.exists():
                python_exe = str(venv_python)
            elif dotvenv_python.exists():
                python_exe = str(dotvenv_python)
            else:
                # Kein venv - prüfe ob PySide6 im aktuellen Python verfügbar ist
                import importlib.util
                if importlib.util.find_spec("PySide6") is None:
                    logger.warning("PySide6 nicht installiert - öffne .env im Editor")
                    self._open_env_in_editor()
                    return
                python_exe = sys.executable

            settings_script = PROJECT_ROOT / "ui" / "settings_windows.py"

            if not settings_script.exists():
                logger.error(f"Settings-Script nicht gefunden: {settings_script}")
                self._open_env_in_editor()
                return

            # PYTHONPATH erweitern damit utils.* imports funktionieren
            env = os.environ.copy()
            project_root = str(PROJECT_ROOT)
            existing_pythonpath = env.get("PYTHONPATH")
            if existing_pythonpath:
                env["PYTHONPATH"] = project_root + os.pathsep + existing_pythonpath
            else:
                env["PYTHONPATH"] = project_root

            process = subprocess.Popen(
                [python_exe, str(settings_script)],
                cwd=str(PROJECT_ROOT),
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0,
            )

            # Kurz warten und prüfen ob Prozess sofort stirbt (Import-Fehler etc.)
            import time
            time.sleep(0.5)
            if process.poll() is not None:
                _, stderr = process.communicate(timeout=1)
                error_msg = stderr.decode("utf-8", errors="replace").strip()
                logger.error(f"Settings-Fenster fehlgeschlagen: {error_msg[:200]}")
                self._open_env_in_editor()
                return

            logger.info("Settings-Fenster gestartet (separater Prozess)")

        except Exception as e:
            logger.error(f"Settings-Fenster konnte nicht geöffnet werden: {e}")
            self._open_env_in_editor()

    def _open_env_in_editor(self):
        """Öffnet die .env Datei im Standard-Editor als Fallback."""
        try:
            from utils.preferences import ENV_FILE
            env_path = ENV_FILE

            if env_path.exists():
                os.startfile(str(env_path))
                logger.info(f".env geöffnet im Editor: {env_path}")
            else:
                # .env existiert nicht - erstellen mit Beispiel-Inhalt
                env_path.parent.mkdir(parents=True, exist_ok=True)
                env_path.write_text(
                    "# PulseScribe Konfiguration\n"
                    "# Siehe CLAUDE.md für alle Optionen\n\n"
                    "PULSESCRIBE_MODE=deepgram\n"
                    "# DEEPGRAM_API_KEY=\n"
                    "# OPENAI_API_KEY=\n"
                )
                os.startfile(str(env_path))
                logger.info(f".env erstellt und geöffnet: {env_path}")

        except Exception as e:
            logger.error(f".env konnte nicht geöffnet werden: {e}")

    def _reload_settings(self):
        """Lädt Settings aus .env neu und wendet sie an.

        Wird automatisch aufgerufen wenn die .env Datei geändert wird (via FileWatcher)
        oder manuell über das Tray-Menü.
        """
        logger.info("Settings neu laden...")

        # WICHTIG: os.environ aktualisieren, damit alle Module die neuen Werte sehen
        # (z.B. refine/llm.py verwendet os.getenv() direkt)
        load_environment(override_existing=True)

        # .env auch als Dict lesen für explizite Instanzvariablen
        from utils.preferences import read_env_file
        env_values = read_env_file()

        # Mode aktualisieren
        new_mode = env_values.get("PULSESCRIBE_MODE", "deepgram")
        if new_mode != self.mode:
            old_mode = self.mode
            self.mode = new_mode

            # GESAMTEN Provider-Cache leeren (nicht nur alten Mode)
            # Wichtig weil auch LocalProvider.invalidate_runtime_config() nötig ist
            for provider in self._provider_cache.values():
                if hasattr(provider, "invalidate_runtime_config"):
                    provider.invalidate_runtime_config()
            self._provider_cache.clear()
            logger.info(f"Mode geändert: {old_mode} → {new_mode}")

            # Bei Wechsel zu local: Model preloaden
            if new_mode == "local":
                threading.Thread(
                    target=self._preload_local_model, daemon=True
                ).start()

        # Refine aktualisieren
        self.refine = env_values.get("PULSESCRIBE_REFINE", "").lower() == "true"
        self.refine_model = env_values.get("PULSESCRIBE_REFINE_MODEL")
        self.refine_provider = env_values.get("PULSESCRIBE_REFINE_PROVIDER")

        # Context aktualisieren
        self.context = env_values.get("PULSESCRIBE_CONTEXT")

        # Streaming aktualisieren (nur Deepgram unterstützt Streaming)
        streaming_val = env_values.get("PULSESCRIBE_STREAMING", "true")
        streaming_enabled = streaming_val.lower() != "false"
        self.streaming = streaming_enabled and self.mode == "deepgram"

        # Overlay aktualisieren (mit Start/Stop wenn nötig)
        overlay_val = env_values.get("PULSESCRIBE_OVERLAY", "true")
        new_overlay_enabled = overlay_val.lower() != "false"

        if new_overlay_enabled != self.overlay_enabled:
            self.overlay_enabled = new_overlay_enabled
            if new_overlay_enabled and self._overlay is None:
                # Overlay aktivieren
                logger.info("Overlay aktiviert")
                self._setup_overlay()
            elif not new_overlay_enabled and self._overlay is not None:
                # Overlay deaktivieren
                logger.info("Overlay deaktiviert")
                self._overlay.stop()
                self._overlay = None

        # Hotkeys aktualisieren (erfordert Listener-Neustart)
        new_toggle = env_values.get("PULSESCRIBE_TOGGLE_HOTKEY")
        new_hold = env_values.get("PULSESCRIBE_HOLD_HOTKEY")

        # Fallback: Wenn nichts konfiguriert, beide Defaults setzen (wie beim Startup)
        if not new_toggle and not new_hold:
            new_toggle = _DEFAULT_TOGGLE_HOTKEY
            new_hold = _DEFAULT_HOLD_HOTKEY

        if new_toggle != self.toggle_hotkey or new_hold != self.hold_hotkey:
            self.toggle_hotkey = new_toggle
            self.hold_hotkey = new_hold
            logger.info(f"Hotkeys geändert: toggle={new_toggle}, hold={new_hold}")
            # Listener neu starten
            self._restart_hotkey_listeners()

        logger.info("Settings erfolgreich neu geladen")

    def _preload_local_model(self):
        """Lädt Local-Model vor nach Settings-Änderung."""
        try:
            provider = self._get_provider("local")
            model, _ = self._get_transcription_config()
            if self._overlay:
                self._overlay.update_state("LOADING", f"Loading {model}...")
            if hasattr(provider, "preload"):
                logger.info(f"Preloading local model '{model}'...")
                provider.preload(model=model)
            if self._overlay and self.state == AppState.LOADING:
                self._set_state(AppState.IDLE)
        except Exception as e:
            logger.warning(f"Local-Model Preload fehlgeschlagen: {e}")
            if self.state == AppState.LOADING:
                self._set_state(AppState.IDLE)

    def _start_env_watcher(self):
        """Startet FileWatcher für .env Änderungen (Auto-Reload).

        Verwendet watchdog wenn verfügbar, ansonsten Polling-Fallback.
        Reagiert auf .env Änderungen und .reload Signal-Datei.
        """
        from utils.preferences import ENV_FILE

        self._reload_signal_file = ENV_FILE.parent / ".reload"
        watchdog_started = False

        try:
            from watchdog.observers import Observer
            from watchdog.events import FileSystemEventHandler

            class EnvFileHandler(FileSystemEventHandler):
                def __init__(handler_self, callback, signal_file):
                    handler_self.callback = callback
                    handler_self.signal_file = signal_file
                    handler_self._last_modified = 0.0

                def on_modified(handler_self, event):
                    # .env oder .reload Datei beachten
                    if not (event.src_path.endswith(".env") or
                            event.src_path.endswith(".reload")):
                        return
                    # Debounce: Ignoriere Events < 1s nach letztem
                    now = time.time()
                    if now - handler_self._last_modified > 1.0:
                        handler_self._last_modified = now
                        logger.debug(f"Settings-Änderung erkannt: {event.src_path}")
                        handler_self.callback()
                        # Signal-Datei löschen nach Verarbeitung
                        if handler_self.signal_file.exists():
                            try:
                                handler_self.signal_file.unlink()
                            except Exception:
                                pass

                def on_created(handler_self, event):
                    # Auch neue .reload Dateien beachten
                    if event.src_path.endswith(".reload"):
                        handler_self.on_modified(event)

            handler = EnvFileHandler(self._reload_settings, self._reload_signal_file)
            self._env_observer = Observer()
            self._env_observer.schedule(
                handler, str(ENV_FILE.parent), recursive=False
            )
            self._env_observer.start()
            logger.info(f"FileWatcher gestartet für {ENV_FILE.parent}")
            watchdog_started = True

        except ImportError:
            logger.debug("watchdog nicht installiert - verwende Polling-Fallback")
            self._env_observer = None
        except Exception as e:
            logger.warning(f"FileWatcher konnte nicht gestartet werden: {e}")
            self._env_observer = None

        # Polling-Fallback wenn watchdog nicht funktioniert
        if not watchdog_started:
            self._start_reload_polling()

    def _stop_env_watcher(self):
        """Stoppt den FileWatcher und Polling."""
        # FileWatcher stoppen
        if hasattr(self, "_env_observer") and self._env_observer is not None:
            try:
                self._env_observer.stop()
                self._env_observer.join(timeout=1.0)
                logger.debug("FileWatcher gestoppt")
            except Exception as e:
                logger.debug(f"FileWatcher Stop-Fehler: {e}")
            self._env_observer = None

        # Polling stoppen
        if hasattr(self, "_reload_polling_timer") and self._reload_polling_timer is not None:
            self._reload_polling_timer.cancel()
            self._reload_polling_timer = None

    def _start_reload_polling(self):
        """Startet Polling für .reload Signal-Datei (Fallback wenn watchdog nicht verfügbar)."""
        if self._reload_signal_file is None:
            return

        def poll_for_reload():
            if self._stop_event.is_set():
                return

            try:
                if self._reload_signal_file and self._reload_signal_file.exists():
                    logger.debug("Reload-Signal erkannt (Polling)")
                    self._reload_signal_file.unlink()
                    self._reload_settings()
            except Exception as e:
                logger.debug(f"Polling-Fehler: {e}")

            # Nächsten Poll planen (alle 2 Sekunden)
            if not self._stop_event.is_set():
                self._reload_polling_timer = threading.Timer(2.0, poll_for_reload)
                self._reload_polling_timer.daemon = True
                self._reload_polling_timer.start()

        # Ersten Poll starten
        self._reload_polling_timer = threading.Timer(2.0, poll_for_reload)
        self._reload_polling_timer.daemon = True
        self._reload_polling_timer.start()
        logger.info("Polling-Fallback gestartet für Settings-Reload")

    def _restart_hotkey_listeners(self):
        """Startet Hotkey-Listener mit neuen Einstellungen neu."""
        # Alte Listener stoppen
        for listener in self._hotkey_listeners:
            try:
                listener.stop()
            except Exception:
                pass
        self._hotkey_listeners.clear()

        # Neue Listener starten
        self._setup_hotkey()

    def _setup_overlay(self):
        """Richtet Overlay ein (läuft in separatem Thread)."""
        if not self.overlay_enabled:
            return

        if not _load_overlay():
            logger.warning("Overlay deaktiviert (Modul nicht verfügbar)")
            return

        try:
            # Overlay mit INTERIM_FILE für Interim-Text Polling
            self._overlay = WindowsOverlayController(interim_file=INTERIM_FILE)
            threading.Thread(target=self._overlay.run, daemon=True).start()
            logger.info("Overlay gestartet")
        except Exception as e:
            logger.warning(f"Overlay konnte nicht gestartet werden: {e}")
            self._overlay = None

    def _prewarm_imports(self):
        """Lädt teure Imports und erkennt Audio-Device im Hintergrund.

        Reduziert Latenz beim ersten Hotkey-Drücken um ~1.5-2s.
        Analog zu macOS _preload_local_model_async().
        """
        start = time.perf_counter()

        try:
            # Phase 1: Core-Libraries (für Streaming und REST)
            import numpy  # noqa: F401 - ~300ms
            import sounddevice  # noqa: F401 - ~100ms

            # Phase 2: Streaming-Dependencies (nur wenn Streaming aktiv)
            if self.streaming:
                from providers.deepgram_stream import deepgram_stream_core  # noqa: F401
                import httpx  # noqa: F401
                import websockets  # noqa: F401

                # Deepgram SDK-Klassen (werden in deepgram_stream_core benötigt)
                from deepgram.core.events import EventType  # noqa: F401
                from deepgram.extensions.types.sockets import ListenV1ControlMessage  # noqa: F401

                # Event-Loop vorab erstellen (spart ~50-100ms beim ersten Recording)
                import asyncio

                self._event_loop = asyncio.new_event_loop()

            # Phase 2b: UI-Imports (optional, beschleunigt _setup_overlay/tray)
            try:
                import pystray  # noqa: F401
                from PIL import Image  # noqa: F401

                if self.overlay_enabled:
                    from ui.overlay_windows import (
                        WindowsOverlayController as _WOC,  # noqa: F401
                    )
            except ImportError:
                pass  # Optional, nicht kritisch

            imports_ms = (time.perf_counter() - start) * 1000

            # Phase 3: Audio-Device erkennen (~250-500ms auf Windows)
            # get_input_device() cached das Ergebnis in config._cached_input_device
            device_start = time.perf_counter()
            device_idx, sample_rate = get_input_device()

            # Phase 4: Warm-Stream starten (für alle Modi!)
            # Der Warm-Stream bleibt offen und ermöglicht instant-start Recording
            # Auch REST-Modi (Groq, OpenAI, Local) profitieren vom Warm-Stream
            self._start_warm_stream()

            device_ms = (time.perf_counter() - device_start) * 1000

            # Phase 5: DNS-Prefetch für Deepgram WebSocket (spart ~50-200ms)
            if self.streaming:
                try:
                    import socket
                    socket.getaddrinfo("api.deepgram.com", 443)
                except Exception:
                    pass  # Ignorieren wenn es fehlschlägt

            # Phase 6: Local-Model Preload (nur im local mode)
            # Lädt faster-whisper Modell vorab → erste Transkription ohne Delay
            preload_ms = 0.0
            if self.mode == "local":
                try:
                    preload_start = time.perf_counter()
                    provider = self._get_provider("local")
                    model, _language = self._get_transcription_config()
                    if self._overlay:
                        self._overlay.update_state("LOADING", f"Loading {model}...")
                    if hasattr(provider, "preload"):
                        provider.preload(model=model)
                    preload_ms = (time.perf_counter() - preload_start) * 1000
                    # Runtime-Info für Logging (Device, Compute-Type)
                    runtime_info = ""
                    if hasattr(provider, "get_runtime_info"):
                        info = provider.get_runtime_info()
                        device = (info.get("device") or "unknown").upper()
                        compute = info.get("compute_type")
                        runtime_info = f", Device: {device}"
                        if compute:
                            runtime_info += f", Compute: {compute}"
                    logger.info(
                        f"Local-Modell '{model}' vorab geladen ({preload_ms:.0f}ms{runtime_info})"
                    )
                except Exception as e:
                    logger.warning(f"Local-Modell Preload fehlgeschlagen: {e}")

            total_ms = (time.perf_counter() - start) * 1000
            mode_desc = f"{self.mode} ({'Streaming' if self.streaming else 'REST'})"
            preload_info = f", Preload={preload_ms:.0f}ms" if self.mode == "local" else ""
            logger.info(
                f"Pre-Warm abgeschlossen ({total_ms:.0f}ms, {mode_desc}, Warm-Stream): "
                f"Imports={imports_ms:.0f}ms, Device={device_ms:.0f}ms{preload_info} "
                f"(idx={device_idx}, {sample_rate}Hz)"
            )
        except Exception as e:
            logger.debug(f"Pre-Warm fehlgeschlagen: {e}", exc_info=True)
        finally:
            self._prewarm_complete.set()

    def _show_settings_if_needed(self):
        """Zeigt Settings-Fenster beim ersten Start oder wenn aktiviert.

        Analog zu macOS _show_welcome_if_needed(): Öffnet Settings automatisch
        wenn Onboarding nicht abgeschlossen ist oder "Show at startup" aktiviert ist.
        """
        from utils.preferences import (
            is_onboarding_complete,
            get_show_welcome_on_startup,
        )

        # Logik analog zu macOS (dort mit Wizard, hier direkt Settings):
        # 1. Erster Start (Onboarding nicht complete) → Settings öffnen
        # 2. Sonst: Nur wenn "Show at startup" aktiviert → Settings öffnen
        if not is_onboarding_complete():
            logger.info("Erster Start erkannt - Settings öffnen")
            self._show_settings()
        elif get_show_welcome_on_startup():
            logger.info("'Show at startup' aktiviert - Settings öffnen")
            self._show_settings()

    def run(self):
        """Startet den Daemon."""
        hotkey_info = []
        if self.toggle_hotkey:
            hotkey_info.append(f"Toggle: {self.toggle_hotkey}")
        if self.hold_hotkey:
            hotkey_info.append(f"Hold: {self.hold_hotkey}")
        hotkey_text = ", ".join(hotkey_info) if hotkey_info else "Keiner"
        print(f"PulseScribe Windows gestartet (Hotkeys: {hotkey_text})")
        print("Drücke Ctrl+C oder nutze Tray-Menü zum Beenden")

        # Hotkey ZUERST registrieren - User kann sofort starten
        # (Device-Erkennung läuft parallel im Pre-Warm)
        self._setup_hotkey()

        # Overlay FRÜH starten, damit LOADING angezeigt werden kann
        self._setup_overlay()

        # LOADING-State während Pre-Warm anzeigen (für alle Modi mit Warm-Stream)
        self._is_prewarm_loading = True
        self._set_state(AppState.LOADING)

        # Pre-Warm: Teure Imports + Warm-Stream starten
        def _prewarm_and_ready():
            self._prewarm_imports()
            # Nach Pre-Warm: Zurück zu IDLE (Ready)
            self._is_prewarm_loading = False
            if self.state == AppState.LOADING:
                self._set_state(AppState.IDLE)
                self._play_sound("ready")  # Signal: System bereit

        threading.Thread(
            target=_prewarm_and_ready, daemon=True, name="PreWarm"
        ).start()

        self._setup_tray()

        # FileWatcher für Auto-Reload bei .env Änderungen
        self._start_env_watcher()

        # Settings-Fenster beim ersten Start oder wenn aktiviert öffnen (wie macOS)
        self._show_settings_if_needed()

        # Ctrl+C Handler: Signal wird an _quit weitergeleitet (nur einmal)
        def signal_handler(sig, frame):
            if not self._stop_event.is_set():
                print("\nCtrl+C erkannt, beende...")
                self._quit()

        signal.signal(signal.SIGINT, signal_handler)

        if self._tray:
            # Tray-Icon in Hintergrund-Thread, damit Hauptthread Ctrl+C empfängt
            self._tray.run_detached()

        # Hauptthread wartet auf Stop-Signal oder Ctrl+C
        try:
            while not self._stop_event.is_set():
                time.sleep(0.5)
        except KeyboardInterrupt:
            self._quit()

        print("Beendet.")


def main():
    parser = argparse.ArgumentParser(description="PulseScribe Windows Daemon")
    parser.add_argument(
        "--toggle-hotkey",
        default=None,
        help="Toggle-Hotkey (druecken-sprechen-druecken)",
    )
    parser.add_argument(
        "--hold-hotkey",
        default=None,
        help="Hold-Hotkey (halten-sprechen-loslassen)",
    )
    parser.add_argument(
        "--mode",
        choices=["deepgram", "groq", "openai", "local"],
        default=os.getenv("PULSESCRIBE_MODE", "deepgram"),
        help="Transkriptions-Modus (deepgram, groq, openai, local)",
    )
    parser.add_argument(
        "--no-paste",
        action="store_true",
        help="Deaktiviert Auto-Paste (nur Clipboard)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Aktiviert Debug-Logging",
    )
    parser.add_argument(
        "--refine",
        action="store_true",
        help="LLM-Nachbearbeitung aktivieren",
    )
    parser.add_argument(
        "--refine-model",
        default=None,
        help="Modell für LLM-Nachbearbeitung",
    )
    parser.add_argument(
        "--refine-provider",
        choices=["groq", "openai", "openrouter"],
        default=None,
        help="LLM-Provider (groq, openai, openrouter)",
    )
    parser.add_argument(
        "--context",
        choices=["email", "chat", "code", "default"],
        default=None,
        help="Kontext für Nachbearbeitung",
    )
    parser.add_argument(
        "--no-streaming",
        action="store_true",
        help="REST API statt WebSocket Streaming",
    )
    parser.add_argument(
        "--no-overlay",
        action="store_true",
        help="Overlay deaktivieren",
    )
    parser.add_argument(
        "--settings",
        action="store_true",
        help="Settings-Fenster öffnen (statt Daemon starten)",
    )

    args = parser.parse_args()

    # --settings: Settings-Fenster öffnen und beenden (kein Daemon)
    if args.settings:
        try:
            from PySide6.QtWidgets import QApplication
            from ui.settings_windows import SettingsWindow

            app = QApplication(sys.argv)
            window = SettingsWindow()
            window.show()
            sys.exit(app.exec())
        except ImportError as e:
            print(f"Settings-Fenster nicht verfügbar: {e}", file=sys.stderr)
            sys.exit(1)

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    # Refine: CLI > ENV > Default (False)
    effective_refine = (
        args.refine or os.getenv("PULSESCRIBE_REFINE", "").lower() == "true"
    )
    effective_refine_model = args.refine_model or os.getenv("PULSESCRIBE_REFINE_MODEL")
    effective_refine_provider = args.refine_provider or os.getenv(
        "PULSESCRIBE_REFINE_PROVIDER"
    )
    effective_context = args.context or os.getenv("PULSESCRIBE_CONTEXT")
    effective_mode = args.mode

    # Streaming: Default True, kann via --no-streaming oder ENV deaktiviert werden
    effective_streaming = (
        not args.no_streaming
        and os.getenv("PULSESCRIBE_STREAMING", "true").lower() != "false"
    )

    # Nur Deepgram unterstützt aktuell Streaming im Daemon
    if effective_mode != "deepgram" and effective_streaming:
        logging.info(
            f"Modus '{effective_mode}' unterstützt kein Streaming im Daemon -> Fallback auf REST"
        )
        effective_streaming = False

    # Overlay: Default True, kann via --no-overlay oder ENV deaktiviert werden
    effective_overlay = (
        not args.no_overlay
        and os.getenv("PULSESCRIBE_OVERLAY", "true").lower() != "false"
    )

    # Hotkeys: CLI > ENV > Default
    # Konsistent mit macOS: PULSESCRIBE_TOGGLE_HOTKEY und PULSESCRIBE_HOLD_HOTKEY
    effective_toggle_hotkey = (
        args.toggle_hotkey or os.getenv("PULSESCRIBE_TOGGLE_HOTKEY")
    )
    effective_hold_hotkey = (
        args.hold_hotkey or os.getenv("PULSESCRIBE_HOLD_HOTKEY")
    )

    # Fallback: Wenn nichts konfiguriert, beide Defaults setzen
    if not effective_toggle_hotkey and not effective_hold_hotkey:
        effective_toggle_hotkey = _DEFAULT_TOGGLE_HOTKEY
        effective_hold_hotkey = _DEFAULT_HOLD_HOTKEY

    daemon = PulseScribeWindows(
        toggle_hotkey=effective_toggle_hotkey,
        hold_hotkey=effective_hold_hotkey,
        mode=effective_mode,
        auto_paste=not args.no_paste,
        refine=effective_refine,
        refine_model=effective_refine_model,
        refine_provider=effective_refine_provider,
        context=effective_context,
        streaming=effective_streaming,
        overlay=effective_overlay,
    )

    try:
        daemon.run()
    except KeyboardInterrupt:
        print("\nBeendet.")


if __name__ == "__main__":
    main()
