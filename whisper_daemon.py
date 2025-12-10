#!/usr/bin/env python3
"""
whisper_daemon.py – Unified Daemon für whisper_go.

Konsolidiert in einem Prozess:
- Hotkey-Listener (QuickMacHotKey, keine Accessibility nötig)
- Mikrofon-Aufnahme + Deepgram Streaming (wie run_daemon_mode_streaming)
- Menübar-Status (NSStatusBar)
- Overlay mit Animationen (NSWindow)
- LLM-Nachbearbeitung (optional)
- Auto-Paste (pynput/Quartz)

Architektur:
- Main Thread: NSApplication Event-Loop (QuickMacHotKey, Menübar, Overlay)
- Worker Thread: _deepgram_stream_core() mit external_stop_event

Usage:
    python whisper_daemon.py              # Mit Defaults aus .env
    python whisper_daemon.py --hotkey f19 # Hotkey überschreiben
"""

import logging
import os
import queue
import sys
import tempfile
import threading
import time
from pathlib import Path

# --- Emergency Logging (Before everything else) ---
def emergency_log(msg: str):
    """Schreibt direkt in eine Datei im User-Home, falls Logging versagt."""
    try:
        debug_file = Path.home() / ".whisper_go" / "startup.log"
        debug_file.parent.mkdir(exist_ok=True)
        with open(debug_file, "a", encoding="utf-8") as f:
            timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
            f.write(f"[{timestamp}] {msg}\n")
    except Exception:
        pass

emergency_log("=== Booting Whisper Daemon ===")

try:
    from config import INTERIM_FILE, VAD_THRESHOLD
    from utils import setup_logging
    from config import DEFAULT_DEEPGRAM_MODEL
    from providers.deepgram_stream import deepgram_stream_core
    from providers import get_provider
    from refine.llm import refine_transcript
    from whisper_platform import get_sound_player
    from utils.state import AppState, DaemonMessage, MessageType
    from utils import parse_hotkey, paste_transcript
    from utils.permissions import check_microphone_permission, check_accessibility_permission
    from ui import MenuBarController, OverlayController
except Exception as e:
    emergency_log(f"CRITICAL IMPORT ERROR: {e}")
    sys.exit(1)

emergency_log("Imports successful")

# DEBOUNCE_INTERVAL defined locally as it is specific to hotkey daemon
DEBOUNCE_INTERVAL = 0.3
logger = logging.getLogger("whisper_go")


# =============================================================================
# WhisperDaemon: Hauptklasse
# =============================================================================


