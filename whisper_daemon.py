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
    from config import INTERIM_FILE, VAD_THRESHOLD, WHISPER_SAMPLE_RATE
    from utils import setup_logging, show_error_alert
    from config import DEFAULT_DEEPGRAM_MODEL
    from providers.deepgram_stream import deepgram_stream_core
    from providers import get_provider
    from refine.llm import refine_transcript
    from whisper_platform import get_sound_player
    from utils.state import AppState, DaemonMessage, MessageType
    from utils import parse_hotkey, paste_transcript
    from utils.permissions import (
        check_microphone_permission,
        check_accessibility_permission,
    )
    from ui import MenuBarController, OverlayController
except Exception as e:
    emergency_log(f"CRITICAL IMPORT ERROR: {e}")
    # Auch direkt auf stderr ausgeben, damit der Fehler bei CLI-Start sichtbar ist
    try:
        import traceback  # noqa: WPS433 (safety import inside except)

        traceback.print_exc()
    except Exception:
        # Falls traceback import fehlschlägt, zumindest den Fehler ausgeben
        print(f"CRITICAL IMPORT ERROR: {e}", file=sys.stderr)

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
        hotkey_mode: str | None = None,
    ):
        self.hotkey = hotkey
        self.language = language
        self.model = model
        self.refine = refine
        self.refine_model = refine_model
        self.refine_provider = refine_provider
        self.context = context
        self.mode = mode
        self.hotkey_mode = hotkey_mode or os.getenv("WHISPER_GO_HOTKEY_MODE", "toggle")

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

        # Provider-Cache: vermeidet Re-Init (z.B. lokales Modell laden)
        self._provider_cache: dict[str, object] = {}
        self._hold_listener = None
        self._hold_active = False
        self._fn_active = False
        self._caps_active = False
        self._modifier_tap = None
        self._modifier_source = None
        self._modifier_callback = None

    # =============================================================================
    # Modifier Hotkeys (Fn/Globe, CapsLock)
    # =============================================================================

    def _call_on_main(self, fn) -> None:
        """Führt fn auf dem Main-Thread aus (AppKit thread-safe)."""
        try:
            from Foundation import NSThread  # type: ignore[import-not-found]

            if NSThread.isMainThread():
                fn()
                return
        except Exception:
            pass

        try:
            from PyObjCTools import AppHelper  # type: ignore[import-not-found]

            AppHelper.callAfter(fn)
        except Exception:  # pragma: no cover
            fn()

    def _start_modifier_hotkey_tap(
        self,
        *,
        keycode: int,
        flag_mask: int,
        active_attr: str,
        name: str,
        hotkey_mode: str,
        toggle_on_down_only: bool,
    ) -> bool:
        """Installiert einen Quartz FlagsChanged Tap für Modifier-Keys."""
        try:
            from Quartz import (  # type: ignore[import-not-found]
                CGEventTapCreate,
                CGEventTapEnable,
                CGEventMaskBit,
                CFMachPortCreateRunLoopSource,
                CFRunLoopGetCurrent,
                CFRunLoopAddSource,
                kCFRunLoopCommonModes,
                kCGHIDEventTap,
                kCGHeadInsertEventTap,
                kCGEventTapOptionListenOnly,
                kCGEventFlagsChanged,
                CGEventGetFlags,
                CGEventGetIntegerValueField,
                kCGKeyboardEventKeycode,
            )
        except Exception as e:  # pragma: no cover
            logger.error(f"{name} Hotkey Tap benötigt Quartz: {e}")
            return False

        def callback(_proxy, event_type, event, _refcon):
            try:
                if event_type != kCGEventFlagsChanged:
                    return event
                event_keycode = int(
                    CGEventGetIntegerValueField(event, kCGKeyboardEventKeycode)
                )
                if event_keycode != keycode:
                    return event

                flags = int(CGEventGetFlags(event))
                is_down = bool(flags & flag_mask)
                is_active = bool(getattr(self, active_attr))

                # Always keep state in sync
                setattr(self, active_attr, is_down)

                if hotkey_mode == "hold":
                    if is_down and not is_active:
                        logger.debug(f"Hotkey {name} down")
                        self._call_on_main(self._start_recording)
                    elif not is_down and is_active:
                        logger.debug(f"Hotkey {name} up")
                        self._call_on_main(self._stop_recording)
                else:
                    if toggle_on_down_only:
                        if is_down and not is_active:
                            logger.debug(f"Hotkey {name} down")
                            self._call_on_main(self._on_hotkey)
                    else:
                        logger.debug(f"Hotkey {name} pressed")
                        self._call_on_main(self._on_hotkey)
            except Exception as e:
                logger.debug(f"{name} tap error: {e}")
            return event

        tap = CGEventTapCreate(
            kCGHIDEventTap,
            kCGHeadInsertEventTap,
            kCGEventTapOptionListenOnly,
            CGEventMaskBit(kCGEventFlagsChanged),
            callback,
            None,
        )
        if tap is None:  # pragma: no cover
            logger.error(f"{name} Hotkey Tap konnte nicht erstellt werden (Input Monitoring?)")
            return False

        source = CFMachPortCreateRunLoopSource(None, tap, 0)
        CFRunLoopAddSource(CFRunLoopGetCurrent(), source, kCFRunLoopCommonModes)
        CGEventTapEnable(tap, True)

        self._modifier_tap = tap
        self._modifier_source = source
        self._modifier_callback = callback
        return True

    def _start_fn_hotkey_monitor(self, hotkey_mode: str) -> bool:
        """Erfasst Fn/Globe als Hotkey über Quartz Flags Tap."""
        try:
            from Quartz import kCGEventFlagMaskSecondaryFn  # type: ignore[import-not-found]
        except Exception:  # pragma: no cover
            kCGEventFlagMaskSecondaryFn = 0x800000

        return self._start_modifier_hotkey_tap(
            keycode=63,
            flag_mask=int(kCGEventFlagMaskSecondaryFn),
            active_attr="_fn_active",
            name="fn",
            hotkey_mode=hotkey_mode,
            toggle_on_down_only=True,
        )

    def _start_capslock_hotkey_monitor(self, hotkey_mode: str) -> bool:
        """Erfasst CapsLock als Hotkey über Quartz Flags Tap."""
        try:
            from Quartz import kCGEventFlagMaskAlphaShift  # type: ignore[import-not-found]
        except Exception:  # pragma: no cover
            kCGEventFlagMaskAlphaShift = 0x10000

        return self._start_modifier_hotkey_tap(
            keycode=57,
            flag_mask=int(kCGEventFlagMaskAlphaShift),
            active_attr="_caps_active",
            name="capslock",
            hotkey_mode=hotkey_mode,
            toggle_on_down_only=False,
        )

    def _get_provider(self, mode: str):
        """Gibt gecachten Provider zurück oder erstellt ihn."""
        provider = self._provider_cache.get(mode)
        if provider is None:
            provider = get_provider(mode)
            self._provider_cache[mode] = provider
        return provider

    @staticmethod
    def _trim_silence(
        data,
        threshold: float,
        sample_rate: int,
        pad_s: float = 0.15,
    ):
        """Schneidet Stille am Anfang/Ende ab (RMS über kurze Fenster)."""
        import numpy as np

        mono = data.squeeze()
        if mono.ndim != 1:
            mono = mono.reshape(-1)
        window = int(sample_rate * 0.02)  # 20ms
        hop = int(sample_rate * 0.01)     # 10ms
        if mono.shape[0] <= window:
            return mono.astype(np.float32, copy=False)
        frame_count = (mono.shape[0] - window) // hop + 1
        if frame_count <= 0:
            return mono.astype(np.float32, copy=False)
        strides = (mono.strides[0] * hop, mono.strides[0])
        frames = np.lib.stride_tricks.as_strided(
            mono, shape=(frame_count, window), strides=strides
        )
        rms = np.sqrt(np.mean(frames**2, axis=1))
        active = rms > threshold
        if not np.any(active):
            return mono.astype(np.float32, copy=False)
        first = int(np.argmax(active))
        last = int(len(active) - np.argmax(active[::-1]) - 1)
        start = max(0, first * hop - int(pad_s * sample_rate))
        end = min(mono.shape[0], last * hop + window + int(pad_s * sample_rate))
        return mono[start:end].astype(np.float32, copy=False)

    def _preload_local_model_async(self) -> None:
        """Lädt lokales Modell im Hintergrund vor (reduziert erste Latenz)."""
        if self.mode != "local":
            return
        provider = self._get_provider("local")
        if not hasattr(provider, "preload"):
            return

        def _preload():
            try:
                provider.preload(self.model)
                logger.debug("Lokales Modell vorab geladen")
            except Exception as e:
                logger.warning(f"Preload lokales Modell fehlgeschlagen: {e}")

        threading.Thread(target=_preload, daemon=True, name="LocalPreload").start()

    def _update_state(self, state: AppState, text: str | None = None) -> None:
        """Aktualisiert State und benachrichtigt UI-Controller."""
        self._current_state = state
        logger.debug(f"State: {state}" + (f" text='{text[:20]}...'" if text else ""))

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

    # =============================================================================
    # Hold-to-record Hotkey (Push-to-talk)
    # =============================================================================

    @staticmethod
    def _parse_pynput_hotkey(hotkey_str: str):
        """Parst Hotkey-String in pynput-Key-Set."""
        from pynput import keyboard  # type: ignore[import-not-found]

        parts = [p.strip().lower() for p in hotkey_str.split("+")]
        keys: set = set()

        special_map = {
            "space": keyboard.Key.space,
            "tab": keyboard.Key.tab,
            "enter": keyboard.Key.enter,
            "return": keyboard.Key.enter,
            "esc": keyboard.Key.esc,
            "escape": keyboard.Key.esc,
        }

        for part in parts:
            if part in ("ctrl", "control"):
                keys.add(keyboard.Key.ctrl)
            elif part in ("alt", "option"):
                keys.add(keyboard.Key.alt)
            elif part == "shift":
                keys.add(keyboard.Key.shift)
            elif part in ("cmd", "command", "win"):
                keys.add(keyboard.Key.cmd)
            elif part in special_map:
                keys.add(special_map[part])
            elif part.startswith("f") and part[1:].isdigit():
                f_key = getattr(keyboard.Key, part, None)
                if f_key:
                    keys.add(f_key)
                else:
                    raise ValueError(f"Unbekannte Funktionstaste: {part}")
            elif part == "fn":
                # Fn/Globe key: pynput has no Key.fn, use virtual keycode
                keys.add(keyboard.KeyCode.from_vk(63))
            elif part in ("capslock", "caps_lock", "caps"):
                keys.add(keyboard.Key.caps_lock)
            else:
                # Normale Taste
                keys.add(keyboard.KeyCode.from_char(part))

        return keys

    def _start_hold_hotkey_listener(self) -> bool:
        """Startet pynput Listener für Hold-Mode. Gibt True bei Erfolg zurück."""
        try:
            from pynput import keyboard  # type: ignore[import-not-found]
        except ImportError:
            logger.error("Hold Hotkey Mode benötigt pynput")
            return False

        try:
            hotkey_keys = self._parse_pynput_hotkey(self.hotkey)
        except ValueError as e:
            logger.error(f"Hotkey Parsing fehlgeschlagen: {e}")
            return False

        current_keys: set = set()

        def on_press(key):
            if self._current_state == AppState.ERROR:
                return
            current_keys.add(key)
            if not self._hold_active and hotkey_keys.issubset(current_keys):
                self._hold_active = True
                logger.debug("Hotkey hold down")
                self._call_on_main(self._start_recording)

        def on_release(key):
            current_keys.discard(key)
            if self._hold_active and not hotkey_keys.issubset(current_keys):
                self._hold_active = False
                logger.debug("Hotkey hold up")
                self._call_on_main(self._stop_recording)

        listener = keyboard.Listener(on_press=on_press, on_release=on_release)
        listener.daemon = True
        listener.start()
        self._hold_listener = listener
        return True

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
                        DaemonMessage(
                            type=MessageType.STATUS_UPDATE, payload=AppState.REFINING
                        )
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
                    DaemonMessage(
                        type=MessageType.TRANSCRIPT_RESULT, payload=transcript
                    )
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

            with sd.InputStream(
                samplerate=WHISPER_SAMPLE_RATE,
                channels=1,
                dtype="float32",
                callback=callback,
            ):
                while not self._stop_event.is_set():
                    sd.sleep(50)

            # Stop-Sound
            player.play("stop")

            # Speichern
            if not recorded_chunks:
                logger.warning("Keine Audiodaten aufgenommen")
                return

            audio_data = np.concatenate(recorded_chunks)

            # Silence-Trimming (reduziert Zeit/Kosten bei allen Providern)
            raw_duration = 0.0
            audio_duration = 0.0
            if hasattr(audio_data, "shape"):
                raw_duration = float(audio_data.shape[0]) / WHISPER_SAMPLE_RATE
                trimmed_audio = self._trim_silence(
                    audio_data, VAD_THRESHOLD, WHISPER_SAMPLE_RATE
                )
                trimmed_duration = float(trimmed_audio.shape[0]) / WHISPER_SAMPLE_RATE
                if trimmed_duration < raw_duration - 0.05:
                    logger.info(
                        f"Trimmed silence: raw={raw_duration:.2f}s -> trimmed={trimmed_duration:.2f}s"
                    )

                audio_duration = trimmed_duration
                audio_data = trimmed_audio

            # Temp-File erstellen
            fd, temp_path = tempfile.mkstemp(suffix=".wav")
            os.close(fd)

            try:
                # Update State: Transcribing
                # (via Queue nicht direkt möglich, aber _stop_recording setzt es im Main-Thread)

                # Transkribieren via Provider
                provider = self._get_provider(self.mode)
                t0 = time.perf_counter()
                if self.mode == "local" and hasattr(provider, "transcribe_audio"):
                    transcript = provider.transcribe_audio(
                        audio_data, model=self.model, language=self.language
                    )
                else:
                    sf.write(temp_path, audio_data, WHISPER_SAMPLE_RATE)
                    transcript = provider.transcribe(
                        Path(temp_path), model=self.model, language=self.language
                    )
                t_transcribe = time.perf_counter() - t0
                if audio_duration > 0:
                    rtf = t_transcribe / audio_duration
                    logger.info(
                        f"Transcription performance: mode={self.mode}, "
                        f"model={self.model or getattr(provider, 'default_model', None)}, "
                        f"audio={audio_duration:.2f}s, time={t_transcribe:.2f}s, rtf={rtf:.2f}x"
                    )
                else:
                    logger.info(
                        f"Transcription performance: mode={self.mode}, "
                        f"time={t_transcribe:.2f}s (audio duration unknown)"
                    )

                # LLM-Refine
                if self.refine and transcript:
                    self._result_queue.put(
                        DaemonMessage(
                            type=MessageType.STATUS_UPDATE, payload=AppState.REFINING
                        )
                    )
                    t1 = time.perf_counter()
                    transcript = refine_transcript(
                        transcript,
                        model=self.refine_model,
                        provider=self.refine_provider,
                        context=self.context,
                    )
                    t_refine = time.perf_counter() - t1
                    logger.info(f"Refine performance: provider={self.refine_provider}, time={t_refine:.2f}s")

                self._result_queue.put(
                    DaemonMessage(
                        type=MessageType.TRANSCRIPT_RESULT, payload=transcript
                    )
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
                        emergency_log(f"Worker Exception: {result}")  # Backup log

                        # API-Key-Fehler als Pop-up anzeigen
                        if isinstance(result, ValueError):
                            show_error_alert("API-Key fehlt", str(result))

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
                            if (
                                self._current_state == AppState.LISTENING
                                and level > VAD_THRESHOLD
                            ):
                                self._update_state(AppState.RECORDING)

                            # Forward to Overlay (nur wenn noch Recording/Listening)
                            if self._overlay and self._current_state in [
                                AppState.LISTENING,
                                AppState.RECORDING,
                            ]:
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

    def _show_welcome_if_needed(self) -> None:
        """Zeigt Welcome Window beim ersten Start oder wenn aktiviert."""
        from utils import has_seen_onboarding, get_show_welcome_on_startup
        from ui import WelcomeController

        show_welcome = not has_seen_onboarding() or get_show_welcome_on_startup()

        if show_welcome:
            self._welcome = WelcomeController(
                hotkey=self.hotkey,
                config={
                    "deepgram_key": bool(os.getenv("DEEPGRAM_API_KEY")),
                    "groq_key": bool(os.getenv("GROQ_API_KEY")),
                    "refine": self.refine,
                    "refine_model": self.refine_model,
                    "language": self.language,
                    "mode": self.mode,
                },
            )
            # Callback für Settings-Änderungen setzen
            self._welcome.set_on_settings_changed(self._reload_settings)
            self._welcome.show()
        else:
            self._welcome = None

        # Callback für Menubar "Setup..." setzen
        if self._menubar:
            self._menubar.set_welcome_callback(self._show_welcome_window)

    def _show_welcome_window(self) -> None:
        """Zeigt Welcome Window (via Menubar)."""
        from ui import WelcomeController

        # Neues Window erstellen falls noch nicht vorhanden
        if self._welcome is None:
            self._welcome = WelcomeController(
                hotkey=self.hotkey,
                config={
                    "deepgram_key": bool(os.getenv("DEEPGRAM_API_KEY")),
                    "groq_key": bool(os.getenv("GROQ_API_KEY")),
                    "refine": self.refine,
                    "refine_model": self.refine_model,
                    "language": self.language,
                    "mode": self.mode,
                },
            )
            # Callback für Settings-Änderungen setzen
            self._welcome.set_on_settings_changed(self._reload_settings)
        self._welcome.show()

    def _reload_settings(self) -> None:
        """Lädt Settings aus .env neu und wendet sie an (außer Hotkey)."""
        from utils.preferences import get_env_setting

        # .env neu laden (override=True um Änderungen zu übernehmen)
        load_environment(override_existing=True)

        # Hotkey / Hotkey-Mode Änderungen erfordern Neustart
        new_hotkey = get_env_setting("WHISPER_GO_HOTKEY")
        if new_hotkey and new_hotkey.lower() != (self.hotkey or "").lower():
            logger.warning(
                f"Hotkey geändert ({self.hotkey} → {new_hotkey}). Neustart erforderlich."
            )

        new_hotkey_mode = get_env_setting("WHISPER_GO_HOTKEY_MODE")
        if new_hotkey_mode and new_hotkey_mode.lower() != (self.hotkey_mode or "").lower():
            logger.warning(
                f"Hotkey-Modus geändert ({self.hotkey_mode} → {new_hotkey_mode}). Neustart erforderlich."
            )

        # Settings aktualisieren (außer Hotkey - erfordert Neustart)
        new_mode = get_env_setting("WHISPER_GO_MODE")
        if new_mode:
            self.mode = new_mode

        new_language = get_env_setting("WHISPER_GO_LANGUAGE")
        self.language = new_language  # None ist valid für "auto"

        new_refine = get_env_setting("WHISPER_GO_REFINE")
        if new_refine is not None:
            self.refine = new_refine.lower() == "true"

        new_refine_provider = get_env_setting("WHISPER_GO_REFINE_PROVIDER")
        if new_refine_provider:
            self.refine_provider = new_refine_provider

        new_refine_model = get_env_setting("WHISPER_GO_REFINE_MODEL")
        if new_refine_model:
            self.refine_model = new_refine_model

        logger.info(
            f"Settings reloaded: mode={self.mode}, language={self.language}, "
            f"refine={self.refine}, refine_provider={self.refine_provider}, "
            f"refine_model={self.refine_model}"
        )

        # Falls lokal aktiviert, Modell im Hintergrund vorladen
        self._preload_local_model_async()

    def run(self) -> None:
        """Startet Daemon (blockiert)."""
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

        # Welcome Window (beim ersten Start oder wenn aktiviert)
        self._show_welcome_if_needed()

        # Hotkey parsen
        hotkey_mode = (self.hotkey_mode or "toggle").lower()
        if hotkey_mode not in ("toggle", "hold"):
            logger.warning(f"Unbekannter Hotkey-Modus '{hotkey_mode}', fallback auf toggle")
            hotkey_mode = "toggle"

        # Berechtigungen prüfen (Mikrofon - blockierend)
        if not check_microphone_permission():
            logger.error("Daemon Start abgebrochen: Fehlende Mikrofon-Berechtigung")
            return

        # Accessibility prüfen (nur Warnung, nicht blockierend)
        accessibility_ok = check_accessibility_permission()

        hotkey_str = (self.hotkey or "").strip().lower()
        hotkey_is_fn = hotkey_str == "fn"
        hotkey_is_capslock = hotkey_str in ("capslock", "caps_lock")

        if hotkey_mode == "hold" and not accessibility_ok and not (hotkey_is_fn or hotkey_is_capslock):
            logger.warning(
                "Hold Hotkey Mode benötigt Bedienungshilfen-Zugriff. "
                "Fallback auf Toggle-Mode bis Berechtigung erteilt ist."
            )
            hotkey_mode = "toggle"

        # Logging + Start-Info
        print("🎤 whisper_daemon läuft", file=sys.stderr)
        print(f"   Hotkey: {self.hotkey}", file=sys.stderr)
        print(f"   Hotkey Mode: {hotkey_mode}", file=sys.stderr)
        if show_dock:
            print("   Beenden: CMD+Q (wenn fokussiert) oder Ctrl+C", file=sys.stderr)
        else:
            print("   Beenden: Menubar-Icon → Quit oder Ctrl+C", file=sys.stderr)

        # Lokales Modell vorab laden (falls aktiv)
        self._preload_local_model_async()

        if hotkey_is_fn:
            logger.info(
                f"Daemon gestartet: hotkey=fn (Globe), hotkey_mode={hotkey_mode} (Quartz FlagsChanged Tap)"
            )
            if not accessibility_ok:
                logger.warning(
                    "Fn/Globe Hotkey benötigt Bedienungshilfen-Zugriff. "
                    "Bitte in Systemeinstellungen aktivieren."
                )
            if not self._start_fn_hotkey_monitor(hotkey_mode):
                logger.error("Fn Hotkey Monitor konnte nicht gestartet werden.")
        elif hotkey_is_capslock:
            logger.info(
                f"Daemon gestartet: hotkey=capslock, hotkey_mode={hotkey_mode} (Quartz FlagsChanged Tap)"
            )
            if not accessibility_ok:
                logger.warning(
                    "CapsLock Hotkey benötigt Bedienungshilfen-Zugriff. "
                    "Bitte in Systemeinstellungen aktivieren."
                )
            if not self._start_capslock_hotkey_monitor(hotkey_mode):
                logger.error("CapsLock Hotkey Monitor konnte nicht gestartet werden.")
        elif hotkey_mode == "toggle":
            from quickmachotkey import quickHotKey

            virtual_key, modifier_mask = parse_hotkey(self.hotkey)
            logger.info(
                f"Daemon gestartet: hotkey={self.hotkey}, "
                f"virtualKey={virtual_key}, modifierMask={modifier_mask}, "
                f"hotkey_mode=toggle"
            )

            # Hotkey registrieren (Toggle)
            @quickHotKey(virtualKey=virtual_key, modifierMask=modifier_mask)  # type: ignore[arg-type]
            def hotkey_handler() -> None:
                self._on_hotkey()
        else:
            logger.info(
                f"Daemon gestartet: hotkey={self.hotkey}, hotkey_mode=hold (pynput)"
            )
            if not self._start_hold_hotkey_listener():
                logger.error("Hold Hotkey Listener konnte nicht gestartet werden, fallback auf toggle")
                from quickmachotkey import quickHotKey

                virtual_key, modifier_mask = parse_hotkey(self.hotkey)

                @quickHotKey(virtualKey=virtual_key, modifierMask=modifier_mask)  # type: ignore[arg-type]
                def hotkey_handler() -> None:
                    self._on_hotkey()

        # FIX: Ctrl+C Support
        # 1. Dummy-Timer, damit der Python-Interpreter regelmäßig läuft und Signale prüft
        NSTimer.scheduledTimerWithTimeInterval_repeats_block_(0.1, True, lambda _: None)

        # 2. Signal-Handler, der die App sauber beendet
        def signal_handler(sig, frame):
            app.terminate_(None)

        signal.signal(signal.SIGINT, signal_handler)

        app.run()


# =============================================================================
# Environment Loading
# =============================================================================


def load_environment(override_existing: bool = False) -> None:
    """Lädt .env-Datei aus dem User-Config-Verzeichnis.

    `override_existing=False` respektiert bereits gesetzte Umgebungsvariablen
    (ENV > .env). Beim Reload wird mit override=True geladen.
    """
    try:
        from dotenv import load_dotenv
        from config import USER_CONFIG_DIR

        # Priorität 1: .env im User-Verzeichnis ~/.whisper_go/.env
        user_env = USER_CONFIG_DIR / ".env"
        if user_env.exists():
            load_dotenv(user_env, override=override_existing)

        # Priorität 2: .env im aktuellen Verzeichnis (für Dev)
        local_env = Path(".env")
        if local_env.exists():
            load_dotenv(local_env, override=override_existing)

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
        emergency_log(msg)  # Backup

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
        "--hotkey-mode",
        choices=["toggle", "hold"],
        default=None,
        help="Hotkey-Modus: toggle oder hold (default: WHISPER_GO_HOTKEY_MODE)",
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
    hotkey_mode = args.hotkey_mode or os.getenv("WHISPER_GO_HOTKEY_MODE", "toggle")
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
            hotkey_mode=hotkey_mode,
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
