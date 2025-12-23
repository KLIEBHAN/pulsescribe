"""
PulseScribe Windows Daemon.

Minimaler Windows Entry-Point für Spracheingabe mit:
- Tray-Icon (pystray)
- Globaler Hotkey (pynput)
- Sound-Feedback (Windows System-Sounds)
- Deepgram REST-Transkription

Usage:
    python pulsescribe_windows.py
    python pulsescribe_windows.py --hotkey "ctrl+alt+r"
"""

import sys

if sys.platform != "win32":
    print("Error: This script is Windows-only", file=sys.stderr)
    sys.exit(1)

import argparse
import logging
import os
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
from utils.hotkey import paste_transcript
from whisper_platform import get_clipboard, get_sound_player
from config import INTERIM_FILE, get_input_device

# Lazy imports für optionale Features
pystray = None
PIL_Image = None
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

# VAD Threshold: Audio-Level ab dem Sprache erkannt wird
# REST-Modus verwendet Peak (max), Streaming verwendet RMS (niedriger)
_VAD_THRESHOLD_PEAK = 0.01  # Für REST-Modus (float32 peak)
_VAD_THRESHOLD_RMS = 0.003  # Für Streaming-Modus (RMS/INT16_MAX)


def _load_tray_dependencies():
    """Lädt pystray und Pillow (lazy)."""
    global pystray, PIL_Image
    try:
        import pystray as _pystray
        from PIL import Image as _Image

        pystray = _pystray
        PIL_Image = _Image
        return True
    except ImportError as e:
        logger.warning(f"Tray-Icon nicht verfügbar: {e}")
        return False