class WhisperDaemon:
    """
    Unified Daemon für whisper_go.
    
    Architektur:
        Main-Thread: Hotkey-Listener (QuickMacHotKey) + UI-Updates
        Worker-Thread: Deepgram-Streaming (async)
    
    State-Flow:
        idle → [Hotkey] → recording → [Hotkey] → transcribing → done/error → idle
    """

    def __init__(
        self,
        hotkey: str = "f19",
        language: str | None = None,
        model: str | None = None,
        refine: bool = False,
        refine_model: str | None = None,
        refine_provider: str | None = None,

        context: str | None = None,
        mode: str | None = None,
    ):
        self.hotkey = hotkey
        self.language = language
        self.model = model
        self.refine = refine
        self.refine_model = refine_model
        self.refine_provider = refine_provider
        self.context = context
        self.mode = mode

        # State
        self._recording = False
        self._toggle_lock = threading.Lock()
        self._last_hotkey_time = 0.0
        self._current_state = AppState.IDLE

        # Stop-Event für _deepgram_stream_core
        self._stop_event: threading.Event | None = None

        # Worker-Thread für Streaming
        self._worker_thread: threading.Thread | None = None

        # Result-Queue für Transkripte
        self._result_queue: queue.Queue[DaemonMessage | Exception] = queue.Queue()

        # NSTimer für Result-Polling und Interim-Polling
        self._result_timer = None
        self._interim_timer = None
        self._last_interim_mtime = 0.0

        # UI-Controller (werden in run() initialisiert)
        self._menubar: MenuBarController | None = None
        self._overlay: OverlayController | None = None

    def _update_state(self, state: AppState, text: str | None = None) -> None:
        """Aktualisiert State und benachrichtigt UI-Controller."""
        self._current_state = state
        logger.debug(
            f"State: {state}"
            + (f" text='{text[:20]}...'" if text else "")
        )

        # UI-Controller aktualisieren
        if self._menubar:
            self._menubar.update_state(state, text)
        if self._overlay:
            self._overlay.update_state(state, text)

    def _on_hotkey(self) -> None:
        """Callback bei Hotkey-Aktivierung."""
        # Keyboard-Auto-Repeat und schnelle Doppelklicks ignorieren
        now = time.time()
        if now - self._last_hotkey_time < DEBOUNCE_INTERVAL:
            logger.debug("Debounce: Event ignoriert")
            return
        self._last_hotkey_time = now

        # Parallele Ausführung verhindern (non-blocking Lock)
        if not self._toggle_lock.acquire(blocking=False):
            logger.warning("Hotkey ignoriert - Toggle bereits aktiv")
            return

        try:
            logger.debug(f"Hotkey gedrückt! Recording={self._recording}")
            self._toggle_recording()
        finally:
            self._toggle_lock.release()

    def _toggle_recording(self) -> None:
        """Toggle-Mode: Start/Stop bei jedem Tastendruck."""
        if self._recording:
            self._stop_recording()
        else:
            self._start_recording()

    def _start_recording(self) -> None:
        """Startet Streaming-Aufnahme im Worker-Thread."""
        # Sicherstellen, dass kein alter Worker noch läuft
        if self._worker_thread is not None and self._worker_thread.is_alive():
            logger.warning("Alter Worker-Thread läuft noch, warte auf Beendigung...")
            if self._stop_event is not None:
                self._stop_event.set()
            self._worker_thread.join(timeout=2.0)
            if self._worker_thread.is_alive():
                logger.error("Worker-Thread konnte nicht beendet werden!")

            self._worker_thread = None
            self._stop_event = None

        self._recording = True
        self._update_state(AppState.LISTENING)

        # Interim-Datei löschen, um veralteten Text zu vermeiden
        INTERIM_FILE.unlink(missing_ok=True)

        # Neues Stop-Event für diese Aufnahme
        self._stop_event = threading.Event()

        # Modus-Entscheidung: Streaming vs. Recording
        use_streaming = (
            self.mode == "deepgram"
            and os.getenv("WHISPER_GO_STREAMING", "true").lower() != "false"
        )

        if use_streaming:
            target = self._streaming_worker
            name = "StreamingWorker"
            logger.info("Starte Deepgram Streaming...")
        else:
            target = self._recording_worker
            name = "RecordingWorker"
            logger.info(f"Starte Standard-Aufnahme (Mode: {self.mode})...")

        # Worker-Thread starten
        self._worker_thread = threading.Thread(
            target=target,
            daemon=True,
            name=name,
        )
        self._worker_thread.start()

        # Interim-Polling starten (nur bei Streaming sinnvoll, aber schadet nicht)
        if use_streaming:
            self._start_interim_polling()

        # Result-Polling sofort starten für Audio-Levels und VAD
        self._start_result_polling()

    def _start_interim_polling(self) -> None:
        """Startet NSTimer für Interim-Text-Polling."""
        from Foundation import NSTimer  # type: ignore[import-not-found]

        self._last_interim_mtime = 0.0

        def poll_interim() -> None:
            if self._current_state != AppState.RECORDING:
                return
            try:
                mtime = INTERIM_FILE.stat().st_mtime
                if mtime > self._last_interim_mtime:
                    self._last_interim_mtime = mtime
                    interim_text = INTERIM_FILE.read_text().strip()
                    if interim_text:
                        self._update_state(AppState.RECORDING, interim_text)
            except FileNotFoundError:
                pass
            except OSError:
                pass

        self._interim_timer = NSTimer.scheduledTimerWithTimeInterval_repeats_block_(
            0.2, True, lambda _: poll_interim()
        )

    def _stop_interim_polling(self) -> None:
        """Stoppt Interim-Polling."""
        if self._interim_timer:
            self._interim_timer.invalidate()
            self._interim_timer = None

    def _on_audio_level(self, level: float) -> None:
        """Callback für Audio-Level aus dem Worker-Thread."""
        try:
            self._result_queue.put_nowait(
                DaemonMessage(type=MessageType.AUDIO_LEVEL, payload=level)
            )
        except queue.Full:
            pass

    def _streaming_worker(self) -> None:
        """
        Hintergrund-Thread für Deepgram-Streaming.
        
        Läuft in eigenem Thread, weil Deepgram async ist,
        aber der Main-Thread für QuickMacHotKey und UI frei bleiben muss.
        
        Lifecycle: Start → Mikrofon → Stream → Stop-Event → Finalize → Result
        """
        import asyncio

        try:
            model = self.model or DEFAULT_DEEPGRAM_MODEL

            # setup_logging(debug=logger.level == logging.DEBUG) # Bereits global konfiguriert

            # Eigener Event-Loop, da wir nicht im Main-Thread sind
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            try:
                transcript = loop.run_until_complete(
                    deepgram_stream_core(
                        model=model,
                        language=self.language,
                        play_ready=True,
                        external_stop_event=self._stop_event,
                        audio_level_callback=self._on_audio_level,
                    )
                )

                # LLM-Nachbearbeitung (optional)
                if self.refine and transcript:
                    self._result_queue.put(
                        DaemonMessage(type=MessageType.STATUS_UPDATE, payload=AppState.REFINING)
                    )
                    transcript = refine_transcript(
                        transcript,
                        model=self.refine_model,
                        provider=self.refine_provider,
                        context=self.context,
                    )
                elif not self.refine:
                    logger.debug("Refine deaktiviert (self.refine=False)")

                self._result_queue.put(
                    DaemonMessage(type=MessageType.TRANSCRIPT_RESULT, payload=transcript)
                )

            finally:
                loop.close()

        except Exception as e:
            logger.exception(f"Streaming-Worker Fehler: {e}")
            self._result_queue.put(e)

    def _recording_worker(self) -> None:
        """
        Standard-Aufnahme für OpenAI, Groq, Local.
        
        Nimmt Audio auf bis Stop-Event, speichert als WAV,
        und ruft dann Provider direkt auf.
        """
        import numpy as np
        import sounddevice as sd
        import soundfile as sf
        
        recorded_chunks = []
        player = get_sound_player()
        
        try:
            # Ready-Sound
            player.play("ready")
            
            # Aufnahme-Loop
            def callback(indata, frames, time, status):
                recorded_chunks.append(indata.copy())
                # RMS Berechnung und Queueing
                rms = float(np.sqrt(np.mean(indata**2)))
                try:
                    self._result_queue.put_nowait(
                        DaemonMessage(type=MessageType.AUDIO_LEVEL, payload=rms)
                    )
                except queue.Full:
                    pass
                
            with sd.InputStream(samplerate=16000, channels=1, dtype="float32", callback=callback):
                while not self._stop_event.is_set():
                    sd.sleep(50)
            
            # Stop-Sound
            player.play("stop")
            
            # Speichern
            if not recorded_chunks:
                logger.warning("Keine Audiodaten aufgenommen")
                return

            audio_data = np.concatenate(recorded_chunks)
            
            # Temp-File erstellen
            fd, temp_path = tempfile.mkstemp(suffix=".wav")
            os.close(fd)
            
            try:
                sf.write(temp_path, audio_data, 16000)
                
                # Update State: Transcribing
                # (via Queue nicht direkt möglich, aber _stop_recording setzt es im Main-Thread)
                
                # Transkribieren via Provider
                provider = get_provider(self.mode)
                transcript = provider.transcribe(
                    Path(temp_path),
                    model=self.model,
                    language=self.language
                )
                
                # LLM-Refine
                if self.refine and transcript:
                    self._result_queue.put(
                        DaemonMessage(type=MessageType.STATUS_UPDATE, payload=AppState.REFINING)
                    )
                    transcript = refine_transcript(
                        transcript,
                        model=self.refine_model,
                        provider=self.refine_provider,
                        context=self.context,
                    )
                
                self._result_queue.put(
                    DaemonMessage(type=MessageType.TRANSCRIPT_RESULT, payload=transcript)
                )
                
            finally:
                if os.path.exists(temp_path):
                    os.unlink(temp_path)
                    
        except Exception as e:
            logger.exception(f"Recording-Worker Fehler: {e}")
            self._result_queue.put(e)

    def _stop_recording(self) -> None:
        """Stoppt Aufnahme und wartet auf Worker-Beendigung."""
        if not self._recording:
            return

        logger.info("Stop-Event setzen...")

        self._stop_interim_polling()

        # Signal an Worker: Beende Deepgram-Stream sauber
        if self._stop_event:
            self._stop_event.set()

        # Worker-Thread muss beendet sein, bevor neuer starten kann
        # Verhindert parallele Mikrofon-Zugriffe
        if self._worker_thread is not None and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=2.0)
            if self._worker_thread.is_alive():
                logger.warning("Worker-Thread noch aktiv nach Timeout")

        self._recording = False
        self._update_state(AppState.TRANSCRIBING)

        # Polling läuft bereits seit Start

    def _start_result_polling(self) -> None:
        """Startet NSTimer für Result-Polling."""
        from Foundation import NSTimer  # type: ignore[import-not-found]

        # NSTimer für regelmäßiges Polling (50ms)
        def check_result() -> None:
            # Queue drainen um Backlog zu vermeiden (z.B. hunderte Audio-Level Messages)
            # Wir verarbeiten ALLE Messages, aber UI-Updates passieren so schnell wie möglich
            try:
                processed_count = 0
                while True:
                    result = self._result_queue.get_nowait()
                    processed_count += 1
                    
                    # Exception Handling
                    if isinstance(result, Exception):
                        self._stop_result_polling()
                        logger.error(f"Fehler: {result}")
                        emergency_log(f"Worker Exception: {result}") # Backup log
                        get_sound_player().play("error")
                        self._update_state(AppState.ERROR)
                        return

                    # DaemonMessage Handling
                    if isinstance(result, DaemonMessage):
                        if result.type == MessageType.STATUS_UPDATE:
                            self._update_state(result.payload)
                            # Continue draining
                        
                        elif result.type == MessageType.AUDIO_LEVEL:
                            level = result.payload
                            # VAD Logic: Switch LISTENING -> RECORDING
                            if self._current_state == AppState.LISTENING and level > VAD_THRESHOLD:
                                 self._update_state(AppState.RECORDING)
                            
                            # Forward to Overlay (nur wenn noch Recording/Listening)
                            if self._overlay and self._current_state in [AppState.LISTENING, AppState.RECORDING]:
                                self._overlay.update_audio_level(level)
                            # Continue draining

                        elif result.type == MessageType.TRANSCRIPT_RESULT:
                            self._stop_result_polling()
                            transcript = result.payload
                            if transcript:
                                self._paste_result(transcript)
                                self._update_state(AppState.DONE, transcript)
                            else:
                                logger.warning("Leeres Transkript")
                                self._update_state(AppState.IDLE)
                            return
                    
                    # Safety Break nach zu vielen Messages pro Tick, um UI nicht zu blockieren
                    if processed_count > 50:
                        break

            except queue.Empty:
                pass

        self._result_timer = NSTimer.scheduledTimerWithTimeInterval_repeats_block_(
            0.05, True, lambda _: check_result()
        )

    def _stop_result_polling(self) -> None:
        """Stoppt NSTimer."""
        if self._result_timer:
            self._result_timer.invalidate()
            self._result_timer = None

    def _paste_result(self, transcript: str) -> None:
        """Fügt Transkript via Auto-Paste ein."""
        success = paste_transcript(transcript)
        if success:
            logger.info(f"✓ Text eingefügt: '{transcript[:50]}...'")
        else:
            logger.error("Auto-Paste fehlgeschlagen")

    def _setup_app_menu(self, app) -> None:
        """Erstellt Application Menu für CMD+Q Support."""
        from AppKit import NSMenu, NSMenuItem, NSEventModifierFlagCommand  # type: ignore[import-not-found]
        
        # Hauptmenüleiste
        menubar = NSMenu.alloc().init()
        
        # App-Menü (erstes Menü, zeigt App-Name in der Menüleiste)
        app_menu_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Whisper Go", None, ""
        )
        menubar.addItem_(app_menu_item)
        
        # App-Menü Inhalt (Submenu)
        app_menu = NSMenu.alloc().initWithTitle_("Whisper Go")
        
        # "About Whisper Go" Item
        about_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "About Whisper Go", "orderFrontStandardAboutPanel:", ""
        )
        app_menu.addItem_(about_item)
        
        app_menu.addItem_(NSMenuItem.separatorItem())
        
        # "Quit Whisper Go" Item mit CMD+Q
        quit_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Quit Whisper Go", "terminate:", "q"
        )
        quit_item.setKeyEquivalentModifierMask_(NSEventModifierFlagCommand)
        app_menu.addItem_(quit_item)
        
        app_menu_item.setSubmenu_(app_menu)
        
        # Menüleiste aktivieren
        app.setMainMenu_(menubar)

    def run(self) -> None:
        """Startet Daemon (blockiert)."""
        from quickmachotkey import quickHotKey
        from AppKit import NSApplication  # type: ignore[import-not-found]
        from Foundation import NSTimer  # type: ignore[import-not-found]
        import signal

        # NSApplication initialisieren
        app = NSApplication.sharedApplication()
        
        # Dock-Icon: Konfigurierbar via ENV (default: an)
        # 0 = Regular (Dock-Icon), 1 = Accessory (kein Dock-Icon)
        show_dock = os.getenv("WHISPER_GO_DOCK_ICON", "true").lower() != "false"
        app.setActivationPolicy_(0 if show_dock else 1)
        
        # Application Menu erstellen (für CMD+Q Support wenn Dock-Icon aktiv)
        if show_dock:
            self._setup_app_menu(app)

        # UI-Controller initialisieren
        logger.info("Initialisiere UI-Controller...")
        self._menubar = MenuBarController()
        self._overlay = OverlayController()
        logger.info("UI-Controller bereit")

        # Hotkey parsen
        virtual_key, modifier_mask = parse_hotkey(self.hotkey)

        # Berechtigungen prüfen (Mikrofon - blockierend)
        if not check_microphone_permission():
            logger.error("Daemon Start abgebrochen: Fehlende Mikrofon-Berechtigung")
            return
        
        # Accessibility prüfen (nur Warnung, nicht blockierend)
        check_accessibility_permission()

        logger.info(
            f"Daemon gestartet: hotkey={self.hotkey}, "
            f"virtualKey={virtual_key}, modifierMask={modifier_mask}"
        )
        print("🎤 whisper_daemon läuft", file=sys.stderr)
        print(f"   Hotkey: {self.hotkey}", file=sys.stderr)
        if show_dock:
            print("   Beenden: CMD+Q (wenn fokussiert) oder Ctrl+C", file=sys.stderr)
        else:
            print("   Beenden: Menubar-Icon → Quit oder Ctrl+C", file=sys.stderr)

        # Hotkey registrieren
        @quickHotKey(virtualKey=virtual_key, modifierMask=modifier_mask)  # type: ignore[arg-type]
        def hotkey_handler() -> None:
            self._on_hotkey()

        # FIX: Ctrl+C Support
        # 1. Dummy-Timer, damit der Python-Interpreter regelmäßig läuft und Signale prüft
        NSTimer.scheduledTimerWithTimeInterval_repeats_block_(
            0.1, True, lambda _: None
        )

        # 2. Signal-Handler, der die App sauber beendet
        def signal_handler(sig, frame):
            app.terminate_(None)

        signal.signal(signal.SIGINT, signal_handler)

        app.run()


