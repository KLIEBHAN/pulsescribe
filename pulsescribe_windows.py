"""
PulseScribe Windows Daemon.

Minimaler Windows Entry-Point für Spracheingabe mit:
- Tray-Icon (pystray)
- Globaler Hotkey (pynput)
- Sound-Feedback (winsound)
- Deepgram-Streaming Transkription

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
import threading
import time
import winsound
from pathlib import Path

# Projekt-Root zum Path hinzufügen
PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))

# Logging Setup (vor anderen Imports)
from utils.logging import setup_logging, get_logger

setup_logging(debug=os.getenv("PULSESCRIBE_DEBUG", "").lower() == "true")
logger = get_logger()

# Imports nach Logging-Setup
from utils.state import AppState
from utils.hotkey import paste_transcript
from whisper_platform import get_clipboard, get_sound_player

# Lazy imports für optionale Features
pystray = None
PIL_Image = None


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
        AppState.DONE: (0, 255, 0),  # Grün
        AppState.ERROR: (255, 0, 0),  # Rot
    }

    def __init__(self, hotkey: str = "ctrl+alt+r", auto_paste: bool = True):
        self.hotkey_str = hotkey
        self.auto_paste = auto_paste

        # State
        self._state = AppState.IDLE
        self._state_lock = threading.Lock()

        # Components
        self._tray = None
        self._hotkey_listener = None
        self._recording_thread = None
        self._stop_event = threading.Event()

        # Audio buffer für Streaming
        self._audio_buffer = []
        self._audio_lock = threading.Lock()

        logger.info(f"PulseScribeWindows initialisiert (Hotkey: {hotkey})")

    @property
    def state(self) -> AppState:
        with self._state_lock:
            return self._state

    def _set_state(self, state: AppState):
        """Setzt State und aktualisiert Tray-Icon."""
        with self._state_lock:
            old_state = self._state
            self._state = state

        if old_state != state:
            logger.info(f"State: {old_state.value} → {state.value}")
            self._update_tray_icon()

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
        sounds = {
            "ready": winsound.MB_OK,
            "stop": winsound.MB_ICONEXCLAMATION,
            "done": winsound.MB_ICONASTERISK,
            "error": winsound.MB_ICONHAND,
        }
        sound = sounds.get(sound_type)
        if sound:
            try:
                winsound.MessageBeep(sound)
            except Exception as e:
                logger.debug(f"Sound-Fehler: {e}")

    def _on_hotkey_press(self):
        """Callback wenn Hotkey gedrückt wird."""
        if self.state == AppState.IDLE:
            self._start_recording()
        elif self.state in (AppState.LISTENING, AppState.RECORDING):
            self._stop_recording()

    def _start_recording(self):
        """Startet Aufnahme."""
        logger.info("Starte Aufnahme...")
        self._set_state(AppState.LISTENING)
        self._play_sound("ready")

        # Stop-Event zurücksetzen
        self._stop_event.clear()

        # Recording-Thread starten
        self._recording_thread = threading.Thread(
            target=self._recording_loop, daemon=True
        )
        self._recording_thread.start()

    def _stop_recording(self):
        """Stoppt Aufnahme und startet Transkription."""
        logger.info("Stoppe Aufnahme...")
        self._play_sound("stop")

        # Signal zum Stoppen
        self._stop_event.set()

        # Auf Thread warten (mit Timeout)
        if self._recording_thread and self._recording_thread.is_alive():
            self._recording_thread.join(timeout=2.0)

        self._set_state(AppState.TRANSCRIBING)

        # Transkription in separatem Thread
        threading.Thread(target=self._transcribe, daemon=True).start()

    def _recording_loop(self):
        """Audio-Aufnahme Loop (läuft in separatem Thread)."""
        try:
            import sounddevice as sd
            import numpy as np

            sample_rate = 16000
            channels = 1
            chunk_duration = 0.1  # 100ms chunks

            with self._audio_lock:
                self._audio_buffer = []

            def audio_callback(indata, frames, time_info, status):
                if status:
                    logger.warning(f"Audio-Status: {status}")
                with self._audio_lock:
                    self._audio_buffer.append(indata.copy())

                # State auf RECORDING setzen wenn Audio erkannt
                if self.state == AppState.LISTENING:
                    # Einfache VAD: Prüfe ob Audio über Threshold
                    if np.abs(indata).max() > 0.01:
                        self._set_state(AppState.RECORDING)

            with sd.InputStream(
                samplerate=sample_rate,
                channels=channels,
                dtype="float32",
                callback=audio_callback,
                blocksize=int(sample_rate * chunk_duration),
            ):
                while not self._stop_event.is_set():
                    time.sleep(0.05)

        except ImportError:
            logger.error("sounddevice nicht installiert")
            self._set_state(AppState.ERROR)
            self._play_sound("error")
        except Exception as e:
            logger.error(f"Recording-Fehler: {e}")
            self._set_state(AppState.ERROR)
            self._play_sound("error")

    def _transcribe(self):
        """Transkribiert aufgenommenes Audio."""
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
                self._audio_buffer = []

            duration = len(audio_data) / 16000
            logger.info(f"Transkribiere {duration:.1f}s Audio...")

            # Audio in temporäre Datei schreiben
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                temp_path = Path(f.name)

            try:
                sf.write(temp_path, audio_data, 16000)

                # Deepgram REST API nutzen
                from providers.deepgram import DeepgramProvider

                provider = DeepgramProvider()
                transcript = provider.transcribe(
                    audio_path=temp_path,
                    language=os.getenv("PULSESCRIBE_LANGUAGE", "de"),
                )

                if transcript:
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
        except Exception as e:
            logger.error(f"Transkriptions-Fehler: {e}")
            self._set_state(AppState.ERROR)
            self._play_sound("error")

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
                logger.info("Text in Zwischenablage kopiert (Auto-Paste fehlgeschlagen)")
        else:
            get_clipboard().copy(transcript)
            logger.info("Text in Zwischenablage kopiert")

        # Nach kurzer Pause zurück zu IDLE
        time.sleep(1.0)
        self._set_state(AppState.IDLE)

    def _setup_hotkey(self):
        """Richtet globalen Hotkey ein."""
        try:
            from pynput import keyboard

            # Parse Hotkey-String
            parts = [p.strip().lower() for p in self.hotkey_str.split("+")]
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
                elif len(part) == 1:
                    hotkey_keys.add(keyboard.KeyCode.from_char(part))
                else:
                    logger.warning(f"Unbekannte Taste: {part}")

            current_keys = set()

            def on_press(key):
                current_keys.add(key)
                if hotkey_keys.issubset(current_keys):
                    self._on_hotkey_press()

            def on_release(key):
                current_keys.discard(key)

            self._hotkey_listener = keyboard.Listener(
                on_press=on_press, on_release=on_release
            )
            self._hotkey_listener.start()
            logger.info(f"Hotkey registriert: {self.hotkey_str}")

        except ImportError:
            logger.error("pynput nicht installiert")
        except Exception as e:
            logger.error(f"Hotkey-Fehler: {e}")

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

        if self._hotkey_listener:
            self._hotkey_listener.stop()

        if self._tray:
            self._tray.stop()

    def run(self):
        """Startet den Daemon."""
        print(f"PulseScribe Windows gestartet (Hotkey: {self.hotkey_str})")
        print("Drücke Ctrl+C oder nutze Tray-Menü zum Beenden")

        self._setup_hotkey()
        self._setup_tray()

        if self._tray:
            # Tray-Icon blockiert den Hauptthread
            self._tray.run()
        else:
            # Ohne Tray: Einfacher Event-Loop
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                self._quit()


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

    args = parser.parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    daemon = PulseScribeWindows(
        hotkey=args.hotkey,
        auto_paste=not args.no_paste,
    )

    try:
        daemon.run()
    except KeyboardInterrupt:
        print("\nBeendet.")


if __name__ == "__main__":
    main()
