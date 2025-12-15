#!/usr/bin/env python3
"""
pulsescribe_daemon.py – Unified Daemon für PulseScribe.

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
    python pulsescribe_daemon.py              # Mit Defaults aus .env
    python pulsescribe_daemon.py --hotkey fn  # Hotkey überschreiben
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
        debug_file = Path.home() / ".pulsescribe" / "startup.log"
        debug_file.parent.mkdir(exist_ok=True)
        with open(debug_file, "a", encoding="utf-8") as f:
            timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
            f.write(f"[{timestamp}] {msg}\n")
    except Exception:
        pass


emergency_log("=== Booting PulseScribe Daemon ===")

try:
    from config import INTERIM_FILE, VAD_THRESHOLD, WHISPER_SAMPLE_RATE
    from utils import setup_logging, show_error_alert
    from config import DEFAULT_DEEPGRAM_MODEL, DEFAULT_LOCAL_MODEL
    from utils.env import get_env_bool, get_env_bool_default, parse_bool
    from utils.environment import load_environment
    from providers.deepgram_stream import deepgram_stream_core
    from providers import get_provider
    from refine.llm import refine_transcript
    from whisper_platform import get_sound_player
    from utils.state import AppState, DaemonMessage, MessageType
    from utils import parse_hotkey, paste_transcript
    from utils.permissions import (
        check_microphone_permission,
        check_accessibility_permission,
        check_input_monitoring_permission,
        is_permission_related_message,
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
logger = logging.getLogger("pulsescribe")

# =============================================================================
# PulseScribeDaemon: Hauptklasse
# =============================================================================


class PulseScribeDaemon:
    """
    Unified Daemon für PulseScribe.

    Architektur:
        Main-Thread: Hotkey-Listener (QuickMacHotKey) + UI-Updates
        Worker-Thread: Deepgram-Streaming (async)

    State-Flow:
        idle → [Hotkey] → recording → [Hotkey] → transcribing → done/error → idle
    """

    def __init__(
        self,
        hotkey: str = "fn",
        language: str | None = None,
        model: str | None = None,
        refine: bool = False,
        refine_model: str | None = None,
        refine_provider: str | None = None,
        context: str | None = None,
        mode: str | None = None,
        hotkey_mode: str | None = None,
        toggle_hotkey: str | None = None,
        hold_hotkey: str | None = None,
    ):
        self.hotkey = hotkey
        self.language = language
        self.model = model
        self.refine = refine
        self.refine_model = refine_model
        self.refine_provider = refine_provider
        self.context = context
        self.mode = mode
        self.hotkey_mode = hotkey_mode or os.getenv("PULSESCRIBE_HOTKEY_MODE", "toggle")
        self.toggle_hotkey = toggle_hotkey or os.getenv("PULSESCRIBE_TOGGLE_HOTKEY")
        self.hold_hotkey = hold_hotkey or os.getenv("PULSESCRIBE_HOLD_HOTKEY")

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
        self._welcome = None
        self._onboarding_wizard = None

        # Provider-Cache: vermeidet Re-Init (z.B. lokales Modell laden)
        self._provider_cache: dict[str, object] = {}
        # Effective mode for the current recording run (may differ after fallbacks).
        self._run_mode: str | None = None
        # Test dictation run (in-app, no auto-paste)
        self._test_run_active = False
        self._test_run_callback = None
        # pynput key listeners (hold hotkeys + toggle fallbacks)
        self._pynput_listeners: list = []
        self._toggle_hotkey_handlers: list = []
        self._fn_active = False
        self._caps_active = False
        self._modifier_taps: list[tuple[object, object, object]] = []
        # Track active hold hotkeys to avoid race conditions
        self._active_hold_sources: set[str] = set()
        self._recording_started_by_hold = False
        # Temporary mode: route hotkeys to a safe, local "test dictation" run
        # (used by onboarding wizard).
        self._test_hotkey_mode_enabled = False
        self._test_hotkey_mode_callback = None
        self._test_hotkey_mode_state_callback = None
        # Hotkey reconfigure may be deferred while recording/transcribing.
        self._pending_hotkey_reconfigure = False

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

    @staticmethod
    def _safe_call(fn, *args, **kwargs) -> None:
        """Best-effort invoke; never raises (used for UI callbacks)."""
        try:
            fn(*args, **kwargs)
        except Exception:
            pass

    def _start_recording_from_hold(self, source_id: str) -> None:
        """Startet Recording nur, wenn der Hold-Hotkey noch aktiv ist."""
        if source_id not in self._active_hold_sources:
            return
        if self._recording:
            return
        if self._test_hotkey_mode_enabled and callable(self._test_hotkey_mode_callback):
            self._start_test_dictation_from_hotkey()
        else:
            self._start_recording()
        if self._recording:
            self._recording_started_by_hold = True

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
                    source_id = f"modifier:{name}"
                    if is_down and not is_active:
                        logger.debug(f"Hotkey {name} down")
                        self._active_hold_sources.add(source_id)
                        self._call_on_main(
                            lambda: self._start_recording_from_hold(source_id)
                        )
                    elif not is_down and is_active:
                        logger.debug(f"Hotkey {name} up")
                        self._active_hold_sources.discard(source_id)
                        if (
                            not self._active_hold_sources
                            and self._recording_started_by_hold
                        ):
                            self._call_on_main(self._stop_recording_from_hotkey)
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
            logger.error(
                f"{name} Hotkey Tap konnte nicht erstellt werden (Input Monitoring?)"
            )
            return False

        source = CFMachPortCreateRunLoopSource(None, tap, 0)
        CFRunLoopAddSource(CFRunLoopGetCurrent(), source, kCFRunLoopCommonModes)
        CGEventTapEnable(tap, True)

        self._modifier_taps.append((tap, source, callback))
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
    def _clean_model_name(model: str | None) -> str | None:
        """Normalisiert Modellnamen (None/Whitespace → None)."""
        if not model:
            return None
        cleaned = model.strip()
        return cleaned or None

    def _model_name_for_logging(
        self, provider, *, mode_override: str | None = None
    ) -> str | None:
        """Ermittelt den tatsächlich erwarteten Modellnamen für Logs."""
        mode = mode_override or self.mode
        model = self._clean_model_name(self.model)
        if mode == "local" and model is None:
            model = self._clean_model_name(os.getenv("PULSESCRIBE_LOCAL_MODEL"))
        if model is None:
            model = self._clean_model_name(getattr(provider, "default_model", None))
        return model

    def _local_backend_for_logging(
        self, provider, *, mode_override: str | None = None
    ) -> str | None:
        """Ermittelt das tatsächlich verwendete Local-Backend für Logs."""
        mode = mode_override or self.mode
        if mode != "local":
            return None
        backend = getattr(provider, "backend", None)
        if not backend:
            backend = getattr(provider, "_backend", None)
        if not backend:
            backend = (
                (os.getenv("PULSESCRIBE_LOCAL_BACKEND") or "whisper").strip().lower()
            )
            if backend == "auto":
                backend = "auto"
            elif backend in {"faster", "faster-whisper"}:
                backend = "faster"
            elif backend in {"whisper", "openai-whisper"}:
                backend = "whisper"
        return backend or None

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
        hop = int(sample_rate * 0.01)  # 10ms
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
        # Für Trimming ist ein "inklusive" Threshold sinnvoller, um leise
        # Auskling-Phoneme nicht abzuschneiden (>= statt >).
        active = rms >= threshold
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
                warmup_flag = get_env_bool("PULSESCRIBE_LOCAL_WARMUP")
                backend = getattr(provider, "backend", None) or getattr(
                    provider, "_backend", None
                )
                device = getattr(provider, "device", None) or getattr(
                    provider, "_device", None
                )
                # Default: nur Warmup für openai-whisper auf MPS (größter "cold start" Effekt).
                should_warmup = (
                    warmup_flag
                    if warmup_flag is not None
                    else (backend == "whisper" and device == "mps")
                )
                if should_warmup and hasattr(provider, "transcribe_audio"):
                    import numpy as np

                    warmup_s = 0.5
                    warmup_samples = int(WHISPER_SAMPLE_RATE * warmup_s)
                    warmup_audio = np.zeros(warmup_samples, dtype=np.float32)
                    warmup_language = self.language or "en"
                    try:
                        provider.transcribe_audio(
                            warmup_audio,
                            model=self.model,
                            language=warmup_language,
                        )
                        logger.debug("Lokales Modell warmup abgeschlossen")
                    except Exception as e:
                        logger.debug(f"Lokales Modell warmup fehlgeschlagen: {e}")
            except Exception as e:
                logger.warning(f"Preload lokales Modell fehlgeschlagen: {e}")

        threading.Thread(target=_preload, daemon=True, name="LocalPreload").start()

    def _update_state(self, state: AppState, text: str | None = None) -> None:
        """Aktualisiert State und benachrichtigt UI-Controller."""
        self._current_state = state
        logger.debug(f"State: {state}" + (f" text='{text[:20]}...'" if text else ""))

        if self._menubar:
            self._menubar.update_state(state, text)
        if self._overlay:
            self._overlay.update_state(state, text)

    def _flush_ui_and_wait(self) -> None:
        """Erzwingt UI-Rendering und wartet auf WindowServer.

        Warum nötig: NSStatusBar wird von WindowServer gerendert (separater Prozess).
        setTitle_() sendet nur eine Nachricht via Mach-Port, aber wir können nicht
        garantieren, wann WindowServer das Icon tatsächlich zeichnet.

        Lösung:
        1. NSRunLoop.runMode_beforeDate_() - flusht AppKit Event-Queue (~0.2ms)
        2. time.sleep(0.015) - gibt WindowServer Zeit zum Rendern (~15ms)
        3. Sound-Feedback erfolgt VOR dem Delay für sofortige auditive Bestätigung

        Gesamt-Latenz: ~15-16ms, nicht wahrnehmbar für User.
        """
        from Foundation import (  # type: ignore[import-not-found]
            NSDate,
            NSDefaultRunLoopMode,
            NSRunLoop,
        )

        # 1. AppKit Event-Queue flushen
        NSRunLoop.currentRunLoop().runMode_beforeDate_(
            NSDefaultRunLoopMode, NSDate.date()
        )
        # 2. WindowServer Zeit zum Rendern geben
        time.sleep(0.015)

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
        if self._test_hotkey_mode_enabled and callable(self._test_hotkey_mode_callback):
            if self._recording:
                self._stop_test_dictation_from_hotkey()
            else:
                self._start_test_dictation_from_hotkey()
            return
        if self._recording:
            self._stop_recording()
        else:
            self._start_recording()

    def enable_test_hotkey_mode(self, callback, *, state_callback=None) -> None:
        """Routes hotkey presses to a safe onboarding test dictation run."""
        self._test_hotkey_mode_enabled = True
        self._test_hotkey_mode_callback = callback
        self._test_hotkey_mode_state_callback = state_callback

    def disable_test_hotkey_mode(self) -> None:
        """Disables onboarding test hotkey routing."""
        self._test_hotkey_mode_enabled = False
        self._test_hotkey_mode_callback = None
        self._test_hotkey_mode_state_callback = None

    def _start_test_dictation_from_hotkey(self) -> None:
        """Starts a test dictation run using the configured hotkey."""
        cb = self._test_hotkey_mode_callback
        state_cb = self._test_hotkey_mode_state_callback
        if callable(cb):
            self._start_test_dictation_run(cb, state_callback=state_cb)

    def _stop_test_dictation_from_hotkey(self) -> None:
        """Stops an active test dictation run started via hotkey."""
        state_cb = self._test_hotkey_mode_state_callback
        self._stop_test_dictation_run(state_callback=state_cb)

    def _stop_recording_from_hotkey(self) -> None:
        """Stops recording and updates onboarding test UI if needed."""
        if self._test_hotkey_mode_enabled and self._test_run_active:
            self._stop_test_dictation_from_hotkey()
            return
        self._stop_recording()

    def start_test_dictation(self, callback) -> None:
        """Starts a one-off test dictation run (no auto-paste).

        The transcript is delivered via `callback(transcript, error)`.
        """
        self._start_test_dictation_run(callback)

    def stop_test_dictation(self) -> None:
        """Stops an active test dictation run."""
        self._stop_test_dictation_run()

    def cancel_test_dictation(self) -> None:
        """Cancels an active test dictation run and discards the result.

        Use this when navigating away from the test step to prevent
        stale results from updating the UI.
        """
        self._stop_test_dictation_run(discard_result=True)

    def _start_test_dictation_run(self, callback, *, state_callback=None) -> None:
        """Internal helper to start a safe test dictation run."""
        if self._recording:
            self._safe_call(callback, "", "Already recording.")
            return
        if self._worker_thread is not None and self._worker_thread.is_alive():
            self._safe_call(
                callback,
                "",
                "PulseScribe is busy — please wait a moment and try again.",
            )
            return

        if callable(state_callback):
            self._safe_call(state_callback, "recording")

        self._test_run_active = True
        self._test_run_callback = callback
        self._start_recording(run_mode_override="local")

    def _stop_test_dictation_run(
        self, *, state_callback=None, discard_result: bool = False
    ) -> None:
        """Internal helper to stop an active test dictation run.

        Args:
            state_callback: Optional callback for state updates.
            discard_result: If True, clears the callback to prevent stale results
                from being delivered (used when navigating away from test step).
        """
        if callable(state_callback):
            self._safe_call(state_callback, "stopping")
        if discard_result:
            # Clear callback to prevent stale results after step change.
            self._test_run_callback = None
        self._stop_recording()

    def _finish_test_run(self, transcript: str, error: str | None) -> None:
        cb = self._test_run_callback
        self._test_run_active = False
        self._test_run_callback = None
        if cb:
            self._safe_call(cb, transcript, error)

    def _handle_worker_error(self, err: Exception) -> None:
        if self._test_run_active:
            self._finish_test_run("", str(err))
            self._update_state(AppState.ERROR)
            get_sound_player().play("error")
            self._apply_pending_hotkey_reconfigure_if_safe()
            return

        logger.error(f"Fehler: {err}")
        emergency_log(f"Worker Exception: {err}")  # Backup log

        # API-Key-Fehler als Pop-up anzeigen
        if isinstance(err, ValueError):
            show_error_alert("API-Key fehlt", str(err))

        self._update_state(AppState.ERROR)
        get_sound_player().play("error")
        self._apply_pending_hotkey_reconfigure_if_safe()

    def _handle_transcript_result(self, transcript: str) -> None:
        """Verarbeitet das fertige Transkript: UI-Update, History, Auto-Paste."""
        # Test-Modus: Callback ausführen, kein Auto-Paste
        if self._test_run_active:
            self._finish_test_run(transcript, None)
            self._update_state(
                AppState.DONE if transcript else AppState.IDLE, transcript
            )
            self._apply_pending_hotkey_reconfigure_if_safe()
            return

        # Leeres Transkript: Nichts zu tun
        if not transcript:
            logger.warning("Leeres Transkript")
            self._update_state(AppState.IDLE)
            self._apply_pending_hotkey_reconfigure_if_safe()
            return

        # Erfolgreiche Transkription: State → Sound → UI-Flush → History → Paste
        self._update_state(AppState.DONE, transcript)
        get_sound_player().play("done")  # Sofortiges auditives Feedback
        self._flush_ui_and_wait()  # ✅ muss sichtbar sein BEVOR Text eingefügt wird
        self._save_to_history(transcript)
        self._paste_result(transcript)
        self._apply_pending_hotkey_reconfigure_if_safe()

    def _save_to_history(self, transcript: str) -> None:
        """Speichert Transkript in der Historie."""
        from utils.history import save_transcript

        try:
            save_transcript(
                transcript,
                mode=self._run_mode or self.mode,
                language=self.language,
                refined=self.refine,
            )
        except Exception as e:
            logger.debug(f"History save failed: {e}")

    # =============================================================================
    # Hold-to-record Hotkey (Push-to-talk)
    # =============================================================================

    @staticmethod
    def _parse_quartz_hotkey(hotkey_str: str) -> tuple[int, int]:
        """Parses a canonical hotkey string into (keycode, required_flags) for Quartz.

        Fn/CapsLock are handled separately via FlagsChanged taps.
        """
        from utils.hotkey import KEY_CODE_MAP

        raw = (hotkey_str or "").strip().lower()
        parts = [p.strip() for p in raw.split("+") if p.strip()]
        if not parts:
            raise ValueError("Leerer Hotkey")

        *mods, key = parts
        if key not in KEY_CODE_MAP:
            raise ValueError(f"Unbekannte Taste: {key}")

        try:
            from Quartz import (  # type: ignore[import-not-found]
                kCGEventFlagMaskAlternate,
                kCGEventFlagMaskCommand,
                kCGEventFlagMaskControl,
                kCGEventFlagMaskShift,
            )
        except Exception as e:  # pragma: no cover
            raise ValueError(f"Quartz nicht verfügbar: {e}") from e

        required_flags = 0
        for mod in mods:
            if mod in ("cmd", "command", "win"):
                required_flags |= int(kCGEventFlagMaskCommand)
            elif mod in ("ctrl", "control"):
                required_flags |= int(kCGEventFlagMaskControl)
            elif mod == "shift":
                required_flags |= int(kCGEventFlagMaskShift)
            elif mod in ("alt", "option"):
                required_flags |= int(kCGEventFlagMaskAlternate)
            else:
                raise ValueError(f"Unbekannter Modifier: {mod}")

        return int(KEY_CODE_MAP[key]), int(required_flags)

    def _start_quartz_hotkey_listener(self, hotkey_str: str, *, mode: str) -> bool:
        """Starts a Quartz event-tap based hotkey listener (macOS only)."""
        if sys.platform != "darwin":
            return False

        target_hotkey = (hotkey_str or "").strip()
        if not target_hotkey:
            return False

        try:
            keycode, required_flags = self._parse_quartz_hotkey(target_hotkey)
        except Exception as e:
            logger.error(f"Quartz Hotkey Parsing fehlgeschlagen: {e}")
            return False

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
                kCGEventKeyDown,
                kCGEventKeyUp,
                CGEventGetFlags,
                CGEventGetIntegerValueField,
                kCGKeyboardEventKeycode,
            )
        except Exception as e:  # pragma: no cover
            logger.error(f"Quartz Hotkey Tap benötigt Quartz: {e}")
            return False

        source_id = f"quartz:{mode}:{target_hotkey.lower()}"
        active = False
        pressed = False

        def callback(_proxy, event_type, event, _refcon):
            nonlocal active, pressed
            try:
                if event_type not in (
                    kCGEventKeyDown,
                    kCGEventKeyUp,
                    kCGEventFlagsChanged,
                ):
                    return event

                flags = int(CGEventGetFlags(event))
                mods_ok = (flags & required_flags) == required_flags

                if event_type == kCGEventFlagsChanged:
                    if mode == "hold" and active and not mods_ok:
                        # Modifier released while active: stop hold.
                        active = False
                        self._active_hold_sources.discard(source_id)
                        if (
                            not self._active_hold_sources
                            and self._recording_started_by_hold
                        ):
                            self._call_on_main(self._stop_recording_from_hotkey)
                    elif mode == "toggle" and not mods_ok:
                        pressed = False
                    return event

                event_keycode = int(
                    CGEventGetIntegerValueField(event, kCGKeyboardEventKeycode)
                )
                if event_keycode != keycode:
                    return event

                if mode == "hold":
                    if event_type == kCGEventKeyDown and mods_ok and not active:
                        active = True
                        self._active_hold_sources.add(source_id)
                        self._call_on_main(
                            lambda: self._start_recording_from_hold(source_id)
                        )
                    elif event_type == kCGEventKeyUp and active:
                        active = False
                        self._active_hold_sources.discard(source_id)
                        if (
                            not self._active_hold_sources
                            and self._recording_started_by_hold
                        ):
                            self._call_on_main(self._stop_recording_from_hotkey)
                else:
                    if event_type == kCGEventKeyDown and mods_ok and not pressed:
                        pressed = True
                        self._call_on_main(self._on_hotkey)
                    elif event_type == kCGEventKeyUp:
                        pressed = False
            except Exception as e:
                logger.debug(f"Quartz hotkey tap error: {e}")
            return event

        tap = CGEventTapCreate(
            kCGHIDEventTap,
            kCGHeadInsertEventTap,
            kCGEventTapOptionListenOnly,
            CGEventMaskBit(kCGEventKeyDown)
            | CGEventMaskBit(kCGEventKeyUp)
            | CGEventMaskBit(kCGEventFlagsChanged),
            callback,
            None,
        )
        if tap is None:  # pragma: no cover
            logger.error(
                "Quartz Hotkey Tap konnte nicht erstellt werden (Input Monitoring?)"
            )
            return False

        source = CFMachPortCreateRunLoopSource(None, tap, 0)
        CFRunLoopAddSource(CFRunLoopGetCurrent(), source, kCFRunLoopCommonModes)
        CGEventTapEnable(tap, True)

        # Reuse the existing tap registry for cleanup.
        self._modifier_taps.append((tap, source, callback))
        return True

    @staticmethod
    def _parse_pynput_hotkey(hotkey_str: str):
        """Parst Hotkey-String in pynput-Key-Set."""
        from pynput import keyboard  # type: ignore[import-not-found]
        from utils.hotkey import KEY_CODE_MAP

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
                # Normale Taste: prefer virtual key codes so modified characters
                # (e.g. Option+L) still match the underlying physical key.
                if part in KEY_CODE_MAP:
                    keys.add(keyboard.KeyCode.from_vk(int(KEY_CODE_MAP[part])))
                elif len(part) == 1:
                    keys.add(keyboard.KeyCode.from_char(part))
                else:
                    raise ValueError(f"Unbekannte Taste: {part}")

        return keys

    def _start_hold_hotkey_listener(self, hotkey_str: str | None = None) -> bool:
        """Startet Listener für Hold-Mode für einen Hotkey."""
        target_hotkey = (hotkey_str or self.hotkey or "").strip()
        if not target_hotkey:
            return False

        if sys.platform == "darwin":
            return self._start_quartz_hotkey_listener(target_hotkey, mode="hold")

        try:
            hotkey_keys = self._parse_pynput_hotkey(target_hotkey)
        except Exception as e:
            logger.error(f"Hotkey Parsing fehlgeschlagen: {e}")
            return False

        source_id = f"hold:{target_hotkey.lower()}"

        def on_activate() -> None:
            logger.debug("Hotkey hold down")
            self._active_hold_sources.add(source_id)
            self._call_on_main(lambda: self._start_recording_from_hold(source_id))

        def on_deactivate() -> None:
            logger.debug("Hotkey hold up")
            self._active_hold_sources.discard(source_id)
            if not self._active_hold_sources and self._recording_started_by_hold:
                self._call_on_main(self._stop_recording_from_hotkey)

        return self._start_pynput_hotkey_listener(
            hotkey_keys,
            description=f"Hold Hotkey '{target_hotkey}'",
            on_activate=on_activate,
            on_deactivate=on_deactivate,
        )

    def _start_toggle_hotkey_listener(self, hotkey_str: str) -> bool:
        """Startet Listener für Toggle-Mode (Fallback/Alternative zu Carbon)."""
        target_hotkey = (hotkey_str or "").strip()
        if not target_hotkey:
            return False

        if sys.platform == "darwin":
            return self._start_quartz_hotkey_listener(target_hotkey, mode="toggle")

        try:
            hotkey_keys = self._parse_pynput_hotkey(target_hotkey)
        except Exception as e:
            logger.error(f"Hotkey Parsing fehlgeschlagen: {e}")
            return False

        def on_activate() -> None:
            self._call_on_main(self._on_hotkey)

        return self._start_pynput_hotkey_listener(
            hotkey_keys,
            description=f"Toggle Hotkey '{target_hotkey}'",
            on_activate=on_activate,
            on_deactivate=None,
        )

    @staticmethod
    def _make_pynput_key_normalizer(keyboard):
        """Returns a function that normalizes pynput keys for hotkey matching."""
        ctrl_variants = tuple(
            k
            for k in (
                getattr(keyboard.Key, "ctrl_l", None),
                getattr(keyboard.Key, "ctrl_r", None),
            )
            if k is not None
        )
        alt_variants = tuple(
            k
            for k in (
                getattr(keyboard.Key, "alt_l", None),
                getattr(keyboard.Key, "alt_r", None),
                getattr(keyboard.Key, "alt_gr", None),
            )
            if k is not None
        )
        shift_variants = tuple(
            k
            for k in (
                getattr(keyboard.Key, "shift_l", None),
                getattr(keyboard.Key, "shift_r", None),
            )
            if k is not None
        )
        cmd_variants = tuple(
            k
            for k in (
                getattr(keyboard.Key, "cmd_l", None),
                getattr(keyboard.Key, "cmd_r", None),
                getattr(keyboard.Key, "cmd", None),
            )
            if k is not None
        )

        def normalize_key(key):
            if key in ctrl_variants:
                return keyboard.Key.ctrl
            if key in alt_variants:
                return keyboard.Key.alt
            if key in shift_variants:
                return keyboard.Key.shift
            if key in cmd_variants and key != keyboard.Key.cmd:
                return keyboard.Key.cmd
            if (
                isinstance(key, keyboard.KeyCode)
                and getattr(key, "vk", None) is not None
            ):
                return keyboard.KeyCode.from_vk(int(key.vk))
            return key

        return normalize_key

    def _start_pynput_hotkey_listener(
        self,
        hotkey_keys: set,
        *,
        description: str,
        on_activate,
        on_deactivate=None,
    ) -> bool:
        """Starts a pynput listener for a hotkey combination (best-effort)."""
        try:
            from pynput import keyboard  # type: ignore[import-not-found]
        except ImportError:
            logger.error(f"{description} benötigt pynput")
            return False

        current_keys: set = set()
        active = False
        normalize_key = self._make_pynput_key_normalizer(keyboard)

        def on_press(key):
            nonlocal active
            if self._current_state == AppState.ERROR:
                return
            nk = normalize_key(key)
            current_keys.add(nk)
            if not active and hotkey_keys.issubset(current_keys):
                active = True
                self._safe_call(on_activate)

        def on_release(key):
            nonlocal active
            nk = normalize_key(key)
            current_keys.discard(nk)
            if active and not hotkey_keys.issubset(current_keys):
                active = False
                if callable(on_deactivate):
                    self._safe_call(on_deactivate)

        try:
            listener = keyboard.Listener(on_press=on_press, on_release=on_release)
            listener.daemon = True
            listener.start()
        except Exception as e:
            logger.error(f"{description} Listener konnte nicht gestartet werden: {e}")
            return False

        self._pynput_listeners.append(listener)
        return True

    def _start_recording(self, *, run_mode_override: str | None = None) -> None:
        """Startet Streaming-Aufnahme im Worker-Thread."""
        # Sicherstellen, dass kein alter Worker noch läuft.
        #
        # WICHTIG: Nicht im Main-Thread blocken (join), sonst friert UI/Overlay ein.
        # Wenn ein Worker noch läuft, sind wir "busy" (z.B. Deepgram finalize/close).
        # In dem Fall starten wir keinen neuen Recording-Run.
        if self._worker_thread is not None and self._worker_thread.is_alive():
            logger.warning(
                "Worker-Thread läuft noch – Recording wird nicht neu gestartet"
            )
            if self._stop_event is not None:
                self._stop_event.set()
            return

        self._recording = True
        self._update_state(AppState.LISTENING)

        # Interim-Datei löschen, um veralteten Text zu vermeiden
        INTERIM_FILE.unlink(missing_ok=True)

        # Neues Stop-Event für diese Aufnahme
        self._stop_event = threading.Event()

        effective_mode = run_mode_override or self.mode

        # Modus-Entscheidung: Streaming vs. Recording
        use_streaming = effective_mode == "deepgram" and get_env_bool_default(
            "PULSESCRIBE_STREAMING", True
        )
        self._run_mode = effective_mode

        if use_streaming:
            target = self._streaming_worker
            name = "StreamingWorker"
            logger.info("Starte Deepgram Streaming...")
        else:
            target = self._recording_worker
            name = "RecordingWorker"
            logger.info(f"Starte Standard-Aufnahme (Mode: {effective_mode})...")

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
        max_rms = 0.0
        had_speech = False
        player = get_sound_player()

        try:
            # Ready-Sound
            player.play("ready")

            # Aufnahme-Loop
            def callback(indata, frames, time, status):
                nonlocal max_rms, had_speech
                recorded_chunks.append(indata.copy())
                # RMS Berechnung und Queueing
                rms = float(np.sqrt(np.mean(indata**2)))
                if rms > max_rms:
                    max_rms = rms
                if rms > VAD_THRESHOLD:
                    had_speech = True
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
                # Leeres Ergebnis signalisieren, damit Result-Polling sauber endet.
                self._result_queue.put(
                    DaemonMessage(type=MessageType.TRANSCRIPT_RESULT, payload="")
                )
                return
            if not had_speech:
                logger.info(
                    f"Keine Sprache erkannt (max_rms={max_rms:.4f}) – Transkription übersprungen"
                )
                self._result_queue.put(
                    DaemonMessage(type=MessageType.TRANSCRIPT_RESULT, payload="")
                )
                return

            audio_data = np.concatenate(recorded_chunks)

            # Silence-Trimming (reduziert Zeit/Kosten bei allen Providern)
            raw_duration = 0.0
            audio_duration = 0.0
            if hasattr(audio_data, "shape"):
                raw_duration = float(audio_data.shape[0]) / WHISPER_SAMPLE_RATE
                # Wichtig: VAD_THRESHOLD ist fürs Triggern optimiert und kann am Ende
                # zu aggressiv sein (leise Ausklinger). Für Trimming nehmen wir
                # einen konservativeren, dynamischen Threshold basierend auf max_rms.
                trim_threshold = VAD_THRESHOLD * 0.5
                if max_rms > 0:
                    trim_threshold = min(trim_threshold, max_rms * 0.03)
                trimmed_audio = self._trim_silence(
                    audio_data,
                    trim_threshold,
                    WHISPER_SAMPLE_RATE,
                    pad_s=0.25,
                )
                trimmed_duration = float(trimmed_audio.shape[0]) / WHISPER_SAMPLE_RATE
                if trimmed_duration < raw_duration - 0.05:
                    logger.info(
                        f"Trimmed silence: raw={raw_duration:.2f}s -> trimmed={trimmed_duration:.2f}s"
                    )

                audio_duration = trimmed_duration
                audio_data = trimmed_audio

                # Lokales Whisper profitiert oft von etwas künstlicher "End-Silence",
                # damit letzte Wörter stabiler dekodiert werden.
                mode_for_run = self._run_mode or self.mode
                if mode_for_run == "local":
                    tail_s = 0.2
                    tail_samples = int(WHISPER_SAMPLE_RATE * tail_s)
                    if tail_samples > 0:
                        audio_data = np.concatenate(
                            [audio_data, np.zeros(tail_samples, dtype=np.float32)]
                        )
                        audio_duration += tail_s

            # Temp-File erstellen
            fd, temp_path = tempfile.mkstemp(suffix=".wav")
            os.close(fd)

            try:
                # Update State: Transcribing
                # (via Queue nicht direkt möglich, aber _stop_recording setzt es im Main-Thread)

                # Transkribieren via Provider
                mode_for_run = self._run_mode or self.mode
                provider = self._get_provider(mode_for_run)
                t0 = time.perf_counter()
                try:
                    if mode_for_run == "local" and hasattr(
                        provider, "transcribe_audio"
                    ):
                        transcript = provider.transcribe_audio(
                            audio_data, model=self.model, language=self.language
                        )
                    else:
                        sf.write(temp_path, audio_data, WHISPER_SAMPLE_RATE)
                        transcript = provider.transcribe(
                            Path(temp_path), model=self.model, language=self.language
                        )
                except Exception as e:
                    # Best-effort fallback to local transcription for non-streaming modes
                    # (e.g. missing API keys, provider downtime).
                    if mode_for_run != "local":
                        logger.warning(
                            f"Provider '{mode_for_run}' fehlgeschlagen ({e}). Fallback auf local..."
                        )
                        provider = self._get_provider("local")
                        if hasattr(provider, "transcribe_audio"):
                            transcript = provider.transcribe_audio(
                                audio_data,
                                # Don't pass provider-specific model names (e.g. 'nova-3').
                                model=None,
                                language=self.language,
                            )
                            mode_for_run = "local"
                        else:
                            raise
                    else:
                        raise
                t_transcribe = time.perf_counter() - t0
                if audio_duration > 0:
                    rtf = t_transcribe / audio_duration
                    model_name = self._model_name_for_logging(
                        provider, mode_override=mode_for_run
                    )
                    backend_name = self._local_backend_for_logging(
                        provider, mode_override=mode_for_run
                    )
                    backend_info = f", backend={backend_name}" if backend_name else ""
                    logger.info(
                        f"Transcription performance: mode={mode_for_run}{backend_info}, "
                        f"model={model_name}, "
                        f"audio={audio_duration:.2f}s, time={t_transcribe:.2f}s, rtf={rtf:.2f}x"
                    )
                else:
                    logger.info(
                        f"Transcription performance: mode={mode_for_run}, "
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
                    logger.info(
                        f"Refine performance: provider={self.refine_provider}, time={t_refine:.2f}s"
                    )

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
        """Stoppt Aufnahme (non-blocking) und lässt Worker im Hintergrund auslaufen."""
        if not self._recording:
            return

        logger.info("Stop-Event setzen...")

        self._stop_interim_polling()

        # Signal an Worker: Beende Deepgram-Stream sauber
        if self._stop_event:
            self._stop_event.set()

        # Wichtig: Nicht join() im Main-Thread, sonst blockiert der UI-RunLoop.
        # Deepgram-Streaming hat beim Shutdown typischerweise ~2s Close-Latenz.
        self._start_worker_joiner()

        self._recording = False
        self._recording_started_by_hold = False
        # Nur wenn wir noch im Aufnahme-Flow sind, auf TRANSCRIBING wechseln.
        # Bei sehr kurzen Hold-Taps kann der Worker bereits ein leeres Ergebnis geliefert
        # und den State auf IDLE gesetzt haben.
        if self._current_state in (AppState.LISTENING, AppState.RECORDING):
            self._update_state(AppState.TRANSCRIBING)

        # Polling läuft bereits seit Start

    def _start_worker_joiner(self) -> None:
        """Joint den aktiven Worker in einem Background-Thread und räumt Referenzen auf."""
        worker = self._worker_thread
        if worker is None or not worker.is_alive():
            return

        def _join_and_cleanup() -> None:
            try:
                worker.join()
            except Exception as e:  # pragma: no cover
                logger.debug(f"Worker join failed: {e}")
            self._call_on_main(lambda: self._cleanup_finished_worker(worker))

        threading.Thread(
            target=_join_and_cleanup,
            daemon=True,
            name="WorkerJoiner",
        ).start()

    def _cleanup_finished_worker(self, worker: threading.Thread) -> None:
        """Räumt Worker-Referenzen auf, falls es noch der aktuelle Worker ist."""
        if self._worker_thread is not worker:
            return
        if self._worker_thread is not None and self._worker_thread.is_alive():
            return
        self._worker_thread = None
        self._stop_event = None
        self._apply_pending_hotkey_reconfigure_if_safe()

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
                        self._handle_worker_error(result)
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
                            transcript = str(result.payload or "")
                            self._handle_transcript_result(transcript)
                            return

                    # Safety Break nach zu vielen Messages pro Tick, um UI nicht zu blockieren
                    if processed_count > 50:
                        break

            except queue.Empty:
                pass

        # Etwas schnelleres Polling für direkteres UI-Feedback (Wellen/Level)
        self._result_timer = NSTimer.scheduledTimerWithTimeInterval_repeats_block_(
            0.03, True, lambda _: check_result()
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
        from utils.preferences import (
            get_show_welcome_on_startup,
            is_onboarding_complete,
        )

        # Wizard zeigen wenn Onboarding nicht abgeschlossen (Step != DONE)
        if not is_onboarding_complete():
            self._show_onboarding_wizard(show_settings_after=True)
        elif get_show_welcome_on_startup():
            self._show_welcome_window()

        # Callback für Menubar "Settings..." setzen
        if self._menubar:
            self._menubar.set_welcome_callback(self._show_welcome_window)

    def _show_onboarding_wizard(self, *, show_settings_after: bool) -> None:
        """Zeigt den separaten Setup-Wizard."""
        from ui import OnboardingWizardController

        # Settings-Window verstecken während Wizard läuft
        if self._welcome is not None:
            try:
                self._welcome.hide()
            except Exception:
                pass

        self._onboarding_wizard = OnboardingWizardController(
            persist_progress=bool(show_settings_after)
        )
        self._onboarding_wizard.set_on_settings_changed(self._reload_settings)

        wizard = self._onboarding_wizard
        wizard.set_test_dictation_callbacks(
            start=lambda: self.start_test_dictation(wizard.on_test_dictation_result),
            stop=self.stop_test_dictation,
            cancel=self.cancel_test_dictation,
        )
        wizard.set_test_hotkey_mode_callbacks(
            enable=lambda: self.enable_test_hotkey_mode(
                wizard.on_test_dictation_result,
                state_callback=wizard.on_test_dictation_hotkey_state,
            ),
            disable=self.disable_test_hotkey_mode,
        )
        wizard.set_on_complete(self._show_welcome_window)
        self._onboarding_wizard.show()

    def _show_welcome_window(self) -> None:
        """Zeigt Welcome Window (via Menubar)."""
        from utils.preferences import is_onboarding_complete
        from ui import WelcomeController

        # Don't open settings while wizard is visible (defensive check).
        if self._onboarding_wizard is not None:
            wizard_window = getattr(self._onboarding_wizard, "_window", None)
            if wizard_window is not None:
                try:
                    if wizard_window.isVisible():
                        # Wizard is open - just bring it to front instead.
                        wizard_window.makeKeyAndOrderFront_(None)
                        return
                except Exception:
                    pass

        # Während des First-Run-Wizards kein Settings-Window öffnen.
        if not is_onboarding_complete():
            self._show_onboarding_wizard(show_settings_after=True)
            return

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
            self._welcome.set_onboarding_wizard_callback(
                lambda: self._show_onboarding_wizard(show_settings_after=False)
            )
        else:
            # Ensure callback is present even when reusing the window instance.
            self._welcome.set_onboarding_wizard_callback(
                lambda: self._show_onboarding_wizard(show_settings_after=False)
            )
        self._welcome.show()

    def _reload_settings(self) -> None:
        """Lädt Settings aus .env neu und wendet sie an."""
        from config import DEFAULT_REFINE_MODEL
        from utils.preferences import read_env_file

        # .env neu laden (override=True um Änderungen zu übernehmen)
        load_environment(override_existing=True)
        env_values = read_env_file()

        old_hotkey_signature = self._hotkey_bindings_signature(
            self._resolve_hotkey_bindings()
        )

        # WICHTIG: python-dotenv setzt Variablen, entfernt sie aber nicht wenn ein Key
        # aus der Datei gelöscht wurde. Das ist relevant, weil die Settings-UI einige
        # Defaults über "Key entfernen" abbildet (z.B. local backend = whisper).
        # Daher synchronisieren wir ausgewählte Keys explizit mit der .env Datei.
        for key in (
            # Hotkeys
            "PULSESCRIBE_HOTKEY",
            "PULSESCRIBE_HOTKEY_MODE",
            "PULSESCRIBE_TOGGLE_HOTKEY",
            "PULSESCRIBE_HOLD_HOTKEY",
            # Local options
            "PULSESCRIBE_LOCAL_BACKEND",
            "PULSESCRIBE_LOCAL_MODEL",
            "PULSESCRIBE_LANGUAGE",
            "PULSESCRIBE_DEVICE",
            "PULSESCRIBE_FP16",
            "PULSESCRIBE_LOCAL_FAST",
            "PULSESCRIBE_LOCAL_BEAM_SIZE",
            "PULSESCRIBE_LOCAL_BEST_OF",
            "PULSESCRIBE_LOCAL_TEMPERATURE",
            "PULSESCRIBE_LOCAL_COMPUTE_TYPE",
            "PULSESCRIBE_LOCAL_CPU_THREADS",
            "PULSESCRIBE_LOCAL_NUM_WORKERS",
            "PULSESCRIBE_LOCAL_WITHOUT_TIMESTAMPS",
            "PULSESCRIBE_LOCAL_VAD_FILTER",
            "PULSESCRIBE_LOCAL_WARMUP",
            # Optional keys that can be removed in UI to reset to default.
            "PULSESCRIBE_REFINE_MODEL",
        ):
            value = env_values.get(key)
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

        # Hotkeys übernehmen (apply immediately; legacy values kept unless explicitly set)
        self.toggle_hotkey = (
            env_values.get("PULSESCRIBE_TOGGLE_HOTKEY") or ""
        ).strip() or None
        self.hold_hotkey = (
            env_values.get("PULSESCRIBE_HOLD_HOTKEY") or ""
        ).strip() or None
        if "PULSESCRIBE_HOTKEY" in env_values:
            self.hotkey = (env_values.get("PULSESCRIBE_HOTKEY") or "").strip() or None
        if "PULSESCRIBE_HOTKEY_MODE" in env_values:
            self.hotkey_mode = (
                env_values.get("PULSESCRIBE_HOTKEY_MODE") or ""
            ).strip() or self.hotkey_mode

        # Settings aktualisieren
        new_mode = env_values.get("PULSESCRIBE_MODE")
        if new_mode:
            self.mode = new_mode

        new_language = env_values.get("PULSESCRIBE_LANGUAGE")
        self.language = new_language  # None ist valid für "auto"

        refine_flag = parse_bool(env_values.get("PULSESCRIBE_REFINE"))
        if refine_flag is not None:
            self.refine = refine_flag

        new_refine_provider = env_values.get("PULSESCRIBE_REFINE_PROVIDER")
        if new_refine_provider:
            self.refine_provider = new_refine_provider

        new_refine_model = env_values.get("PULSESCRIBE_REFINE_MODEL")
        self.refine_model = new_refine_model or DEFAULT_REFINE_MODEL

        # Lokalen Provider nicht wegwerfen (Modell-Load ist teuer).
        # Stattdessen Runtime-Konfig invalidieren, damit ENV-Änderungen greifen.
        local_provider = self._provider_cache.get("local")
        if local_provider is not None:
            invalidate = getattr(local_provider, "invalidate_runtime_config", None)
            if callable(invalidate):
                try:
                    invalidate()
                except Exception as e:
                    logger.warning(
                        f"LocalProvider invalidate_runtime_config fehlgeschlagen: {e}"
                    )
            else:
                # Fallback: alte Implementierung (sicher, aber langsamer)
                del self._provider_cache["local"]

        logger.info(
            f"Settings reloaded: mode={self.mode}, language={self.language}, "
            f"refine={self.refine}, refine_provider={self.refine_provider}, "
            f"refine_model={self.refine_model}"
        )

        # Hotkeys ggf. neu registrieren (ohne Neustart)
        new_hotkey_signature = self._hotkey_bindings_signature(
            self._resolve_hotkey_bindings()
        )
        if new_hotkey_signature != old_hotkey_signature:
            if self._is_hotkey_reconfigure_busy():
                logger.info(
                    f"Hotkeys geändert ({old_hotkey_signature} → {new_hotkey_signature}) – apply after current run…"
                )
                self._pending_hotkey_reconfigure = True
            else:
                logger.info(
                    f"Hotkeys geändert ({old_hotkey_signature} → {new_hotkey_signature}) – re-register…"
                )
                self._reconfigure_hotkeys(show_alerts=True)

        # Falls lokal aktiviert, Modell im Hintergrund vorladen
        self._preload_local_model_async()

    def _is_hotkey_reconfigure_busy(self) -> bool:
        """True if it's unsafe to unregister/re-register hotkeys right now."""
        if self._recording:
            return True
        worker = self._worker_thread
        if worker is not None and worker.is_alive():
            return True
        return False

    def _apply_pending_hotkey_reconfigure_if_safe(self) -> None:
        if not self._pending_hotkey_reconfigure:
            return
        if self._is_hotkey_reconfigure_busy():
            return
        self._pending_hotkey_reconfigure = False
        try:
            self._reconfigure_hotkeys(show_alerts=True)
        except Exception as e:  # pragma: no cover
            logger.warning(f"Deferred hotkey reconfigure fehlgeschlagen: {e}")

    def _resolve_hotkey_bindings(self) -> list[tuple[str, str]]:
        """Ermittelt Hotkey-Bindings (mode, hotkey) inkl. Backwards-Compat."""
        bindings: list[tuple[str, str]] = []

        toggle_hk = (self.toggle_hotkey or "").strip()
        hold_hk = (self.hold_hotkey or "").strip()

        if toggle_hk or hold_hk:
            if toggle_hk:
                bindings.append(("toggle", toggle_hk))
            if hold_hk:
                bindings.append(("hold", hold_hk))
            return bindings

        # Fallback: altes Single-Hotkey-Setup
        legacy_hotkey = (self.hotkey or "").strip()
        legacy_mode = (self.hotkey_mode or "toggle").lower()
        if legacy_hotkey:
            if legacy_mode not in ("toggle", "hold"):
                legacy_mode = "toggle"
            bindings.append((legacy_mode, legacy_hotkey))
        return bindings

    @staticmethod
    def _hotkey_bindings_signature(
        bindings: list[tuple[str, str]],
    ) -> tuple[tuple[str, str], ...]:
        """Normalisierte Signatur für Bindings-Vergleiche."""
        normalized: list[tuple[str, str]] = []
        for mode, hk in bindings:
            m = (mode or "toggle").strip().lower()
            key = (hk or "").strip().lower()
            if not key:
                continue
            if m not in ("toggle", "hold"):
                m = "toggle"
            normalized.append((m, key))
        return tuple(normalized)

    def _unregister_all_hotkeys(self) -> None:
        """Stoppt alle registrierten Hotkeys/Listener (best-effort)."""
        # Toggle hotkeys (Carbon / best-effort)
        for handler in list(self._toggle_hotkey_handlers):
            unregister = getattr(handler, "unregister", None)
            if callable(unregister):
                try:
                    unregister()
                except Exception:
                    pass
        self._toggle_hotkey_handlers.clear()

        # pynput listeners (hold + toggle fallback)
        for listener in list(self._pynput_listeners):
            stop = getattr(listener, "stop", None)
            if callable(stop):
                try:
                    stop()
                except Exception:
                    pass
        self._pynput_listeners.clear()

        # Modifier taps (Quartz)
        if self._modifier_taps:
            try:
                from Quartz import (  # type: ignore[import-not-found]
                    CGEventTapEnable,
                    CFMachPortInvalidate,
                    CFRunLoopGetCurrent,
                    CFRunLoopRemoveSource,
                    kCFRunLoopCommonModes,
                )

                run_loop = CFRunLoopGetCurrent()
                for tap, source, _callback in list(self._modifier_taps):
                    try:
                        CGEventTapEnable(tap, False)
                    except Exception:
                        pass
                    try:
                        CFRunLoopRemoveSource(run_loop, source, kCFRunLoopCommonModes)
                    except Exception:
                        pass
                    try:
                        CFMachPortInvalidate(tap)
                    except Exception:
                        pass
            except Exception:
                pass

        self._modifier_taps.clear()
        self._fn_active = False
        self._caps_active = False
        self._active_hold_sources.clear()
        self._recording_started_by_hold = False

    def _register_hotkeys_from_current_settings(self, *, show_alerts: bool) -> None:
        """Registriert Hotkeys anhand der aktuellen Settings (toggle/hold + legacy)."""
        bindings = self._resolve_hotkey_bindings()
        if not bindings:
            logger.warning("Kein Hotkey konfiguriert – Hotkeys deaktiviert")
            if show_alerts:
                try:
                    show_error_alert(
                        "Kein Hotkey konfiguriert",
                        "Es ist kein Hotkey gesetzt. PulseScribe läuft ohne Hotkey.\n\n"
                        "Öffne Settings, um einen Hotkey zu wählen.",
                    )
                except Exception:
                    pass
            return

        normalized: list[tuple[str, str]] = []
        for mode, hk in bindings:
            m = (mode or "toggle").lower()
            if m not in ("toggle", "hold"):
                logger.warning(f"Unbekannter Hotkey-Modus '{m}', fallback auf toggle")
                m = "toggle"
            normalized.append((m, hk))

        # Doppelte Hotkeys entfernen (z.B. gleicher Key für Toggle und Hold)
        deduped: list[tuple[str, str]] = []
        seen_keys: dict[str, str] = {}
        duplicate_msgs: list[str] = []
        for m, hk in normalized:
            key_norm = hk.strip().lower()
            if not key_norm:
                continue
            if key_norm in seen_keys:
                msg = (
                    f"Hotkey '{hk}' ist doppelt konfiguriert "
                    f"({seen_keys[key_norm]} + {m}). Nur der erste wird verwendet."
                )
                logger.warning(msg)
                duplicate_msgs.append(msg)
                continue
            seen_keys[key_norm] = m
            deduped.append((m, hk))

        # Berechtigungen prüfen (ohne modale Alerts während Settings-Änderungen)
        input_monitoring_granted = check_input_monitoring_permission(show_alert=False)

        invalid_hotkeys: list[str] = []
        invalid_hotkeys.extend(duplicate_msgs)

        for mode, hk in deduped:
            hk_str = hk.strip().lower()
            hk_is_fn = hk_str == "fn"
            hk_is_capslock = hk_str in ("capslock", "caps_lock")

            if not input_monitoring_granted and (
                mode == "hold" or hk_is_fn or hk_is_capslock
            ):
                msg = f"Hotkey '{hk}' benötigt Eingabemonitoring‑Zugriff – deaktiviert."
                logger.warning(msg)
                invalid_hotkeys.append(msg)
                continue

            if hk_is_fn:
                logger.info(
                    f"Hotkey aktiviert: fn (Globe), mode={mode} (Quartz FlagsChanged Tap)"
                )
                if not self._start_fn_hotkey_monitor(mode):
                    logger.error("Fn Hotkey Monitor konnte nicht gestartet werden.")
                continue

            if hk_is_capslock:
                logger.info(
                    f"Hotkey aktiviert: capslock, mode={mode} (Quartz FlagsChanged Tap)"
                )
                if not self._start_capslock_hotkey_monitor(mode):
                    logger.error(
                        "CapsLock Hotkey Monitor konnte nicht gestartet werden."
                    )
                continue

            if mode == "toggle":

                def _handler() -> None:
                    self._on_hotkey()

                try:
                    virtual_key, modifier_mask = parse_hotkey(hk)
                except ValueError as e:
                    msg = f"Hotkey '{hk}' ungültig: {e}"
                    logger.error(msg)
                    invalid_hotkeys.append(msg)
                    continue

                from utils.carbon_hotkey import CarbonHotKeyRegistration

                reg = CarbonHotKeyRegistration(
                    virtual_key=virtual_key,
                    modifier_mask=modifier_mask,
                    callback=_handler,
                )
                ok, err = reg.register()
                if not ok:
                    # Fallback: Quartz event tap (requires Input Monitoring).
                    if input_monitoring_granted and self._start_toggle_hotkey_listener(
                        hk
                    ):
                        listener_kind = (
                            "quartz" if sys.platform == "darwin" else "pynput"
                        )
                        logger.info(f"Hotkey aktiviert: {hk} (toggle, {listener_kind})")
                        continue

                    msg = f"Hotkey '{hk}' konnte nicht registriert werden: {err}"
                    logger.error(msg)
                    invalid_hotkeys.append(msg)
                    continue

                logger.info(f"Hotkey aktiviert: {hk} (toggle, carbon)")
                self._toggle_hotkey_handlers.append(reg)
            else:
                listener_kind = "quartz" if sys.platform == "darwin" else "pynput"
                logger.info(f"Hotkey aktiviert: {hk} (hold, {listener_kind})")
                if not self._start_hold_hotkey_listener(hk):
                    msg = (
                        f"Hold Hotkey Listener für '{hk}' konnte nicht gestartet werden "
                        "und wurde deaktiviert."
                    )
                    logger.error(msg)
                    invalid_hotkeys.append(msg)
                    # No fallback: keep semantics predictable.

        if show_alerts and invalid_hotkeys:
            non_permission_msgs = [
                m for m in invalid_hotkeys if not is_permission_related_message(m)
            ]
            if not non_permission_msgs:
                return
            try:
                show_error_alert(
                    "Ungültige Hotkey‑Konfiguration",
                    "Ein oder mehrere Hotkeys konnten nicht aktiviert werden:\n\n"
                    + "\n".join(f"- {m}" for m in non_permission_msgs)
                    + "\n\nÖffne Settings, um das zu korrigieren.",
                )
            except Exception:
                pass

    def _reconfigure_hotkeys(self, *, show_alerts: bool) -> None:
        """Re-register hotkeys at runtime."""
        # Carbon + Quartz hotkey registration touches AppKit/HIToolbox and must run
        # on the main thread on recent macOS versions.
        try:
            from Foundation import NSThread  # type: ignore[import-not-found]

            if not NSThread.isMainThread():
                self._call_on_main(
                    lambda: self._reconfigure_hotkeys(show_alerts=show_alerts)
                )
                return
        except Exception:
            pass

        self._unregister_all_hotkeys()
        self._register_hotkeys_from_current_settings(show_alerts=show_alerts)

    def run(self) -> None:
        """Startet Daemon (blockiert)."""
        from AppKit import NSApplication  # type: ignore[import-not-found]
        from Foundation import NSTimer  # type: ignore[import-not-found]
        import signal

        # NSApplication initialisieren
        app = NSApplication.sharedApplication()

        # Dock-Icon: Konfigurierbar via ENV (default: an)
        # 0 = Regular (Dock-Icon), 1 = Accessory (kein Dock-Icon)
        show_dock = get_env_bool_default("PULSESCRIBE_DOCK_ICON", True)
        app.setActivationPolicy_(0 if show_dock else 1)

        # Application Menu erstellen (für CMD+Q Support wenn Dock-Icon aktiv)
        if show_dock:
            self._setup_app_menu(app)

        # UI-Controller initialisieren
        logger.info("Initialisiere UI-Controller...")
        self._menubar = MenuBarController()
        self._overlay = OverlayController()
        logger.info("UI-Controller bereit")

        # Vocabulary beim Start validieren und ggf. warnen
        try:
            from utils.vocabulary import validate_vocabulary

            vocab_issues = validate_vocabulary()
            if vocab_issues:
                from utils.alerts import show_error_alert

                show_error_alert(
                    "Probleme in der Vocabulary",
                    "Es wurden Probleme in ~/.pulsescribe/vocabulary.json gefunden:\n\n"
                    + "\n".join(f"- {issue}" for issue in vocab_issues)
                    + "\n\nÖffne Settings, um das zu korrigieren.",
                )
        except Exception:
            pass

        # Welcome Window (beim ersten Start oder wenn aktiviert)
        self._show_welcome_if_needed()

        # Hotkeys ermitteln (für Start-Info im Terminal)
        bindings_for_info = self._resolve_hotkey_bindings()

        # Berechtigungen prüfen (keine modalen Popups; UI handled via Permissions page)
        if not check_microphone_permission(show_alert=False):
            logger.warning(
                "Mikrofon-Berechtigung fehlt – PulseScribe läuft, aber Aufnahmen funktionieren nicht."
            )

        # Accessibility prüfen (nur Logging; Auto-Paste kann ohne Permission nicht funktionieren)
        check_accessibility_permission(show_alert=False)

        # Logging + Start-Info
        print("🎤 pulsescribe_daemon läuft", file=sys.stderr)
        if self.toggle_hotkey or self.hold_hotkey:
            if self.toggle_hotkey:
                print(f"   Toggle Hotkey: {self.toggle_hotkey}", file=sys.stderr)
            if self.hold_hotkey:
                print(f"   Hold Hotkey: {self.hold_hotkey}", file=sys.stderr)
        elif bindings_for_info:
            print(f"   Hotkey: {bindings_for_info[0][1]}", file=sys.stderr)
            print(f"   Hotkey Mode: {bindings_for_info[0][0]}", file=sys.stderr)
        else:
            print("   Hotkey: (none)", file=sys.stderr)
        if show_dock:
            print("   Beenden: CMD+Q (wenn fokussiert) oder Ctrl+C", file=sys.stderr)
        else:
            print("   Beenden: Menubar-Icon → Quit oder Ctrl+C", file=sys.stderr)

        # Lokales Modell vorab laden (falls aktiv)
        self._preload_local_model_async()

        # Hotkeys registrieren (zentral, auch für Runtime-Reconfigure)
        self._reconfigure_hotkeys(show_alerts=True)

        # FIX: Ctrl+C Support
        # 1. Dummy-Timer, damit der Python-Interpreter regelmäßig läuft und Signale prüft
        NSTimer.scheduledTimerWithTimeInterval_repeats_block_(0.1, True, lambda _: None)

        # 2. Signal-Handler, der die App sauber beendet
        def signal_handler(sig, frame):
            app.terminate_(None)

        signal.signal(signal.SIGINT, signal_handler)

        app.run()


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
        description="pulsescribe_daemon – Unified Daemon für PulseScribe",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Beispiele:
  %(prog)s                          # Mit Defaults aus .env
  %(prog)s --hotkey fn              # Fn/Globe als Hotkey
  %(prog)s --hotkey cmd+shift+r     # Tastenkombination
  %(prog)s --refine                 # Mit LLM-Nachbearbeitung
        """,
    )

    parser.add_argument(
        "--hotkey",
        default=None,
        help="Hotkey (default: PULSESCRIBE_HOTKEY oder 'fn')",
    )
    parser.add_argument(
        "--toggle-hotkey",
        default=None,
        help="Toggle-Hotkey (default: PULSESCRIBE_TOGGLE_HOTKEY)",
    )
    parser.add_argument(
        "--hold-hotkey",
        default=None,
        help="Hold-Hotkey (default: PULSESCRIBE_HOLD_HOTKEY)",
    )
    parser.add_argument(
        "--hotkey-mode",
        choices=["toggle", "hold"],
        default=None,
        help="Hotkey-Modus: toggle oder hold (default: PULSESCRIBE_HOTKEY_MODE)",
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
        help="Transkriptions-Modus (default: PULSESCRIBE_MODE)",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Deepgram-Modell (default: nova-3)",
    )
    parser.add_argument(
        "--refine",
        action="store_true",
        default=get_env_bool_default("PULSESCRIBE_REFINE", False),
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
    env_hotkey = os.getenv("PULSESCRIBE_HOTKEY")
    hotkey = args.hotkey or env_hotkey or "fn"
    hotkey_mode = args.hotkey_mode or os.getenv("PULSESCRIBE_HOTKEY_MODE", "toggle")
    toggle_hotkey = args.toggle_hotkey or os.getenv("PULSESCRIBE_TOGGLE_HOTKEY")
    hold_hotkey = args.hold_hotkey or os.getenv("PULSESCRIBE_HOLD_HOTKEY")

    # New default for fresh installs: Fn/Globe as hold hotkey
    if (
        toggle_hotkey is None
        and hold_hotkey is None
        and args.hotkey is None
        and env_hotkey is None
    ):
        hold_hotkey = "fn"
    language = args.language or os.getenv("PULSESCRIBE_LANGUAGE")
    model = args.model or os.getenv("PULSESCRIBE_MODEL")
    mode_env = os.getenv("PULSESCRIBE_MODE")
    mode = args.mode or mode_env or "deepgram"

    # Demo/first-success default: if no mode is configured and no API keys are present,
    # start in local mode so the app works immediately without setup.
    if args.mode is None and mode_env is None:
        has_any_api_key = bool(
            os.getenv("DEEPGRAM_API_KEY")
            or os.getenv("OPENAI_API_KEY")
            or os.getenv("GROQ_API_KEY")
        )
        if not has_any_api_key:
            mode = "local"
            # Avoid passing provider-specific model names (e.g. Deepgram model) into local mode.
            if args.model is None:
                model = None
            os.environ.setdefault("PULSESCRIBE_LOCAL_MODEL", DEFAULT_LOCAL_MODEL)
            if os.getenv("PULSESCRIBE_LOCAL_BACKEND") is None:
                try:
                    import importlib.util
                    import platform

                    is_arm = platform.machine() in ("arm64", "aarch64")
                    has_mlx = importlib.util.find_spec("mlx_whisper") is not None
                    if is_arm and has_mlx:
                        os.environ["PULSESCRIBE_LOCAL_BACKEND"] = "mlx"
                        os.environ.setdefault("PULSESCRIBE_LOCAL_FAST", "true")
                    else:
                        os.environ["PULSESCRIBE_LOCAL_BACKEND"] = "whisper"
                except Exception:
                    os.environ["PULSESCRIBE_LOCAL_BACKEND"] = "whisper"

    # Daemon starten
    try:
        daemon = PulseScribeDaemon(
            hotkey=hotkey,
            language=language,
            model=model,
            refine=args.refine,
            refine_model=args.refine_model,
            refine_provider=args.refine_provider,
            context=args.context,
            mode=mode,
            hotkey_mode=hotkey_mode,
            toggle_hotkey=toggle_hotkey,
            hold_hotkey=hold_hotkey,
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