class PulseScribeWindows:
    """Windows-Daemon mit Tray-Icon, Hotkey und Deepgram-Streaming."""

    # Tray-Icon Farben (RGB)
    COLORS = {
        AppState.IDLE: (128, 128, 128),  # Grau
        AppState.LISTENING: (255, 165, 0),  # Orange
        AppState.RECORDING: (255, 0, 0),  # Rot
        AppState.TRANSCRIBING: (255, 255, 0),  # Gelb
        AppState.REFINING: (0, 255, 255),  # Cyan
        AppState.DONE: (0, 255, 0),  # Grün
        AppState.ERROR: (255, 0, 0),  # Rot
    }

    def __init__(
        self,
        hotkey: str = "ctrl+alt+r",
        auto_paste: bool = True,
        refine: bool = False,
        refine_model: str | None = None,
        refine_provider: str | None = None,
        context: str | None = None,
        streaming: bool = True,
        overlay: bool = True,
    ):
        self.hotkey_str = hotkey
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

        # Components
        self._tray = None
        self._hotkey_listener = None
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

        mode = "Streaming" if streaming else "REST"
        logger.info(
            f"PulseScribeWindows initialisiert (Hotkey: {hotkey}, Mode: {mode}, Refine: {refine}, Overlay: {overlay})"
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
        if self._tray is None or PIL_Image is None:
            return

        color = self.COLORS.get(self.state, (128, 128, 128))
        icon = self._create_icon(color)
        self._tray.icon = icon

        # Tooltip aktualisieren
        state_text = {
            AppState.IDLE: "Bereit",
            AppState.LISTENING: "Warte auf Sprache...",
            AppState.RECORDING: "Aufnahme...",
            AppState.TRANSCRIBING: "Transkribiere...",
            AppState.REFINING: "Verfeinere...",
            AppState.DONE: "Fertig",
            AppState.ERROR: "Fehler",
        }
        self._tray.title = f"PulseScribe - {state_text.get(self.state, 'Unbekannt')}"

    def _create_icon(self, color: tuple) -> "PIL_Image.Image":
        """Erstellt ein einfaches farbiges Icon."""
        size = 64
        image = PIL_Image.new("RGB", (size, size), color)
        return image

    def _play_sound(self, sound_type: str):
        """Spielt System-Sound ab."""
        try:
            get_sound_player().play(sound_type)
        except Exception as e:
            logger.debug(f"Sound-Fehler: {e}")

    def _on_hotkey_press(self):
        """Callback wenn Hotkey gedrückt wird."""
        if self.state == AppState.IDLE:
            self._start_recording()
        elif self.state in (AppState.LOADING, AppState.LISTENING, AppState.RECORDING):
            self._stop_recording()

    def _start_recording(self):
        """Startet Aufnahme (Streaming oder REST)."""
        logger.info(f"Starte Aufnahme ({'Streaming' if self.streaming else 'REST'})...")

        # Recording-Stop-Event zurücksetzen
        self._recording_stop_event.clear()

        if self.streaming:
            # Streaming: LOADING sofort setzen für UI-Feedback
            self._set_state(AppState.LOADING)

            # Kurz auf Pre-Warm warten (non-blocking check, dann kurzer wait)
            # Verhindert langsamen ersten Start wenn User sofort drückt
            if not self._prewarm_complete.is_set():
                logger.debug("Warte auf Pre-Warm...")
                if not self._prewarm_complete.wait(timeout=1.0):
                    logger.warning("Pre-Warm Timeout - starte trotzdem")

            self._recording_thread = threading.Thread(
                target=self._streaming_worker, daemon=True
            )
        else:
            # REST: Sound hier spielen, dann Recording starten
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
                        original = transcript
                        try:
                            from refine.llm import refine_transcript

                            transcript = refine_transcript(
                                transcript,
                                model=self.refine_model,
                                provider=self.refine_provider,
                                context=self.context,
                            )
                            if not transcript or not transcript.strip():
                                logger.warning(
                                    "Refine gab leeren String zurück, verwende Original"
                                )
                                transcript = original
                        except Exception as e:
                            logger.warning(
                                f"Refine fehlgeschlagen, verwende Original: {e}"
                            )
                            transcript = original

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

    def _transcribe_rest(self):
        """Transkribiert aufgenommenes Audio via REST API."""
        try:
            import numpy as np
            import soundfile as sf
            import tempfile
            from pathlib import Path

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

            # Audio in temporäre Datei schreiben
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                temp_path = Path(f.name)

            try:
                sf.write(temp_path, audio_data, sample_rate)

                # Deepgram REST API nutzen
                from providers.deepgram import DeepgramProvider

                provider = DeepgramProvider()
                transcript = provider.transcribe(
                    audio_path=temp_path,
                    language=os.getenv("PULSESCRIBE_LANGUAGE", "de"),
                )

                if transcript:
                    # LLM-Nachbearbeitung (optional)
                    if self.refine:
                        self._set_state(AppState.REFINING)
                        original = transcript
                        try:
                            from refine.llm import refine_transcript

                            transcript = refine_transcript(
                                transcript,
                                model=self.refine_model,
                                provider=self.refine_provider,
                                context=self.context,
                            )
                            # Fallback auf Original wenn LLM leeren String zurückgibt
                            if not transcript or not transcript.strip():
                                logger.warning(
                                    "Refine gab leeren String zurück, verwende Original"
                                )
                                transcript = original
                        except Exception as e:
                            logger.warning(
                                f"Refine fehlgeschlagen, verwende Original: {e}"
                            )
                            transcript = original

                    self._handle_result(transcript)
                else:
                    logger.warning("Leeres Transkript")
                    self._set_state(AppState.IDLE)

            finally:
                # Temporäre Datei löschen
                if temp_path.exists():
                    temp_path.unlink()

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

    def _handle_result(self, transcript: str):
        """Verarbeitet Transkriptions-Ergebnis."""
        logger.info(f"Transkript: {transcript[:50]}...")
        self._set_state(AppState.DONE)
        self._play_sound("done")

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
        """Richtet globalen Hotkey ein."""
        try:
            from pynput import keyboard

            # Parse Hotkey-String zu Set von erwarteten Keys
            hotkey_keys = self._parse_hotkey_string(self.hotkey_str, keyboard)
            if not hotkey_keys:
                logger.error(f"Ungültiger Hotkey: {self.hotkey_str}")
                return

            # Aktuell gedrückte Tasten (normalisiert)
            current_keys: set = set()

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

            def on_press(key):
                current_keys.add(normalize_key(key))

                # Hotkey erkannt?
                if hotkey_keys.issubset(current_keys):
                    # Debouncing: Verhindere Doppel-Trigger
                    now = time.monotonic()
                    if now - self._last_hotkey_time >= _HOTKEY_DEBOUNCE_SEC:
                        self._last_hotkey_time = now
                        self._on_hotkey_press()

            def on_release(key):
                current_keys.discard(normalize_key(key))

            self._hotkey_listener = keyboard.Listener(
                on_press=on_press, on_release=on_release
            )
            self._hotkey_listener.start()
            logger.info(f"Hotkey registriert: {self.hotkey_str}")

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

        menu = pystray.Menu(
            pystray.MenuItem("PulseScribe", None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(f"Hotkey: {self.hotkey_str}", None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Beenden", self._quit),
        )

        self._tray = pystray.Icon("pulsescribe", icon, "PulseScribe - Bereit", menu)

    def _quit(self):
        """Beendet den Daemon."""
        logger.info("Beende PulseScribe...")

        # Stop-Signal fuer Hauptschleife
        self._stop_event.set()

        if self._overlay:
            self._overlay.stop()

        if self._hotkey_listener:
            self._hotkey_listener.stop()

        if self._tray:
            self._tray.stop()

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

            # Phase 4: PortAudio "warm-up" - erster Stream-Start ist langsam
            # Öffne kurz einen Dummy-Stream um COM/WASAPI zu initialisieren
            try:
                dummy = sounddevice.InputStream(
                    device=device_idx,
                    samplerate=sample_rate,
                    channels=1,
                    blocksize=1024,
                )
                dummy.start()
                dummy.stop()
                dummy.close()
            except Exception:
                pass  # Ignorieren wenn es fehlschlägt

            device_ms = (time.perf_counter() - device_start) * 1000

            # Phase 5: DNS-Prefetch für Deepgram WebSocket (spart ~50-200ms)
            if self.streaming:
                try:
                    import socket
                    socket.getaddrinfo("api.deepgram.com", 443)
                except Exception:
                    pass  # Ignorieren wenn es fehlschlägt

            total_ms = (time.perf_counter() - start) * 1000
            mode = "Streaming" if self.streaming else "REST"
            logger.info(
                f"Pre-Warm abgeschlossen ({total_ms:.0f}ms, {mode}): "
                f"Imports={imports_ms:.0f}ms, Device={device_ms:.0f}ms "
                f"(idx={device_idx}, {sample_rate}Hz)"
            )
        except Exception as e:
            logger.debug(f"Pre-Warm fehlgeschlagen: {e}", exc_info=True)
        finally:
            self._prewarm_complete.set()

    def run(self):
        """Startet den Daemon."""
        print(f"PulseScribe Windows gestartet (Hotkey: {self.hotkey_str})")
        print("Drücke Ctrl+C oder nutze Tray-Menü zum Beenden")

        # Hotkey ZUERST registrieren - User kann sofort starten
        # (Device-Erkennung läuft parallel im Pre-Warm)
        self._setup_hotkey()

        # Pre-Warm: Teure Imports + Device-Erkennung im Hintergrund
        threading.Thread(
            target=self._prewarm_imports, daemon=True, name="PreWarm"
        ).start()

        self._setup_overlay()
        self._setup_tray()

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
        "--hotkey",
        default=os.getenv("PULSESCRIBE_HOTKEY", "ctrl+alt+r"),
        help="Globaler Hotkey (default: ctrl+alt+r)",
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

    args = parser.parse_args()

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

    # Streaming: Default True, kann via --no-streaming oder ENV deaktiviert werden
    effective_streaming = (
        not args.no_streaming
        and os.getenv("PULSESCRIBE_STREAMING", "true").lower() != "false"
    )

    # Overlay: Default True, kann via --no-overlay oder ENV deaktiviert werden
    effective_overlay = (
        not args.no_overlay
        and os.getenv("PULSESCRIBE_OVERLAY", "true").lower() != "false"
    )

    daemon = PulseScribeWindows(
        hotkey=args.hotkey,
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