# =============================================================================
# Environment Loading
# =============================================================================


def load_environment() -> None:
    """Lädt .env-Datei aus dem User-Config-Verzeichnis."""
    try:
        from dotenv import load_dotenv
        from config import USER_CONFIG_DIR

        # Priorität 1: .env im User-Verzeichnis ~/.whisper_go/.env
        user_env = USER_CONFIG_DIR / ".env"
        if user_env.exists():
            load_dotenv(user_env)
        
        # Priorität 2: .env im aktuellen Verzeichnis (für Dev)
        local_env = Path(".env")
        if local_env.exists():
            load_dotenv(local_env)

    except ImportError:
        pass


# =============================================================================
# Main
# =============================================================================


def main() -> int:
    """CLI-Einstiegspunkt."""
    import argparse
    
    # Globaler Exception Handler für Crashes
    def handle_exception(exc_type, exc_value, exc_traceback):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return
        msg = f"Uncaught exception: {exc_type.__name__}: {exc_value}"
        logger.critical(msg, exc_info=(exc_type, exc_value, exc_traceback))
        emergency_log(msg) # Backup
        
    sys.excepthook = handle_exception
    
    emergency_log("=== Whisper Go Daemon gestartet ===")

    # Environment laden bevor Argumente definiert werden (für Defaults)
    load_environment()

    parser = argparse.ArgumentParser(
        description="whisper_daemon – Unified Daemon für whisper_go",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Beispiele:
  %(prog)s                          # Mit Defaults aus .env
  %(prog)s --hotkey f19             # F19 als Hotkey
  %(prog)s --hotkey cmd+shift+r     # Tastenkombination
  %(prog)s --refine                 # Mit LLM-Nachbearbeitung
        """,
    )

    parser.add_argument(
        "--hotkey",
        default=None,
        help="Hotkey (default: WHISPER_GO_HOTKEY oder 'f19')",
    )
    parser.add_argument(
        "--language",
        default=None,
        help="Sprachcode z.B. 'de', 'en'",
    )
    parser.add_argument(
        "--mode",
        choices=["openai", "deepgram", "groq", "local"],
        default=None,
        help="Transkriptions-Modus (default: WHISPER_GO_MODE)",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Deepgram-Modell (default: nova-3)",
    )
    parser.add_argument(
        "--refine",
        action="store_true",
        default=os.getenv("WHISPER_GO_REFINE", "").lower() == "true",
        help="LLM-Nachbearbeitung aktivieren",
    )
    parser.add_argument(
        "--refine-model",
        default=None,
        help="Modell für LLM-Nachbearbeitung",
    )
    parser.add_argument(
        "--refine-provider",
        choices=["openai", "openrouter", "groq"],
        default=None,
        help="LLM-Provider für Nachbearbeitung",
    )
    parser.add_argument(
        "--context",
        choices=["email", "chat", "code", "default"],
        default=None,
        help="Kontext für LLM-Nachbearbeitung",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Debug-Logging aktivieren",
    )

    args = parser.parse_args()

    setup_logging(debug=args.debug)

    # Konfiguration: CLI > ENV > Default
    hotkey = args.hotkey or os.getenv("WHISPER_GO_HOTKEY", "f19")
    language = args.language or os.getenv("WHISPER_GO_LANGUAGE")
    model = args.model or os.getenv("WHISPER_GO_MODEL")
    mode = args.mode or os.getenv("WHISPER_GO_MODE", "deepgram")

    # Daemon starten
    try:
        daemon = WhisperDaemon(
            hotkey=hotkey,
            language=language,
            model=model,
            refine=args.refine,
            refine_model=args.refine_model,
            refine_provider=args.refine_provider,
            context=args.context,
            mode=mode,
        )
        daemon.run()
    except ValueError as e:
        print(f"Konfigurationsfehler: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\n👋 Daemon beendet", file=sys.stderr)
        return 0
    except Exception as e:
        logger.exception(f"Unerwarteter Fehler: {e}")
        print(f"Fehler: {e}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
