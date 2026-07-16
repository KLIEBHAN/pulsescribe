# ruff: noqa: E402

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
from collections import deque
from pathlib import Path

# Projekt-Root zum Path hinzufügen
PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))

# .env ZUERST laden (vor Logging-Setup, damit PULSESCRIBE_DEBUG wirkt)
from utils.env import load_environment, parse_bool


def _env_flag(raw_value: str | None, *, default: bool) -> bool:
    """Parse common bool env variants while preserving an explicit default."""
    parsed = parse_bool(raw_value)
    return default if parsed is None else parsed


load_environment()

# Logging Setup (nach .env, damit PULSESCRIBE_DEBUG aus .env funktioniert)
from utils.logging import setup_logging, get_logger

setup_logging(debug=_env_flag(os.getenv("PULSESCRIBE_DEBUG"), default=False))
logger = get_logger()

# Imports nach Logging-Setup
from utils.state import AppState
from utils.hold_state import HoldHotkeyState
from utils.hotkey import paste_transcript
from utils.timing import redacted_text_summary
from utils.hotkey_windows import hotkeys_conflict, parse_windows_hotkey_for_pynput
from utils.subprocess_io import start_stream_drain_thread
from utils.audio_latency import (
    WINDOWS_AUDIO_BLOCK_MS,
    create_low_latency_input_stream,
    windows_audio_blocksize,
)
from utils.windows_latency_diagnostics import (
    WindowsLatencyRun,
    start_windows_latency_run,
)
from utils.windows_responsiveness import apply_windows_responsiveness_boost
from whisper_platform import get_clipboard, get_sound_player
from config import (
    INTERIM_FILE,
    WARM_STREAM_QUEUE_SIZE,
    get_input_device,
    get_windows_stop_grace_seconds,
)
from providers import get_provider
from ui.daemon_status_feedback import (
    build_daemon_status_label,
    build_daemon_tray_title,
    infer_daemon_status_error,
)

# Lazy imports für optionale Features
pystray = None
PIL_Image = None
PIL_ImageDraw = None
WindowsOverlayController = None


def _friendly_error_status_text(error: Exception | str) -> str:
    return build_daemon_status_label(
        AppState.ERROR,
        infer_daemon_status_error(error) or error,
        max_chars=80,
    )


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
        from ui import overlay_windows as _overlay_windows

        if not getattr(_overlay_windows, "TK_AVAILABLE", True):
            raise ImportError(
                str(
                    getattr(
                        _overlay_windows,
                        "_TK_IMPORT_ERROR",
                        "tkinter unavailable",
                    )
                )
            )

        WindowsOverlayController = _overlay_windows.WindowsOverlayController
        logger.info("Overlay: Tkinter (Fallback)")
        return True
    except ImportError as e:
        logger.debug(f"Overlay nicht verfügbar: {e}")
        return False


def _is_reload_event_path(src_path: str) -> bool:
    return src_path.endswith(".env") or src_path.endswith(".reload")


def _unlink_reload_signal_file(signal_file: Path) -> None:
    if not signal_file.exists():
        return
    try:
        signal_file.unlink()
    except Exception:
        pass


# =============================================================================
# Hotkey-Helpers (Modul-Level für Wiederverwendung und Testbarkeit)
# =============================================================================

# Virtual Key Codes für Buchstaben A-Z (Windows)
# Mit Ctrl+Alt gedrückt wird 'r' als <82> erkannt, nicht als 'r'
_VK_TO_CHAR = {vk: chr(vk + 32) for vk in range(65, 91)}  # 65='A' -> 'a', etc.

# Debounce-Zeit in Sekunden (verhindert Doppel-Trigger)
_HOTKEY_DEBOUNCE_SEC = 0.3

# Ab dieser Queue-Wartezeit (ms) wird eine verzögerte Hotkey-Aktion geloggt.
# Hilft bei der Diagnose, falls Übergänge trotz Dispatch träge wirken.
_HOTKEY_DISPATCH_SLOW_MS = 100.0

# Timeout für "stale" Keys (Sekunden) - Keys älter als dies werden entfernt
_KEY_STALE_TIMEOUT_SEC = 2.0

# VAD Threshold: Audio-Level ab dem Sprache erkannt wird
# REST-Modus verwendet Peak (max), Streaming verwendet RMS (niedriger)
_VAD_THRESHOLD_PEAK = 0.01  # Für REST-Modus (float32 peak)
_VAD_THRESHOLD_RMS = 0.003  # Für Streaming-Modus (RMS/INT16_MAX)

# Tail-Padding: Stille am Ende des Audio (verhindert abgeschnittene Wörter bei Whisper)
_TAIL_PADDING_SEC = 0.2

# Pre-Roll: kurze Audio-Historie vor dem Hotkey-Start, damit sofortiges Sprechen
# nicht im ersten Warm-Stream-Callback-Fenster verloren geht.
_WARM_STREAM_PREROLL_SEC = 0.25

# Provider die gecached werden sollen (stateful, z.B. Model-Caching)
_STATEFUL_PROVIDERS = {"local"}
_LOCAL_PROVIDER_RELOAD_ENV_KEYS = (
    "PULSESCRIBE_LOCAL_BACKEND",
    "PULSESCRIBE_LOCAL_MODEL",
    "PULSESCRIBE_MODEL",
    "PULSESCRIBE_DEVICE",
    "PULSESCRIBE_FP16",
    "PULSESCRIBE_LOCAL_COMPUTE_TYPE",
    "PULSESCRIBE_LOCAL_CPU_THREADS",
    "PULSESCRIBE_LOCAL_NUM_WORKERS",
    "PULSESCRIBE_LIGHTNING_BATCH_SIZE",
    "PULSESCRIBE_LIGHTNING_QUANT",
)

# Default-Hotkeys (verwendet bei Startup und Reload wenn nichts konfiguriert)
_DEFAULT_TOGGLE_HOTKEY = "ctrl+alt+r"
_DEFAULT_HOLD_HOTKEY = "ctrl+win"

# Shutdown-Timeout für FileWatcher und andere Komponenten (Sekunden)
# Kurz gehalten für schnelles Beenden - Daemon-Threads werden automatisch beendet
_SHUTDOWN_TIMEOUT_SEC = 0.1

# Wie lange der DONE-State (grünes Feedback) sichtbar bleibt, bevor zurück zu
# IDLE gewechselt wird. Kurz genug, damit der Übergang snappy wirkt, aber lang
# genug als Erfolgs-Bestätigung. Sollte zum Overlay-Hold (FEEDBACK_DISPLAY_MS)
# passen.
_DONE_DISPLAY_HOLD_SEC = 0.6


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
        AppState.NO_SPEECH: (255, 177, 66),  # Amber
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
        self._state_generation = 0  # Inkrementiert bei jedem State-Update
        self._last_hotkey_time = 0.0  # Für Debouncing
        self._last_was_refined: bool = (
            False  # Ob Refinement den Text tatsächlich verändert hat
        )

        # Hold-Mode State (wie macOS)
        self._hold_state = HoldHotkeyState()

        # Hotkey-Aktionen laufen NIE im pynput-Callback: pynput ruft Callbacks
        # auf Windows synchron aus dem Low-Level-Keyboard-Hook auf. Blockierende
        # Arbeit dort (Tray, Sounds, Thread-Joins) verzögert die Event-
        # Verarbeitung und kann systemweite Eingabe-Lags verursachen.
        self._hotkey_action_queue: queue.Queue = queue.Queue()
        self._hotkey_action_thread: threading.Thread | None = None
        self._hotkey_action_thread_lock = threading.Lock()

        # Tray-Updates (Shell_NotifyIcon) sind gelegentlich langsam und laufen
        # deshalb coalesced (latest-wins) in einem eigenen Worker-Thread.
        self._tray_update_signal = threading.Event()
        self._tray_update_thread: threading.Thread | None = None

        # Components
        self._tray = None
        self._last_status_text: str | None = None
        self._hotkey_listeners: list = []  # Mehrere Listener (toggle + hold)
        self._recording_thread = None
        self._recording_action_lock = threading.RLock()  # Start/Stop atomar halten
        self._stop_event = threading.Event()  # App beenden
        self._recording_stop_event = threading.Event()  # Recording stoppen
        self._prewarm_complete = threading.Event()  # Pre-Warm abgeschlossen
        self._mic_ready = threading.Event()  # Warm-Stream bereit für instant Recording
        self._overlay = None
        self._settings_process = None  # Subprocess für Settings-Fenster
        self._onboarding_process = None  # Subprocess für Onboarding-Wizard
        self._ipc_server = None  # IPC-Server für Wizard-Kommunikation
        self._ipc_test_cmd_id: str | None = None  # Aktiver IPC-Test-Command
        self._event_loop = None  # Fallback wenn Warm-WebSocket deaktiviert ist
        self._deepgram_connection_manager = None
        self._latency_run: WindowsLatencyRun | None = None
        self._run_mode: str | None = None  # Snapshot: Modus pro Recording-Run
        self._run_streaming: bool | None = None  # Snapshot: Streaming pro Run

        # Watchdog für hängende Transcription (wie macOS)
        self._transcribing_timeout = 30.0  # Sekunden
        self._transcribing_watchdog: threading.Timer | None = None
        self._watchdog_lock = threading.Lock()
        self._watchdog_token = 0

        # Audio buffer für REST-Modus
        self._audio_buffer = []
        self._audio_sample_rate = 16000  # Default, wird in _recording_loop aktualisiert
        self._audio_lock = threading.Lock()

        # ═══════════════════════════════════════════════════════════════════
        # WARM-STREAM: Mikrofon läuft immer, instant-start beim Hotkey
        # ═══════════════════════════════════════════════════════════════════
        self._warm_stream = None  # sd.InputStream (läuft dauerhaft)
        self._warm_stream_armed = threading.Event()  # Wenn gesetzt: Samples sammeln
        self._warm_stream_draining = (
            threading.Event()
        )  # Erlaubt Sammeln während Drain-Phase
        self._warm_stream_preroll_lock = threading.Lock()
        self._warm_stream_preroll: deque[bytes] = deque(maxlen=4)
        # Queue mit maxsize: via PULSESCRIBE_WARM_STREAM_QUEUE_SIZE (default: 300)
        # Verhindert Memory Leak wenn Forwarder nicht läuft
        self._warm_stream_queue: queue.Queue[bytes] = queue.Queue(
            maxsize=WARM_STREAM_QUEUE_SIZE
        )
        self._warm_stream_sample_rate = 16000  # Wird beim Start aktualisiert
        self._is_prewarm_loading = False  # Unterscheidet Pre-Warm von Recording LOADING

        # Provider-Cache (wichtig für LocalProvider - cached Modelle intern)
        self._provider_cache: dict[str, object] = {}
        self._provider_cache_lock = threading.Lock()

        # Settings-Reload (FileWatcher + Polling-Fallback)
        self._env_observer = None
        self._reload_polling_thread: threading.Thread | None = None
        self._reload_polling_stop = threading.Event()
        self._reload_signal_file: Path | None = None
        self._reload_settings_lock = threading.Lock()
        self._hotkey_listener_lock = threading.RLock()
        # Inkrementiert bei jedem Hotkey-Restart/Shutdown.
        # Alte Listener-Callbacks erkennen so, dass sie stale sind.
        self._hotkey_listener_generation = 0

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

    def _commit_state(
        self,
        state: AppState,
        text: str | None = None,
        *,
        expected_state: AppState | None = None,
        expected_generation: int | None = None,
    ) -> tuple[AppState, str | None, int] | None:
        """Atomarer (bedingter) State-Commit unter _state_lock.

        Check und Commit laufen in EINEM kritischen Abschnitt – nur so können
        verzögerte Callbacks (IDLE-Timer, Watchdog) nicht zwischen ihrem Guard
        und dem Schreiben von einer parallel gestarteten neuen Aufnahme
        überholt werden. Side-Effects publiziert der Caller anschließend über
        _publish_state_change() außerhalb des Locks.

        Returns:
            (old_state, old_text, neue Generation) bei Erfolg, sonst None.
        """
        with self._state_lock:
            if expected_state is not None and self._state != expected_state:
                return None
            if (
                expected_generation is not None
                and self._state_generation != expected_generation
            ):
                return None
            old_state = self._state
            old_text = self._last_status_text
            self._state = state
            self._last_status_text = text
            self._state_generation += 1
            return old_state, old_text, self._state_generation

    def _publish_state_change(
        self,
        old_state: AppState,
        old_text: str | None,
        state: AppState,
        text: str | None,
        *,
        watch_transcribing: bool = True,
    ) -> None:
        """Publiziert Overlay/Log/Watchdog/Tray für einen committeten Wechsel.

        Darf NICHT unter _state_lock oder _watchdog_lock laufen (Watchdog-
        Management nimmt _watchdog_lock, Tray/Overlay sind potenziell langsam).
        """
        state_changed = old_state != state
        text_changed = old_text != text
        if state_changed or text_changed:
            # Perceived latency matters most on hotkey/VAD transitions: update the
            # overlay before logging/pystray, because file IO and tray title/icon
            # updates can be noticeably slower on Windows.
            self._overlay_update_state(state.name, text)

        if state_changed:
            logger.info(f"State: {old_state.value} → {state.value}")

            # Watchdog-Management (wie macOS)
            if state == AppState.TRANSCRIBING and watch_transcribing:
                self._start_transcribing_watchdog()
            elif state in (
                AppState.DONE,
                AppState.NO_SPEECH,
                AppState.ERROR,
                AppState.IDLE,
            ):
                self._stop_transcribing_watchdog()
        elif state == AppState.TRANSCRIBING and text_changed and watch_transcribing:
            # Streaming stop shows TRANSCRIBING immediately without starting the
            # watchdog. When Deepgram actually returns and text changes, start it.
            self._start_transcribing_watchdog()

        if state_changed or text_changed:
            self._request_tray_update()

    def _set_state(
        self,
        state: AppState,
        text: str | None = None,
        *,
        watch_transcribing: bool = True,
    ) -> int:
        """Setzt State und aktualisiert Tray-Icon + Overlay.

        Returns:
            Die neue State-Generation. Caller, die einen verzögerten
            IDLE-Rückfall planen, müssen DIESE Generation übergeben – ein
            späterer Snapshot könnte bereits zu einer neuen Aufnahme gehören.
        """
        committed = self._commit_state(state, text)
        assert committed is not None  # unconditional commit
        old_state, old_text, new_generation = committed
        self._publish_state_change(
            old_state,
            old_text,
            state,
            text,
            watch_transcribing=watch_transcribing,
        )
        return new_generation

    def _overlay_update_state(self, state: str, text: str | None = None) -> None:
        """Best-effort Overlay-State-Update ohne check-then-use Race."""
        overlay = self._overlay
        if overlay is None:
            return
        try:
            overlay.update_state(state, text)
        except Exception as e:
            logger.debug(f"Overlay state update failed: {e}")

    def _overlay_update_audio_level(self, level: float) -> None:
        """Best-effort Overlay-Level-Update ohne check-then-use Race."""
        overlay = self._overlay
        if overlay is None:
            return
        try:
            overlay.update_audio_level(level)
        except Exception as e:
            logger.debug(f"Overlay level update failed: {e}")

    def _overlay_update_interim_text(self, text: str) -> None:
        """Best-effort direct interim update; avoids file-polling latency."""
        overlay = self._overlay
        if overlay is None or not hasattr(overlay, "update_interim_text"):
            return
        try:
            overlay.update_interim_text(text)
        except Exception as e:
            logger.debug(f"Overlay interim update failed: {e}")

    def _start_latency_run(self, *, mode: str, streaming: bool) -> None:
        self._latency_run = start_windows_latency_run(
            mode=mode,
            streaming=streaming,
            logger=logger,
        )
        self._latency_mark("start_recording")

    def _latency_mark(self, name: str, **fields) -> None:
        run = self._latency_run
        if run is not None:
            run.mark(name, **fields)

    def _latency_mark_once(self, name: str, **fields) -> None:
        run = self._latency_run
        if run is not None:
            run.mark_once(name, **fields)

    def _latency_event(self, name: str, fields: dict | None = None) -> None:
        run = self._latency_run
        if run is not None:
            run.event(name, fields)

    def _latency_finish(self, outcome: str, **fields) -> None:
        run = self._latency_run
        if run is None:
            return
        run.finish(outcome, **fields)
        self._latency_run = None

    def _stop_overlay(self) -> None:
        """Stoppt Overlay atomar gegenüber parallelen Worker-Updates."""
        overlay = self._overlay
        self._overlay = None
        if overlay is None:
            return
        try:
            overlay.stop()
        except Exception as e:
            logger.debug(f"Overlay stop failed: {e}")

    def _schedule_idle_if_state_unchanged(
        self,
        delay_seconds: float,
        expected_generation: int | None = None,
    ) -> None:
        """Setzt State nach Delay nur auf IDLE, wenn es keinen Zwischenwechsel gab.

        Args:
            delay_seconds: Wartezeit bis zum IDLE-Rückfall.
            expected_generation: Generation des States, der zurückfallen soll.
                MUSS übergeben werden, wenn zwischen dem State-Wechsel und diesem
                Aufruf Arbeit liegt (z.B. Paste/History nach DONE): DONE ist
                startfähig, ein Snapshot zur Aufrufzeit könnte sonst die
                Generation einer bereits neu gestarteten Aufnahme erfassen und
                diese fälschlich auf IDLE zurücksetzen.
        """
        if expected_generation is None:
            with self._state_lock:
                expected_generation = self._state_generation

        def _set_idle_if_unchanged() -> None:
            # Guard und Commit atomar: Zwischen einem separaten Generationstest
            # und _set_state() könnte sonst eine neue Aufnahme starten und vom
            # stale Timer auf IDLE zurückgesetzt werden.
            committed = self._commit_state(
                AppState.IDLE,
                expected_generation=expected_generation,
            )
            if committed is None:
                return
            old_state, old_text, _generation = committed
            self._publish_state_change(old_state, old_text, AppState.IDLE, None)

        timer = threading.Timer(delay_seconds, _set_idle_if_unchanged)
        timer.daemon = True
        timer.start()

    def _enter_no_speech_state(self, *, delay_seconds: float = 1.2) -> None:
        """Show a brief neutral no-speech result before returning to ready."""
        generation = self._set_state(AppState.NO_SPEECH)
        self._schedule_idle_if_state_unchanged(delay_seconds, generation)

    def _handle_no_speech_result(self, log_message: str = "Leeres Transkript") -> None:
        """Surface a short no-speech retry hint without changing core behavior."""
        logger.warning(log_message)
        # Latency-Run VOR dem Wechsel in einen startfähigen State beenden,
        # damit ein sofortiger neuer Hotkey-Start nicht mit diesem Run kollidiert.
        if self._ipc_test_cmd_id and self._ipc_server:
            # Preserve the existing onboarding / IPC empty-result behavior.
            self._latency_finish("no_speech", ipc=True)
            self._set_state(AppState.IDLE)
            return
        self._latency_finish("no_speech")
        self._enter_no_speech_state()

    def _start_transcribing_watchdog(self):
        """Startet Watchdog-Timer für hängende Transcription."""

        def timeout_handler():
            # Token-Check und ERROR-Commit atomar koppeln (watchdog → state
            # Lock-Ordnung): Ein stale Timer darf eine Aufnahme, die nach dem
            # Token-Check DONE erreicht und neu gestartet wurde, nicht mehr
            # auf ERROR setzen. Nur TRANSCRIBING darf zu ERROR werden.
            with self._watchdog_lock:
                # Ignore stale timer callbacks from previous runs.
                if timer_token != self._watchdog_token:
                    return
                committed = self._commit_state(
                    AppState.ERROR,
                    "Transcription timed out",
                    expected_state=AppState.TRANSCRIBING,
                )

            if committed is not None:
                old_state, old_text, generation = committed
                logger.error(
                    f"Transcription-Timeout nach {self._transcribing_timeout}s"
                )
                # Side-Effects außerhalb beider Locks publizieren
                # (_stop_transcribing_watchdog nimmt _watchdog_lock erneut).
                self._publish_state_change(
                    old_state, old_text, AppState.ERROR, "Transcription timed out"
                )
                self._play_sound("error")
                self._schedule_idle_if_state_unchanged(2.0, generation)

        with self._watchdog_lock:
            if self._transcribing_watchdog is not None:
                self._transcribing_watchdog.cancel()
                self._transcribing_watchdog = None

            self._watchdog_token += 1
            timer_token = self._watchdog_token

            timer = threading.Timer(self._transcribing_timeout, timeout_handler)
            timer.daemon = True
            self._transcribing_watchdog = timer

        timer.start()

    def _stop_transcribing_watchdog(self):
        """Stoppt Watchdog-Timer."""
        with self._watchdog_lock:
            timer = self._transcribing_watchdog
            self._transcribing_watchdog = None
            self._watchdog_token += 1

        if timer is not None:
            timer.cancel()

    def _request_tray_update(self) -> None:
        """Plant ein coalesced Tray-Update (latest-wins, nie im Caller-Thread).

        pystray-Updates (Shell_NotifyIcon) können auf Windows sporadisch
        blockieren. Damit State-Übergänge davon nie ausgebremst werden, setzt
        der Caller nur ein Event; der Tray-Worker liest den aktuellsten State.
        """
        self._tray_update_signal.set()

    def _start_tray_update_worker(self) -> None:
        if self._tray_update_thread is not None and self._tray_update_thread.is_alive():
            return
        self._tray_update_thread = threading.Thread(
            target=self._tray_update_worker,
            daemon=True,
            name="TrayUpdateWorker",
        )
        self._tray_update_thread.start()

    def _tray_update_worker(self) -> None:
        while not self._stop_event.is_set():
            self._tray_update_signal.wait()
            if self._stop_event.is_set():
                return
            self._tray_update_signal.clear()
            self._apply_tray_update_from_state()

    def _apply_tray_update_from_state(self) -> None:
        """Wendet den aktuellsten State/Text auf das Tray an (latest-wins)."""
        with self._state_lock:
            state = self._state
            text = self._last_status_text
        try:
            self._update_tray_icon(state, text)
        except Exception as e:
            logger.debug(f"Tray-Update fehlgeschlagen: {e}")

    def _update_tray_icon(self, state: AppState, text: str | None = None):
        """Aktualisiert Tray-Icon basierend auf State + Status-Text."""
        if self._tray is None or PIL_Image is None or PIL_ImageDraw is None:
            return

        color = self.COLORS.get(state, (128, 128, 128))
        icon = self._create_icon(color)
        self._tray.icon = icon
        self._tray.title = build_daemon_tray_title(state, text)

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
            [
                mic_left,
                mic_top + mic_width // 2,
                mic_right,
                mic_bottom - mic_width // 2,
            ],
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
            [
                center_x - stand_width // 2,
                stand_top,
                center_x + stand_width // 2,
                stand_bottom - 3,
            ],
            fill=color,
        )
        # Fuß (horizontale Linie)
        foot_width = 16
        draw.rectangle(
            [
                center_x - foot_width // 2,
                stand_bottom - 3,
                center_x + foot_width // 2,
                stand_bottom,
            ],
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
            with self._provider_cache_lock:
                if mode not in self._provider_cache:
                    self._provider_cache[mode] = get_provider(mode)
                return self._provider_cache[mode]
        # Stateless Provider: kein Caching nötig (API-Calls)
        return get_provider(mode)

    def _get_transcription_config(
        self, mode: str | None = None
    ) -> tuple[str | None, str]:
        """Gibt (model, language) für Transkription zurück.

        Zentralisiert die Konfigurationslogik für alle Provider-Modi.
        Local-Mode verwendet PULSESCRIBE_LOCAL_MODEL (default: base),
        andere Modi verwenden PULSESCRIBE_MODEL (default: Provider-spezifisch).
        """
        mode_for_config = mode or self.mode
        language = os.getenv("PULSESCRIBE_LANGUAGE", "auto")
        if mode_for_config == "local":
            # Default "base" für Windows (schneller als turbo)
            model = os.getenv("PULSESCRIBE_LOCAL_MODEL", "base")
        else:
            # None = Provider-Default (z.B. nova-3 für Deepgram)
            model = os.getenv("PULSESCRIBE_MODEL")
        return model, language

    def _get_deepgram_streaming_config(self) -> tuple[str, str]:
        model, language = self._get_transcription_config("deepgram")
        return model or "nova-3", language

    @staticmethod
    def _deepgram_warm_websocket_enabled() -> bool:
        return _env_flag(
            os.getenv("PULSESCRIBE_DEEPGRAM_WARM_WEBSOCKET"),
            default=True,
        )

    def _windows_stop_grace_seconds(self) -> float:
        """Return configured Windows capture tail after hotkey release."""
        return get_windows_stop_grace_seconds()

    def _wait_for_windows_stop_grace(self, phase: str) -> None:
        """Keep a non-warm input stream open briefly after stop."""
        grace_seconds = self._windows_stop_grace_seconds()
        if grace_seconds <= 0:
            return

        logger.debug(f"Windows Stop-Grace ({phase}): {grace_seconds:.2f}s")
        deadline = time.monotonic() + grace_seconds
        while not self._stop_event.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(0.02, remaining))

    def _mark_mic_ready(self) -> None:
        """Mark warm mic capture ready, independently from optional prewarm work."""
        if self._mic_ready.is_set():
            return

        self._mic_ready.set()
        if not self._is_prewarm_loading:
            return

        should_play_ready = False
        if self.state == AppState.LOADING:
            self._set_state(AppState.IDLE)
            should_play_ready = True

        # Flip the startup flag only after the visible state is no longer
        # LOADING. This avoids a tiny race where a hotkey sees LOADING but can no
        # longer promote it to ready.
        self._is_prewarm_loading = False
        if should_play_ready:
            self._play_sound("ready")

    def _promote_prewarm_ready_if_possible(self) -> bool:
        """Allow hotkeys as soon as the warm microphone stream is ready."""
        if not self._mic_ready.is_set():
            return False

        if self.state == AppState.LOADING:
            self._set_state(AppState.IDLE)
        self._is_prewarm_loading = False
        return True

    def _enter_recording_from_audio_callback(self) -> None:
        """Switch LISTENING → RECORDING from the audio callback with minimal work.

        The sounddevice callback must stay lean.  Avoid synchronous tray updates
        and info-level file logging here; the overlay wave is the feedback that
        needs to be immediate.
        """
        if (
            self._commit_state(AppState.RECORDING, expected_state=AppState.LISTENING)
            is None
        ):
            return

        self._latency_mark("recording_state")
        self._overlay_update_state(AppState.RECORDING.name, None)
        # Tray darf hier nur signalisiert werden (Event.set ist O(1));
        # das eigentliche Shell_NotifyIcon-Update macht der Tray-Worker.
        self._request_tray_update()
        logger.debug("State: listening → recording (audio callback)")

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

        # Kleine Chunks: schnellere VAD-/Overlay-Reaktion als die alten ~64ms.
        blocksize = windows_audio_blocksize(sample_rate)
        preroll_chunk_count = max(
            1,
            int((_WARM_STREAM_PREROLL_SEC * sample_rate + blocksize - 1) // blocksize),
        )
        with self._warm_stream_preroll_lock:
            self._warm_stream_preroll = deque(maxlen=preroll_chunk_count)

        def audio_callback(indata, frames, time_info, status):
            """Audio-Callback: Samples sammeln wenn armed, sonst verwerfen."""
            if status:
                logger.debug(f"Warm-Stream Status: {status}")

            # RMS immer berechnen (für VAD, unabhängig von Overlay)
            rms = float(np.sqrt(np.mean(indata.astype(np.float32) ** 2)) / INT16_MAX)

            # Audio-Level für Overlay (optional)
            self._overlay_update_audio_level(rms)

            if self._warm_stream_armed.is_set():
                self._latency_mark_once("first_audio_callback")

            # VAD: State-Transition LISTENING → RECORDING (nur wenn armed)
            if self._warm_stream_armed.is_set():
                current_state = self.state
                if current_state == AppState.LISTENING and rms > _VAD_THRESHOLD_RMS:
                    logger.debug(f"VAD triggered: level={rms:.4f}")
                    self._enter_recording_from_audio_callback()

            audio_bytes = indata.tobytes()

            # Audio sammeln wenn armed ODER draining (für Drain-Phase).
            # Vor dem Hotkey halten wir nur eine kurze Pre-Roll-Historie.
            with self._warm_stream_preroll_lock:
                should_queue_audio = (
                    self._warm_stream_armed.is_set()
                    or self._warm_stream_draining.is_set()
                )
                if not should_queue_audio:
                    self._warm_stream_preroll.append(audio_bytes)
                    return

            try:
                self._warm_stream_queue.put_nowait(audio_bytes)
            except queue.Full:
                # Queue voll - Audio-Chunk verworfen (z.B. bei langer REST-Transkription)
                if not hasattr(self, "_warm_stream_overflow_logged"):
                    self._warm_stream_overflow_logged = True
                    logger.warning(
                        "Warm-Stream Queue voll, Audio-Chunks werden verworfen"
                    )

        try:
            self._warm_stream = create_low_latency_input_stream(
                sd,
                logger=logger,
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
            self._mark_mic_ready()
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

    def _prepare_warm_stream_for_recording(self) -> None:
        """Drop stale warm-stream chunks and arm capture before user feedback."""
        self._warm_stream_draining.clear()
        while not self._warm_stream_queue.empty():
            try:
                self._warm_stream_queue.get_nowait()
            except queue.Empty:
                break

        with self._warm_stream_preroll_lock:
            preroll_chunks = list(self._warm_stream_preroll)
            self._warm_stream_preroll.clear()
            for chunk in preroll_chunks:
                try:
                    self._warm_stream_queue.put_nowait(chunk)
                except queue.Full:
                    logger.warning(
                        "Warm-Stream Queue voll, Pre-Roll-Audio wurde verworfen"
                    )
                    break
            self._warm_stream_armed.set()

        if preroll_chunks:
            logger.debug(
                f"Warm-Stream Pre-Roll: {len(preroll_chunks)} Chunks übernommen"
            )

    def _on_hotkey_press(self):
        """Callback wenn Hotkey gedrückt wird (Toggle-Mode)."""
        if self.state == AppState.LOADING and self._promote_prewarm_ready_if_possible():
            self._start_recording()
        elif self.state in (AppState.IDLE, AppState.NO_SPEECH, AppState.DONE):
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

        if self.state == AppState.LOADING:
            self._promote_prewarm_ready_if_possible()

        # Bereits am Aufnehmen / noch nicht wieder startklar
        if self.state not in (AppState.IDLE, AppState.NO_SPEECH, AppState.DONE):
            logger.debug(f"Hold-Recording ignoriert: State={self.state}")
            return

        logger.debug(f"Hold-Recording starten: {source_id}")
        started = self._start_recording()

        # Flag NUR setzen wenn Recording tatsächlich gestartet
        if started:
            self._hold_state.mark_started()

    def _stop_recording_from_hotkey(self):
        """Stoppt Recording (aufgerufen bei Hold-Release).

        Wie macOS: Einheitlicher Name für Stop-Aktion von Hotkey.
        """
        if self.state in (AppState.LOADING, AppState.LISTENING, AppState.RECORDING):
            logger.debug("Hold-Release → Recording stoppen")
            self._stop_recording()  # ruft hold_state.reset() auf

    def _start_recording(self) -> bool:
        """Startet Aufnahme (Streaming oder REST)."""
        with self._recording_action_lock:
            if self._stop_event.is_set():
                logger.debug("Start ignoriert: App wird beendet")
                return False

            if self.state == AppState.LOADING:
                self._promote_prewarm_ready_if_possible()

            # Idempotenz: nur aus Ready-/Retry-/Erfolgs-Zuständen starten.
            # DONE ist startfähig, damit direkt nach einem Diktat ohne Warten
            # auf das grüne Feedback (_DONE_DISPLAY_HOLD_SEC) neu diktiert
            # werden kann.
            if self.state not in (AppState.IDLE, AppState.NO_SPEECH, AppState.DONE):
                logger.debug(f"Start ignoriert: State={self.state}")
                return False

            run_mode = self.mode
            run_streaming = self.streaming
            self._run_mode = run_mode
            self._run_streaming = run_streaming
            self._start_latency_run(mode=run_mode, streaming=run_streaming)

            logger.info(
                f"Starte Aufnahme ({'Streaming' if run_streaming else 'REST'})..."
            )

            # Recording-Stop-Event zurücksetzen
            self._recording_stop_event.clear()

            if run_streaming:
                # Prüfe ob Warm-Stream verfügbar (instant-start)
                if self._warm_stream is not None:
                    # ═══════════════════════════════════════════════════════════════
                    # WARM-STREAM MODE: Mikrofon läuft bereits, instant-start!
                    # ═══════════════════════════════════════════════════════════════
                    logger.info("Warm-Stream Mode: instant-start")

                    # Vor Ready-Sound scharf schalten, damit das erste Wort nicht
                    # im Zeitfenster zwischen Feedback und Worker-Start verloren geht.
                    self._prepare_warm_stream_for_recording()
                    self._latency_mark("warm_stream_armed")

                    # Sofort LISTENING setzen und Sound spielen
                    self._set_state(AppState.LISTENING)
                    self._latency_mark("listening_state")
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

                    # Vor Ready-Sound scharf schalten, damit das erste Wort nicht
                    # im Zeitfenster zwischen Feedback und Worker-Start verloren geht.
                    self._prepare_warm_stream_for_recording()
                    self._latency_mark("warm_stream_armed")

                    # Sofort LISTENING setzen und Sound spielen
                    self._set_state(AppState.LISTENING)
                    self._latency_mark("listening_state")
                    self._play_sound("ready")

                    # Recording-Loop mit Warm-Stream
                    self._recording_thread = threading.Thread(
                        target=self._recording_loop_warm, daemon=True
                    )
                else:
                    # Fallback: Kein Warm-Stream, Cold-Start
                    logger.warning("Kein Warm-Stream - Fallback auf Cold-Start")
                    self._set_state(AppState.LISTENING)
                    self._latency_mark("listening_state")
                    self._play_sound("ready")
                    self._recording_thread = threading.Thread(
                        target=self._recording_loop, daemon=True
                    )

            self._recording_thread.start()
            self._latency_mark("worker_thread_started")
            return True

    def _stop_recording(self):
        """Stoppt Aufnahme und startet Transkription."""
        recording_thread = None
        should_transcribe_rest = False

        with self._recording_action_lock:
            # Idempotenz: Stop nur in aktivem Aufnahme-Flow
            if self.state not in (
                AppState.LOADING,
                AppState.LISTENING,
                AppState.RECORDING,
            ):
                logger.debug(f"Stop ignoriert: State={self.state}")
                return

            logger.info("Stoppe Aufnahme...")
            self._latency_mark("stop_requested")

            # Hold-Flag zurücksetzen - egal wie Recording gestoppt wurde
            self._hold_state.reset()

            # Signal zum Stoppen (nur Recording, nicht App)
            self._recording_stop_event.set()

            run_streaming = (
                self._run_streaming
                if self._run_streaming is not None
                else self.streaming
            )
            if run_streaming:
                # Streaming: Worker beendet sich selbst via stop_event.  Die UI
                # wechselt sofort in TRANSCRIBING, während Stop-Grace/Drain/Finalize
                # im Worker weiterlaufen. Das spiegelt macOS wider und vermeidet
                # den Eindruck, die Aufnahme hinge nach Hotkey-Release noch fest.
                # Der Watchdog startet erst, wenn Deepgram wirklich zurück ist.
                self._set_state(
                    AppState.TRANSCRIBING,
                    "Finishing...",
                    watch_transcribing=False,
                )
                self._latency_mark("transcribing_state")
                # Stop-Sound sofort bei Release spielen (async/non-blocking),
                # statt erst nach der Deepgram-Finalize-Kette. Das gibt direktes
                # Feedback und lässt den Übergang snappier wirken.
                self._play_sound("stop")
                return

            # REST: State früh umschalten, damit parallele Stop-Aufrufe idempotent sind
            self._set_state(AppState.TRANSCRIBING)
            self._latency_mark("transcribing_state")
            recording_thread = self._recording_thread
            should_transcribe_rest = True

        # REST: Auf Recording-Thread warten, Stop-Sound erst nach Ende der
        # Capture-/Grace-Phase abspielen, dann transcribieren.
        if should_transcribe_rest:
            capture_finished = True
            if recording_thread and recording_thread.is_alive():
                join_timeout = 2.0 + self._windows_stop_grace_seconds()
                recording_thread.join(timeout=join_timeout)
                capture_finished = not recording_thread.is_alive()

            if capture_finished:
                self._play_sound("stop")
            else:
                logger.warning("Stop-Sound übersprungen, weil die Aufnahme noch läuft")

            threading.Thread(target=self._transcribe_rest, daemon=True).start()

    def _handle_rest_capture_error(
        self,
        error: Exception,
        status_text: str,
        *,
        capture_mode: str,
    ) -> None:
        self._set_state(AppState.ERROR, status_text)
        self._latency_finish(
            "error",
            error_type=type(error).__name__,
            phase="rest_capture",
            capture_mode=capture_mode,
        )
        self._play_sound("error")
        time.sleep(1.0)
        self._set_state(AppState.IDLE)

    def _recording_loop(self):
        """Audio-Aufnahme Loop (läuft in separatem Thread)."""
        try:
            import sounddevice as sd
            import numpy as np

            channels = 1
            chunk_duration = WINDOWS_AUDIO_BLOCK_MS / 1000

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
                rms = float(np.sqrt(np.mean(indata**2)))
                self._overlay_update_audio_level(rms)
                self._latency_mark_once("first_audio_callback")

                # State auf RECORDING setzen wenn Audio erkannt
                if self.state == AppState.LISTENING:
                    # Einfache VAD: Prüfe ob Audio über Threshold (Peak für REST)
                    if np.abs(indata).max() > _VAD_THRESHOLD_PEAK:
                        self._enter_recording_from_audio_callback()

            with create_low_latency_input_stream(
                sd,
                logger=logger,
                device=input_device,
                samplerate=actual_sample_rate,
                channels=channels,
                dtype="float32",
                callback=audio_callback,
                blocksize=int(actual_sample_rate * chunk_duration),
            ):
                while not self._recording_stop_event.is_set():
                    time.sleep(0.05)
                self._wait_for_windows_stop_grace("REST cold")

        except ImportError as e:
            logger.error("sounddevice nicht installiert")
            self._handle_rest_capture_error(
                e,
                "Microphone dependency missing",
                capture_mode="cold",
            )
        except Exception as e:
            logger.error(f"Recording-Fehler: {e}")
            self._handle_rest_capture_error(
                e,
                _friendly_error_status_text(e),
                capture_mode="cold",
            )

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
            self._prepare_warm_rest_audio_buffer()

            # Warm-Stream armen
            self._warm_stream_armed.set()
            logger.debug("Warm-Stream armed für REST-Recording")

            # Audio sammeln bis Stop-Signal plus kurze Windows-Stop-Grace.
            # VAD wird im audio_callback des Warm-Streams gehandhabt (nicht hier).
            self._collect_warm_stream_until_stop(np, INT16_MAX)

            # === IMMEDIATE-DRAIN ===
            # Race Condition Fix: Zwischen queue.get(timeout) und stop_event Check
            # könnten neue Chunks eingefügt worden sein.
            immediate_drained = self._drain_warm_stream_nowait(np, INT16_MAX)
            if immediate_drained > 0:
                logger.debug(f"REST-Mode Immediate-Drain: {immediate_drained} Chunks")

            logger.debug("Recording-Loop (Warm) beendet")

        except Exception as e:
            logger.error(f"Recording-Fehler (Warm): {e}")
            self._handle_rest_capture_error(
                e,
                _friendly_error_status_text(e),
                capture_mode="warm",
            )
        finally:
            # === DRAIN-PHASE ===
            # Wichtig: sounddevice hat noch ~23ms Audio im Buffer.
            # drain_event setzen BEVOR arm_event gelöscht wird,
            # damit Callback weiter Audio sammelt während Queue geleert wird.
            self._warm_stream_draining.set()
            self._warm_stream_armed.clear()

            try:
                # Queue leeren (max 200ms, 2 leere Polls = fertig)
                drained = self._drain_warm_stream_until_quiet(np, INT16_MAX)
                if drained > 0:
                    logger.debug(f"REST-Mode Drain: {drained} Rest-Chunks gesammelt")
            finally:
                # KRITISCH: drain_event MUSS gelöscht werden, sonst sammelt Callback ewig
                self._warm_stream_draining.clear()

    def _prepare_warm_rest_audio_buffer(self) -> None:
        with self._audio_lock:
            self._audio_buffer = []
            self._audio_sample_rate = self._warm_stream_sample_rate

    def _append_warm_stream_chunk(self, chunk: bytes, np, int16_max: int) -> None:
        audio_int16 = np.frombuffer(chunk, dtype=np.int16)
        audio_float32 = audio_int16.astype(np.float32) / int16_max
        with self._audio_lock:
            self._audio_buffer.append(audio_float32)

    def _maybe_mark_warm_stop_seen(
        self,
        stop_seen_at: float | None,
        grace_seconds: float,
    ) -> float | None:
        if stop_seen_at is not None or not self._recording_stop_event.is_set():
            return stop_seen_at
        stop_seen_at = time.monotonic()
        if grace_seconds > 0:
            logger.debug(f"Windows Stop-Grace (REST warm): {grace_seconds:.2f}s")
        return stop_seen_at

    @staticmethod
    def _warm_stop_grace_elapsed(
        stop_seen_at: float | None,
        grace_seconds: float,
    ) -> bool:
        return (
            stop_seen_at is not None
            and time.monotonic() - stop_seen_at >= grace_seconds
        )

    def _try_collect_warm_stream_chunk(
        self, np, int16_max: int, *, timeout: float
    ) -> bool:
        try:
            chunk = self._warm_stream_queue.get(timeout=timeout)
        except queue.Empty:
            return False
        self._append_warm_stream_chunk(chunk, np, int16_max)
        return True

    def _collect_warm_stream_until_stop(self, np, int16_max: int) -> None:
        stop_seen_at: float | None = None
        grace_seconds = self._windows_stop_grace_seconds()
        while True:
            stop_seen_at = self._maybe_mark_warm_stop_seen(
                stop_seen_at,
                grace_seconds,
            )
            if self._warm_stop_grace_elapsed(stop_seen_at, grace_seconds):
                return
            if self._try_collect_warm_stream_chunk(np, int16_max, timeout=0.02):
                continue
            if stop_seen_at is not None and grace_seconds <= 0:
                return

    def _drain_warm_stream_nowait(self, np, int16_max: int) -> int:
        drained = 0
        while True:
            try:
                chunk = self._warm_stream_queue.get_nowait()
            except queue.Empty:
                return drained
            self._append_warm_stream_chunk(chunk, np, int16_max)
            drained += 1

    def _drain_warm_stream_until_quiet(self, np, int16_max: int) -> int:
        drain_deadline = time.monotonic() + 0.2
        empty_count = 0
        drained = 0
        while empty_count < 2 and time.monotonic() < drain_deadline:
            if self._try_collect_warm_stream_chunk(np, int16_max, timeout=0.01):
                drained += 1
                empty_count = 0
            else:
                empty_count += 1
        return drained

    def _run_deepgram_stream(
        self,
        *,
        use_cached_event_loop: bool,
        **kwargs,
    ) -> str:
        """Run Deepgram on the warm-socket loop or the existing cold fallback."""
        model, language = self._get_deepgram_streaming_config()
        manager = self._deepgram_connection_manager
        if manager is not None:
            return manager.transcribe(model, language, **kwargs)

        import asyncio

        from providers.deepgram_stream import deepgram_stream_core

        if use_cached_event_loop and self._event_loop is not None:
            loop = self._event_loop
            self._event_loop = None
        else:
            loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(
                deepgram_stream_core(model, language, **kwargs)
            )
        finally:
            loop.close()

    def _streaming_worker(self):
        """Streaming-Worker: Recording + Transcription via WebSocket."""
        logger.debug("Streaming-Worker gestartet")

        try:
            logger.debug("Starte deepgram_stream_core")

            def on_audio_level(level: float):
                self._overlay_update_audio_level(level)
                self._latency_mark_once("first_audio_callback")

                current_state = self.state
                if current_state == AppState.LOADING:
                    logger.debug("Mikrofon bereit → LISTENING")
                    self._set_state(AppState.LISTENING)
                    self._latency_mark("listening_state")
                elif current_state == AppState.LISTENING and level > _VAD_THRESHOLD_RMS:
                    logger.debug(
                        f"VAD triggered: level={level:.4f} > threshold={_VAD_THRESHOLD_RMS}"
                    )
                    self._set_state(AppState.RECORDING)
                    self._latency_mark("recording_state")

            self._latency_mark("deepgram_core_start")
            transcript = self._run_deepgram_stream(
                use_cached_event_loop=True,
                play_ready=True,
                external_stop_event=self._recording_stop_event,
                audio_level_callback=on_audio_level,
                interim_text_callback=self._overlay_update_interim_text,
                latency_event_callback=self._latency_event,
                stop_grace_seconds=self._windows_stop_grace_seconds(),
            )
            self._finish_streaming_result(transcript)
        except Exception as e:
            self._handle_streaming_error(e, "Streaming-Fehler")

    def _streaming_worker_warm(self):
        """Streaming-Worker using both the warm mic and warm websocket."""
        logger.debug("Streaming-Worker (Warm) gestartet")

        try:
            from providers.deepgram_stream import WarmStreamSource

            warm_source = WarmStreamSource(
                audio_queue=self._warm_stream_queue,
                sample_rate=self._warm_stream_sample_rate,
                arm_event=self._warm_stream_armed,
                stream=self._warm_stream,
                drain_event=self._warm_stream_draining,
            )
            logger.debug("Starte deepgram_stream_core mit Warm-Stream")
            self._latency_mark("deepgram_core_start")
            transcript = self._run_deepgram_stream(
                use_cached_event_loop=False,
                play_ready=False,
                external_stop_event=self._recording_stop_event,
                interim_text_callback=self._overlay_update_interim_text,
                latency_event_callback=self._latency_event,
                warm_stream_source=warm_source,
                stop_grace_seconds=self._windows_stop_grace_seconds(),
            )
            self._finish_streaming_result(transcript)
        except Exception as e:
            self._warm_stream_draining.set()
            self._warm_stream_armed.clear()
            self._warm_stream_draining.clear()
            self._handle_streaming_error(e, "Streaming-Fehler (Warm)")

    def _finish_streaming_result(self, transcript: str) -> None:
        self._latency_mark("deepgram_core_return", chars=len(transcript))
        logger.debug(f"Streaming abgeschlossen: {len(transcript)} Zeichen")
        if not transcript:
            self._handle_no_speech_result()
            return
        self._set_state(AppState.TRANSCRIBING)
        self._handle_result(self._maybe_refine(transcript))

    def _handle_streaming_error(self, error: Exception, label: str) -> None:
        error_type = "Import-Fehler" if isinstance(error, ImportError) else label
        logger.error(f"{error_type}: {error}")
        self._set_state(AppState.ERROR, _friendly_error_status_text(error))
        self._latency_finish("error", error_type=type(error).__name__)
        self._play_sound("error")
        time.sleep(1.0)
        self._set_state(AppState.IDLE)

    def _transcribe_rest(self, mode_override: str | None = None):
        """Transkribiert aufgenommenes Audio via REST API."""
        try:
            import numpy as np

            # Audio-Buffer zusammenfügen
            with self._audio_lock:
                if not self._audio_buffer:
                    self._handle_no_speech_result("Kein Audio aufgenommen")
                    return

                audio_data = np.concatenate(self._audio_buffer)
                sample_rate = self._audio_sample_rate
                self._audio_buffer = []

            duration = len(audio_data) / sample_rate
            logger.info(f"Transkribiere {duration:.1f}s Audio ({sample_rate}Hz)...")
            self._latency_mark("rest_transcribe_start", duration_s=round(duration, 3))

            # Konfiguration holen (zentralisiert für alle Modi)
            mode_for_run = mode_override or self._run_mode or self.mode
            model, language = self._get_transcription_config(mode_for_run)
            provider = self._get_provider(mode_for_run)

            # Local-Mode: In-Memory Transkription (kein WAV schreiben)
            if mode_for_run == "local" and hasattr(provider, "transcribe_audio"):
                from config import WHISPER_SAMPLE_RATE

                # Tail-Padding (verhindert abgeschnittene letzte Wörter bei Whisper)
                tail_samples = int(sample_rate * _TAIL_PADDING_SEC)
                audio_data = np.concatenate(
                    [audio_data, np.zeros(tail_samples, dtype=np.float32)]
                )

                # Resampling auf 16kHz (Whisper erwartet WHISPER_SAMPLE_RATE)
                if sample_rate != WHISPER_SAMPLE_RATE:
                    audio_data = _resample_audio(
                        audio_data, sample_rate, WHISPER_SAMPLE_RATE
                    )
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

            self._latency_mark("rest_transcribe_done", chars=len(transcript or ""))

            if transcript:
                transcript = self._maybe_refine(transcript)

                self._handle_result(transcript)
            else:
                self._handle_no_speech_result()

        except ImportError as e:
            logger.error(f"Import-Fehler: {e}")
            self._set_state(AppState.ERROR, _friendly_error_status_text(e))
            self._latency_finish("error", error_type=type(e).__name__)
            self._play_sound("error")
            time.sleep(1.0)
            self._set_state(AppState.IDLE)
        except Exception as e:
            logger.error(f"Transkriptions-Fehler: {e}")
            self._set_state(AppState.ERROR, _friendly_error_status_text(e))
            self._latency_finish("error", error_type=type(e).__name__)
            self._play_sound("error")
            time.sleep(1.0)
            self._set_state(AppState.IDLE)

    def _maybe_refine(self, transcript: str) -> str:
        """Wendet LLM-Refinement an (falls aktiviert) und trackt ob Text verändert wurde."""
        self._last_was_refined = False
        if not (self.refine and transcript):
            return transcript
        self._set_state(AppState.REFINING)
        self._latency_mark("refine_start")
        from refine.llm import maybe_refine_transcript

        refined = maybe_refine_transcript(
            transcript,
            refine=True,
            refine_model=self.refine_model,
            refine_provider=self.refine_provider,
            context=self.context,
        )
        self._last_was_refined = refined != transcript
        self._latency_mark("refine_done", changed=self._last_was_refined)
        return refined

    def _save_to_history(self, transcript: str, *, mode: str, refined: bool) -> None:
        """Speichert Transkript in der Historie.

        Mode/Refined kommen als Snapshots vom Caller: `self._run_mode` und
        `self._last_was_refined` können zum Speicherzeitpunkt bereits von einer
        neu gestarteten Aufnahme überschrieben worden sein (DONE ist startfähig).
        """
        from utils.history import save_transcript

        try:
            save_transcript(
                transcript,
                mode=mode,
                language=os.getenv("PULSESCRIBE_LANGUAGE", "auto"),
                refined=refined,
            )
        except Exception as e:
            logger.warning(f"History save failed: {e}")

    def _handle_result(self, transcript: str):
        """Verarbeitet Transkriptions-Ergebnis."""
        logger.info(f"Transkript: {redacted_text_summary(transcript)}")
        # Run-Metadaten und Latency-Run VOR dem DONE-State snapshotten/abkoppeln:
        # DONE ist startfähig, ein sofortiger Hotkey- oder IPC-Start darf
        # `_run_mode`, `_last_was_refined`, `_ipc_test_cmd_id` und
        # `_latency_run` überschreiben, ohne dieses Ergebnis zu verfälschen.
        # Vor DONE ist der State TRANSCRIBING/REFINING (nicht startfähig),
        # daher sind diese Snapshots race-frei.
        run = self._latency_run
        self._latency_run = None
        run_mode = self._run_mode or self.mode
        was_refined = self._last_was_refined
        ipc_cmd_id = self._ipc_test_cmd_id
        ipc_server = self._ipc_server
        if run is not None:
            run.mark("result_ready", chars=len(transcript or ""))
        done_generation = self._set_state(AppState.DONE)
        self._play_sound("done")

        # IPC-Test Mode: Route result to wizard instead of clipboard
        if ipc_cmd_id and ipc_server:
            from utils.ipc import STATUS_DONE

            ipc_server.send_response(ipc_cmd_id, STATUS_DONE, transcript=transcript)
            logger.info(f"IPC-Test Ergebnis gesendet (id={ipc_cmd_id})")
            # Nur die eigene ID zurücksetzen: Ein während send_response neu
            # gestarteter IPC-Test hat evtl. schon eine neue ID gesetzt.
            if self._ipc_test_cmd_id == ipc_cmd_id:
                self._ipc_test_cmd_id = None  # Reset for next test
            if run is not None:
                run.finish("ipc_done", chars=len(transcript or ""))

            # Nach kurzer Pause zurück zu IDLE
            self._schedule_idle_if_state_unchanged(
                _DONE_DISPLAY_HOLD_SEC, done_generation
            )
            return

        if run is not None:
            run.mark("paste_start", auto_paste=self.auto_paste)
        paste_success = True
        if self.auto_paste:
            paste_success = paste_transcript(transcript)
            if not paste_success:
                # Fallback: Nur in Clipboard kopieren
                get_clipboard().copy(transcript)
                logger.info(
                    "Text in Zwischenablage kopiert (Auto-Paste fehlgeschlagen)"
                )
        else:
            get_clipboard().copy(transcript)
            logger.info("Text in Zwischenablage kopiert")
        if run is not None:
            run.mark("paste_done", success=paste_success)

        # History-IO (inkl. möglicher Datei-Rotation) erst NACH dem Paste:
        # Der sichtbare "Text erscheint"-Moment darf nicht auf Disk-IO warten.
        self._save_to_history(transcript, mode=run_mode, refined=was_refined)
        if run is not None:
            run.finish("done", chars=len(transcript or ""), paste_success=paste_success)

        # Nach kurzer Pause zurück zu IDLE (Timer statt sleep, blockiert Thread
        # nicht). WICHTIG: mit der DONE-Generation planen – zum jetzigen
        # Zeitpunkt kann bereits eine neue Aufnahme laufen, deren Generation
        # nicht fälschlich auf IDLE zurückgesetzt werden darf.
        self._schedule_idle_if_state_unchanged(_DONE_DISPLAY_HOLD_SEC, done_generation)

    def _setup_hotkey(self):
        """Richtet globale Hotkeys ein (Toggle und/oder Hold-Mode)."""
        try:
            from pynput import keyboard

            bindings = self._resolve_windows_hotkey_bindings()
            if not bindings:
                logger.warning("Keine Hotkeys konfiguriert")
                return

            parsed_hotkeys = self._parse_windows_hotkey_bindings(bindings, keyboard)
            if not parsed_hotkeys:
                logger.error("Keine gültigen Hotkeys konfiguriert")
                return

            self._start_windows_hotkey_listener(keyboard, parsed_hotkeys)
            self._log_registered_windows_hotkeys(bindings)

        except ImportError:
            logger.error("pynput nicht installiert")
        except Exception as e:
            logger.error(f"Hotkey-Fehler: {e}")

    def _resolve_windows_hotkey_bindings(self) -> list[tuple[str, str]]:
        bindings: list[tuple[str, str]] = []
        if self.toggle_hotkey:
            bindings.append((self.toggle_hotkey, "toggle"))
        if not self.hold_hotkey:
            return bindings
        if self.toggle_hotkey and hotkeys_conflict(
            self.toggle_hotkey, self.hold_hotkey
        ):
            logger.error(
                "Hotkey-Konflikt: Hold-Hotkey überlappt mit Toggle-Hotkey "
                f"({self.hold_hotkey} vs {self.toggle_hotkey}). Hold wird ignoriert."
            )
            return bindings
        bindings.append((self.hold_hotkey, "hold"))
        return bindings

    def _parse_windows_hotkey_bindings(
        self, bindings, keyboard
    ) -> list[tuple[set, str, str]]:
        parsed_hotkeys: list[tuple[set, str, str]] = []
        for hotkey_str, mode in bindings:
            hotkey_keys = self._parse_hotkey_string(hotkey_str, keyboard)
            if not hotkey_keys:
                logger.error(f"Ungültiger Hotkey: {hotkey_str}")
                continue
            parsed_hotkeys.append((hotkey_keys, mode, f"pynput:{mode}:{hotkey_str}"))
        return parsed_hotkeys

    def _start_windows_hotkey_listener(self, keyboard, parsed_hotkeys) -> None:
        # Snapshot der aktuellen Listener-Generation. Wenn zwischenzeitlich ein
        # Restart/Shutdown passiert, ignoriert dieser Listener nachlaufende Events.
        listener_generation = self._hotkey_listener_generation
        current_keys: dict = {}

        def on_press(key):
            if listener_generation != self._hotkey_listener_generation:
                return
            self._handle_windows_hotkey_press(
                key,
                keyboard,
                parsed_hotkeys,
                current_keys,
            )

        def on_release(key):
            if listener_generation != self._hotkey_listener_generation:
                return
            self._handle_windows_hotkey_release(
                key,
                keyboard,
                parsed_hotkeys,
                current_keys,
            )

        listener = keyboard.Listener(on_press=on_press, on_release=on_release)
        listener.start()
        self._hotkey_listeners.append(listener)

    def _normalize_windows_pynput_key(self, key, keyboard):
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

        if hasattr(key, "vk") and key.vk in _VK_TO_CHAR:
            return keyboard.KeyCode.from_char(_VK_TO_CHAR[key.vk])
        if hasattr(key, "char") and key.char:
            return keyboard.KeyCode.from_char(key.char.lower())
        return key

    @staticmethod
    def _cleanup_stale_hotkey_keys(current_keys: dict, now: float) -> None:
        stale = [
            key
            for key, timestamp in current_keys.items()
            if now - timestamp > _KEY_STALE_TIMEOUT_SEC
        ]
        for key in stale:
            del current_keys[key]
            logger.debug(f"Stale Key entfernt: {key}")

    def _handle_windows_hotkey_press(
        self,
        key,
        keyboard,
        parsed_hotkeys,
        current_keys: dict,
    ) -> None:
        now = time.monotonic()
        normalized = self._normalize_windows_pynput_key(key, keyboard)
        self._cleanup_stale_hotkey_keys(current_keys, now)
        current_keys[normalized] = now

        active_keys = set(current_keys.keys())
        for hotkey_keys, mode, source_id in parsed_hotkeys:
            if not hotkey_keys.issubset(active_keys) or normalized not in hotkey_keys:
                continue
            if now - self._last_hotkey_time < _HOTKEY_DEBOUNCE_SEC:
                continue
            self._last_hotkey_time = now
            logger.debug(f"Hotkey ausgelöst: {hotkey_keys} (mode: {mode})")
            self._dispatch_windows_hotkey_match(mode, source_id, current_keys)

    def _dispatch_windows_hotkey_match(
        self,
        mode: str,
        source_id: str,
        current_keys: dict,
    ) -> None:
        if mode == "hold":
            self._maybe_start_hold_hotkey(source_id)
            return
        current_keys.clear()
        self._dispatch_hotkey_action(self._on_hotkey_press, "toggle")

    def _maybe_start_hold_hotkey(self, source_id: str) -> None:
        if self.state == AppState.LOADING and self._is_prewarm_loading:
            logger.debug("Hold-Hotkey ignoriert: Pre-Warm noch nicht abgeschlossen")
            return
        if self._hold_state.should_start(source_id):
            # Nur die billige Hold-Buchhaltung läuft im Hook-Callback; der
            # eigentliche Start (Sounds/Tray/Worker) geht in den Dispatcher.
            # _start_recording_from_hold prüft is_active() erneut und fängt
            # damit ultraschnelle Tap-Releases sauber ab.
            self._dispatch_hotkey_action(
                lambda: self._start_recording_from_hold(source_id),
                "hold-start",
            )

    def _dispatch_hotkey_action(self, action, description: str) -> None:
        """Führt Hotkey-Aktionen außerhalb des pynput-Hook-Threads aus.

        pynput ruft Callbacks auf Windows synchron aus dem Low-Level-Keyboard-
        Hook auf. Blockierende Arbeit dort verzögert die nächsten Key-Events
        (z.B. das Hold-Release) und bremst systemweit die Tastatur. Der
        FIFO-Worker erhält die Reihenfolge Start-vor-Stop.
        """
        self._ensure_hotkey_action_worker()
        self._hotkey_action_queue.put((action, description, time.monotonic()))

    def _ensure_hotkey_action_worker(self) -> None:
        thread = self._hotkey_action_thread
        if thread is not None and thread.is_alive():
            return
        with self._hotkey_action_thread_lock:
            thread = self._hotkey_action_thread
            if thread is not None and thread.is_alive():
                return
            self._hotkey_action_thread = threading.Thread(
                target=self._hotkey_action_worker,
                daemon=True,
                name="HotkeyActionWorker",
            )
            self._hotkey_action_thread.start()

    def _hotkey_action_worker(self) -> None:
        while True:
            item = self._hotkey_action_queue.get()
            if item is None:
                return
            action, description, enqueued_at = item
            queued_ms = (time.monotonic() - enqueued_at) * 1000
            if queued_ms >= _HOTKEY_DISPATCH_SLOW_MS:
                logger.debug(
                    f"Hotkey-Aktion '{description}' wartete {queued_ms:.0f}ms in der Queue"
                )
            try:
                action()
            except Exception as e:
                logger.error(f"Hotkey-Aktion '{description}' fehlgeschlagen: {e}")

    def _handle_windows_hotkey_release(
        self,
        key,
        keyboard,
        parsed_hotkeys,
        current_keys: dict,
    ) -> None:
        normalized = self._normalize_windows_pynput_key(key, keyboard)
        current_keys.pop(normalized, None)
        active_keys = set(current_keys.keys())

        for hotkey_keys, mode, source_id in parsed_hotkeys:
            if mode != "hold" or not self._hold_state.is_active(source_id):
                continue
            if hotkey_keys.issubset(active_keys):
                continue
            logger.debug(f"Hotkey losgelassen: {normalized}")
            if self._hold_state.should_stop(source_id):
                # Stop kann im REST-Modus auf den Recording-Thread warten -
                # niemals im Hook-Callback blockieren.
                self._dispatch_hotkey_action(
                    self._stop_recording_from_hotkey, "hold-stop"
                )

    @staticmethod
    def _log_registered_windows_hotkeys(bindings: list[tuple[str, str]]) -> None:
        for hotkey_str, mode in bindings:
            logger.info(f"Hotkey registriert: {hotkey_str} ({mode.capitalize()}-Mode)")

    @staticmethod
    def _parse_hotkey_string(hotkey_str: str, keyboard) -> set:
        """Parst Hotkey-String zu Set von pynput Keys."""
        return parse_windows_hotkey_for_pynput(hotkey_str, keyboard)

    def _setup_tray(self):
        """Richtet Tray-Icon ein."""
        if not _load_tray_dependencies():
            logger.warning("Tray-Icon deaktiviert (pystray/Pillow nicht verfügbar)")
            return

        current_state = self.state
        current_text = self._last_status_text
        icon = self._create_icon(
            self.COLORS.get(current_state, self.COLORS[AppState.IDLE])
        )

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
            pystray.MenuItem("Open Setup & Settings…", self._show_settings),
            pystray.MenuItem("Reload Settings & Retry", self._reload_settings),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit PulseScribe", self._quit),
        )

        self._tray = pystray.Icon(
            "pulsescribe",
            icon,
            build_daemon_tray_title(current_state, current_text),
            menu,
        )

        # Tray-Worker erst starten, wenn das Tray existiert. Ein evtl. schon
        # gesetztes Update-Signal (Startup-LOADING) wird sofort nachgezogen.
        self._start_tray_update_worker()

    def _quit(self):
        """Beendet den Daemon.

        Optimiert für schnelles Beenden:
        - Hotkey-Listener werden nicht blockierend gestoppt (Daemon-Threads)
        - FileWatcher mit kurzem Timeout
        - Alle Komponenten signalisieren Stop, ohne lange zu warten
        """
        logger.info("Beende PulseScribe...")

        # Stop-Signale für Hauptschleife und eine evtl. aktive Transkription.
        self._stop_event.set()
        self._recording_stop_event.set()

        # Worker-Threads aufwecken/beenden (Daemon-Threads, kein Join nötig)
        self._hotkey_action_queue.put(None)
        self._tray_update_signal.set()

        # Hotkey-Listener: stop() aufrufen, aber NICHT warten
        # pynput-Listener blockieren bis zum nächsten Tastendruck - das umgehen wir
        # Die Listener sind Daemon-Threads und werden beim Prozessende automatisch beendet
        with self._hotkey_listener_lock:
            # Bereits laufende Listener sofort als stale markieren.
            self._hotkey_listener_generation += 1
            for listener in self._hotkey_listeners:
                try:
                    listener.stop()
                except Exception:
                    pass
            self._hotkey_listeners.clear()

        # FileWatcher stoppen (kurzer Timeout)
        self._stop_env_watcher()

        # WebSocket-Loop vor dem Audio-Stream begrenzt stoppen, damit dessen
        # Session-Cleanup noch auf die WarmStreamSource zugreifen kann.
        self._shutdown_deepgram_websocket()

        # Warm-Stream stoppen
        self._stop_warm_stream()

        # Settings-Fenster beenden (falls offen)
        if self._settings_process and self._settings_process.poll() is None:
            try:
                self._settings_process.terminate()
                self._settings_process.wait(timeout=2)
            except Exception:
                pass
            self._settings_process = None

        # Onboarding-Wizard beenden (falls offen)
        if self._onboarding_process and self._onboarding_process.poll() is None:
            try:
                self._onboarding_process.terminate()
                self._onboarding_process.wait(timeout=2)
            except Exception:
                pass
            self._onboarding_process = None

        # IPC-Server stoppen
        self._stop_ipc_server()

        # Overlay stoppen
        self._stop_overlay()

        # Tray stoppen (beendet auch den Prozess)
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
        # Bereits offen? Nicht nochmal starten
        if self._settings_process and self._settings_process.poll() is None:
            logger.debug("Settings-Fenster bereits offen")
            return

        try:
            process, start_label = self._start_settings_subprocess()
            if process is None:
                return
            if self._subprocess_failed_immediately(
                process,
                "Settings-Fenster",
                self._open_env_in_editor,
            ):
                return
            self._start_subprocess_stderr_drain(process, "settings")
            self._settings_process = process
            logger.info(f"Settings-Fenster gestartet ({start_label})")

        except Exception as e:
            logger.error(f"Settings-Fenster konnte nicht geöffnet werden: {e}")
            self._open_env_in_editor()

    def _start_settings_subprocess(self):
        return self._start_qt_subprocess(
            frozen_arg="--settings",
            script_path=PROJECT_ROOT / "ui" / "settings_windows.py",
            missing_script_label="Settings-Script",
            missing_pyside_message="PySide6 nicht installiert - öffne .env im Editor",
            fallback=self._open_env_in_editor,
        )

    @staticmethod
    def _subprocess_creationflags(subprocess) -> int:
        return (
            subprocess.CREATE_NO_WINDOW
            if hasattr(subprocess, "CREATE_NO_WINDOW")
            else 0
        )

    def _start_qt_subprocess(
        self,
        *,
        frozen_arg: str,
        script_path: Path,
        missing_script_label: str,
        missing_pyside_message: str,
        fallback,
    ):
        import subprocess

        if getattr(sys, "frozen", False):
            return (
                subprocess.Popen(
                    [sys.executable, frozen_arg],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    creationflags=self._subprocess_creationflags(subprocess),
                ),
                frozen_arg,
            )

        python_exe = self._resolve_qt_python_executable(
            missing_pyside_message=missing_pyside_message,
            fallback=fallback,
        )
        if python_exe is None:
            return None, "separater Prozess"

        if not script_path.exists():
            logger.error(f"{missing_script_label} nicht gefunden: {script_path}")
            fallback()
            return None, "separater Prozess"

        return (
            subprocess.Popen(
                [python_exe, str(script_path)],
                cwd=str(PROJECT_ROOT),
                env=self._qt_subprocess_env(),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                creationflags=self._subprocess_creationflags(subprocess),
            ),
            "separater Prozess",
        )

    def _resolve_qt_python_executable(
        self, *, missing_pyside_message: str, fallback
    ) -> str | None:
        venv_python = PROJECT_ROOT / "venv" / "Scripts" / "python.exe"
        dotvenv_python = PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"
        if venv_python.exists():
            return str(venv_python)
        if dotvenv_python.exists():
            return str(dotvenv_python)

        import importlib.util

        if importlib.util.find_spec("PySide6") is None:
            logger.warning(missing_pyside_message)
            fallback()
            return None
        return sys.executable

    @staticmethod
    def _qt_subprocess_env() -> dict[str, str]:
        env = os.environ.copy()
        project_root = str(PROJECT_ROOT)
        existing_pythonpath = env.get("PYTHONPATH")
        env["PYTHONPATH"] = (
            project_root + os.pathsep + existing_pythonpath
            if existing_pythonpath
            else project_root
        )
        return env

    @staticmethod
    def _subprocess_failed_immediately(process, label: str, fallback) -> bool:
        import time

        time.sleep(0.5)
        if process.poll() is None:
            return False
        _, stderr = process.communicate(timeout=1)
        error_msg = stderr.decode("utf-8", errors="replace").strip()
        logger.error(f"{label} fehlgeschlagen: {error_msg[:200]}")
        fallback()
        return True

    def _start_subprocess_stderr_drain(self, process, process_name: str) -> None:
        """Entleert stderr-Pipes im Hintergrund, um Subprocess-Hänger zu vermeiden."""
        thread = start_stream_drain_thread(
            getattr(process, "stderr", None),
            thread_name=f"pulsescribe-{process_name}-stderr-drain",
            on_error=lambda exc: logger.debug(
                f"{process_name} stderr-drain beendet: {exc}"
            ),
        )
        if thread is not None:
            logger.debug(f"stderr-drain aktiv: {process_name}")

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
                    "PULSESCRIBE_DEEPGRAM_WARM_WEBSOCKET=true\n"
                    "PULSESCRIBE_DEEPGRAM_KEEPALIVE_INTERVAL_SECONDS=3\n"
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
        with self._reload_settings_lock:
            if self._stop_event.is_set():
                logger.debug("Settings-Reload ignoriert: App wird beendet")
                return

            logger.info("Settings neu laden...")

            old_local_signature = self._local_provider_memory_signature()
            env_values = self._read_reloaded_env_values()
            self._sync_local_provider_reload_env_values(env_values)
            if self._stop_event.is_set():
                logger.debug("Settings-Reload abgebrochen: App wird beendet")
                return

            self._apply_mode_reload_settings(
                env_values,
                old_local_signature=old_local_signature,
            )
            self._apply_refine_reload_settings(env_values)
            self._apply_streaming_reload_settings(env_values)
            self._refresh_deepgram_websocket_prewarm()
            self._apply_overlay_reload_settings(env_values)
            if not self._apply_hotkey_reload_settings(env_values):
                return

            logger.info("Settings erfolgreich neu geladen")

    @staticmethod
    def _read_reloaded_env_values() -> dict[str, str]:
        # WICHTIG: os.environ aktualisieren, damit alle Module die neuen Werte sehen
        # (z.B. refine/llm.py verwendet os.getenv() direkt)
        load_environment(override_existing=True)
        from utils.preferences import read_env_file

        return read_env_file()

    @staticmethod
    def _normalize_local_signature_value(value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None

    @staticmethod
    def _sync_local_provider_reload_env_values(env_values: dict[str, str]) -> None:
        for key in _LOCAL_PROVIDER_RELOAD_ENV_KEYS:
            value = env_values.get(key)
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def _local_provider_memory_signature(self) -> tuple[str | None, ...]:
        local_model = os.getenv("PULSESCRIBE_LOCAL_MODEL") or os.getenv(
            "PULSESCRIBE_MODEL"
        )
        return (
            self._normalize_local_signature_value(self.mode),
            self._normalize_local_signature_value(
                os.getenv("PULSESCRIBE_LOCAL_BACKEND")
            ),
            self._normalize_local_signature_value(local_model),
            self._normalize_local_signature_value(os.getenv("PULSESCRIBE_DEVICE")),
            self._normalize_local_signature_value(os.getenv("PULSESCRIBE_FP16")),
            self._normalize_local_signature_value(
                os.getenv("PULSESCRIBE_LOCAL_COMPUTE_TYPE")
            ),
            self._normalize_local_signature_value(
                os.getenv("PULSESCRIBE_LOCAL_CPU_THREADS")
            ),
            self._normalize_local_signature_value(
                os.getenv("PULSESCRIBE_LOCAL_NUM_WORKERS")
            ),
            self._normalize_local_signature_value(
                os.getenv("PULSESCRIBE_LIGHTNING_BATCH_SIZE")
            ),
            self._normalize_local_signature_value(
                os.getenv("PULSESCRIBE_LIGHTNING_QUANT")
            ),
        )

    @staticmethod
    def _release_provider_resources(provider) -> None:
        clear_model_cache = getattr(provider, "clear_model_cache", None)
        if callable(clear_model_cache):
            clear_model_cache()
            return

        cleanup = getattr(provider, "cleanup", None)
        if callable(cleanup):
            cleanup()

    def _release_local_provider_model_cache(self) -> None:
        with self._provider_cache_lock:
            local_provider = self._provider_cache.get("local")
        if local_provider is None:
            return
        try:
            self._release_provider_resources(local_provider)
        except Exception as e:
            logger.warning(f"LocalProvider cleanup fehlgeschlagen: {e}")

    def _apply_mode_reload_settings(
        self,
        env_values: dict[str, str],
        *,
        old_local_signature: tuple[str | None, ...],
    ) -> None:
        new_mode = env_values.get("PULSESCRIBE_MODE", "deepgram")
        mode_changed = new_mode != self.mode
        if mode_changed:
            old_mode = self.mode
            self.mode = new_mode
            self._invalidate_all_provider_runtime_configs()
            logger.info(f"Mode geändert: {old_mode} → {new_mode}")
        elif new_mode == "local":
            if self._local_provider_memory_signature() != old_local_signature:
                self._release_local_provider_model_cache()
            self._invalidate_local_provider_runtime_config()

        if new_mode == "local":
            threading.Thread(target=self._preload_local_model, daemon=True).start()

    def _invalidate_all_provider_runtime_configs(self) -> None:
        with self._provider_cache_lock:
            providers_to_invalidate = list(self._provider_cache.values())
            self._provider_cache.clear()
        for provider in providers_to_invalidate:
            try:
                self._release_provider_resources(provider)
            except Exception as e:
                logger.warning(f"Provider cleanup fehlgeschlagen: {e}")
            if hasattr(provider, "invalidate_runtime_config"):
                provider.invalidate_runtime_config()

    def _invalidate_local_provider_runtime_config(self) -> None:
        with self._provider_cache_lock:
            local_provider = self._provider_cache.get("local")
        if local_provider and hasattr(local_provider, "invalidate_runtime_config"):
            local_provider.invalidate_runtime_config()

    def _apply_refine_reload_settings(self, env_values: dict[str, str]) -> None:
        self.refine = _env_flag(
            env_values.get("PULSESCRIBE_REFINE"),
            default=False,
        )
        self.refine_model = env_values.get("PULSESCRIBE_REFINE_MODEL")
        self.refine_provider = env_values.get("PULSESCRIBE_REFINE_PROVIDER")
        self.context = env_values.get("PULSESCRIBE_CONTEXT")

    def _apply_streaming_reload_settings(self, env_values: dict[str, str]) -> None:
        streaming_enabled = _env_flag(
            env_values.get("PULSESCRIBE_STREAMING"),
            default=True,
        )
        self.streaming = streaming_enabled and self.mode == "deepgram"

    def _apply_overlay_reload_settings(self, env_values: dict[str, str]) -> None:
        new_overlay_enabled = _env_flag(
            env_values.get("PULSESCRIBE_OVERLAY"),
            default=True,
        )
        if new_overlay_enabled == self.overlay_enabled:
            return
        self.overlay_enabled = new_overlay_enabled
        if new_overlay_enabled and self._overlay is None:
            logger.info("Overlay aktiviert")
            self._setup_overlay()
        elif not new_overlay_enabled and self._overlay is not None:
            logger.info("Overlay deaktiviert")
            self._stop_overlay()

    @staticmethod
    def _resolve_reloaded_hotkeys(
        env_values: dict[str, str],
    ) -> tuple[str | None, str | None]:
        new_toggle = env_values.get("PULSESCRIBE_TOGGLE_HOTKEY")
        new_hold = env_values.get("PULSESCRIBE_HOLD_HOTKEY")
        if not new_toggle and not new_hold:
            return _DEFAULT_TOGGLE_HOTKEY, _DEFAULT_HOLD_HOTKEY
        return new_toggle, new_hold

    def _apply_hotkey_reload_settings(self, env_values: dict[str, str]) -> bool:
        new_toggle, new_hold = self._resolve_reloaded_hotkeys(env_values)
        if new_toggle == self.toggle_hotkey and new_hold == self.hold_hotkey:
            return True
        if self._stop_event.is_set():
            logger.debug("Hotkey-Reload übersprungen: App wird beendet")
            return False
        self.toggle_hotkey = new_toggle
        self.hold_hotkey = new_hold
        logger.info(f"Hotkeys geändert: toggle={new_toggle}, hold={new_hold}")
        self._restart_hotkey_listeners()
        return True

    def _preload_local_model(self):
        """Lädt Local-Model vor nach Settings-Änderung."""
        set_loading = False
        try:
            provider = self._get_provider("local")
            model, _ = self._get_transcription_config()
            if self.state == AppState.IDLE:
                self._set_state(AppState.LOADING, f"Loading {model}...")
                set_loading = True
            if hasattr(provider, "preload"):
                logger.info(f"Preloading local model '{model}'...")
                provider.preload(model=model)
        except Exception as e:
            logger.warning(f"Local-Model Preload fehlgeschlagen: {e}")
        finally:
            if set_loading and self.state == AppState.LOADING:
                self._set_state(AppState.IDLE)

    def _start_env_watcher(self):
        """Startet FileWatcher für .env Änderungen (Auto-Reload).

        Verwendet watchdog wenn verfügbar, ansonsten Polling-Fallback.
        Reagiert auf .env Änderungen und .reload Signal-Datei.
        """
        from utils.preferences import ENV_FILE

        self._reload_signal_file = ENV_FILE.parent / ".reload"

        if not self._start_watchdog_env_observer(ENV_FILE):
            self._start_reload_polling()

    def _start_watchdog_env_observer(self, env_file: Path) -> bool:
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
                    if not _is_reload_event_path(event.src_path):
                        return
                    # Debounce: Ignoriere Events < 1s nach letztem
                    now = time.time()
                    if now - handler_self._last_modified > 1.0:
                        handler_self._last_modified = now
                        logger.debug(f"Settings-Änderung erkannt: {event.src_path}")
                        handler_self.callback()
                        # Signal-Datei löschen nach Verarbeitung
                        _unlink_reload_signal_file(handler_self.signal_file)

                def on_created(handler_self, event):
                    # Auch neue .reload Dateien beachten
                    if event.src_path.endswith(".reload"):
                        handler_self.on_modified(event)

            handler = EnvFileHandler(self._reload_settings, self._reload_signal_file)
            self._env_observer = Observer()
            self._env_observer.schedule(handler, str(env_file.parent), recursive=False)
            self._env_observer.start()
            logger.info(f"FileWatcher gestartet für {env_file.parent}")
            return True

        except ImportError:
            logger.debug("watchdog nicht installiert - verwende Polling-Fallback")
            self._env_observer = None
        except Exception as e:
            logger.warning(f"FileWatcher konnte nicht gestartet werden: {e}")
            self._env_observer = None

        return False

    def _stop_env_watcher(self):
        """Stoppt den FileWatcher und Polling."""
        # FileWatcher stoppen (kurzer Timeout für schnelles Beenden)
        if hasattr(self, "_env_observer") and self._env_observer is not None:
            try:
                self._env_observer.stop()
                self._env_observer.join(timeout=_SHUTDOWN_TIMEOUT_SEC)
                logger.debug("FileWatcher gestoppt")
            except Exception as e:
                logger.debug(f"FileWatcher Stop-Fehler: {e}")
            self._env_observer = None

        # Polling-Fallback stoppen
        self._reload_polling_stop.set()
        if (
            hasattr(self, "_reload_polling_thread")
            and self._reload_polling_thread is not None
        ):
            if self._reload_polling_thread.is_alive():
                self._reload_polling_thread.join(timeout=_SHUTDOWN_TIMEOUT_SEC)
            self._reload_polling_thread = None

    def _start_reload_polling(self):
        """Startet Polling für .reload Signal-Datei (Fallback wenn watchdog nicht verfügbar)."""
        if self._reload_signal_file is None:
            return

        if (
            self._reload_polling_thread is not None
            and self._reload_polling_thread.is_alive()
        ):
            return

        self._reload_polling_stop.clear()

        def poll_for_reload_loop():
            while not self._stop_event.is_set():
                if self._reload_polling_stop.wait(timeout=2.0):
                    break

                try:
                    if self._reload_signal_file and self._reload_signal_file.exists():
                        logger.debug("Reload-Signal erkannt (Polling)")
                        self._reload_signal_file.unlink()
                        self._reload_settings()
                except Exception as e:
                    logger.debug(f"Polling-Fehler: {e}")

        self._reload_polling_thread = threading.Thread(
            target=poll_for_reload_loop,
            daemon=True,
            name="PulseScribeReloadPolling",
        )
        self._reload_polling_thread.start()
        logger.info("Polling-Fallback gestartet für Settings-Reload")

    def _restart_hotkey_listeners(self):
        """Startet Hotkey-Listener mit neuen Einstellungen neu."""
        with self._hotkey_listener_lock:
            # Alte Listener sofort als stale markieren.
            self._hotkey_listener_generation += 1

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

            # PySide6 exposes a ready event. Wait briefly so the first hotkey/
            # LOADING state is not stuck in a pending queue, but never block
            # startup indefinitely.
            wait_until_ready = getattr(self._overlay, "wait_until_ready", None)
            if callable(wait_until_ready):
                wait_until_ready(timeout=0.15)

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
            imports_ms = self._prewarm_dependencies(start)
            device_idx, sample_rate, device_ms = self._prewarm_audio_device()
            self._prefetch_streaming_dns()
            preload_ms = self._preload_local_model_for_prewarm()
            total_ms = (time.perf_counter() - start) * 1000
            self._log_prewarm_complete(
                total_ms,
                imports_ms,
                device_ms,
                preload_ms,
                device_idx,
                sample_rate,
            )
        except Exception as e:
            logger.debug(f"Pre-Warm fehlgeschlagen: {e}", exc_info=True)
        finally:
            self._prewarm_complete.set()

    def _prewarm_dependencies(self, start: float) -> float:
        # Phase 1: Core-Libraries (für Streaming und REST)
        import numpy  # noqa: F401 - ~300ms
        import sounddevice  # noqa: F401 - ~100ms

        # Phase 2: Provider-Dependencies vorwärmen
        if self.streaming:
            self._prewarm_streaming_dependencies()
        else:
            self._prewarm_rest_dependencies()

        # Phase 2b: UI-Imports (optional, beschleunigt _setup_overlay/tray)
        self._prewarm_ui_dependencies()
        return (time.perf_counter() - start) * 1000

    def _prewarm_streaming_dependencies(self) -> None:
        from providers.deepgram_stream import (  # noqa: F401
            DeepgramWarmConnectionManager,
            deepgram_stream_core,
        )
        import httpx  # noqa: F401
        import websockets  # noqa: F401

        # Deepgram SDK-Klassen (werden in deepgram_stream_core benötigt)
        from deepgram.core.events import EventType  # noqa: F401

        # Ohne Warm-WebSocket bleibt der bisherige Event-Loop-Prewarm als Fallback.
        if not self._deepgram_warm_websocket_enabled():
            import asyncio

            self._event_loop = asyncio.new_event_loop()

    def _prewarm_rest_dependencies(self) -> None:
        # REST-Modi zahlen sonst beim ersten Stop lazy Import-/Client-Kosten.
        try:
            import soundfile  # noqa: F401

            self._get_provider(self.mode)
        except Exception as e:
            logger.debug(f"REST-Provider Pre-Warm übersprungen: {e}")

    def _prewarm_ui_dependencies(self) -> None:
        try:
            import pystray  # noqa: F401
            from PIL import Image  # noqa: F401

            if self.overlay_enabled:
                from ui.overlay_windows import (
                    WindowsOverlayController as _WOC,  # noqa: F401
                )
        except ImportError:
            pass  # Optional, nicht kritisch

    def _prewarm_audio_device(self) -> tuple[int | None, int, float]:
        # Phase 3: Audio-Device erkennen (~250-500ms auf Windows)
        # get_input_device() cached das Ergebnis in config._cached_input_device
        device_start = time.perf_counter()
        device_idx, sample_rate = get_input_device()

        # Deepgram parallel zum Mikrofonstart verbinden. Der Aufruf wartet nicht
        # auf TLS/WebSocket und blockiert daher die Mic-Ready-Rückmeldung nicht.
        self._refresh_deepgram_websocket_prewarm(sample_rate=sample_rate)

        # Phase 4: Warm-Stream starten (für alle Modi!)
        # Der Warm-Stream bleibt offen und ermöglicht instant-start Recording
        # Auch REST-Modi (Groq, OpenAI, Local) profitieren vom Warm-Stream
        self._start_warm_stream()
        device_ms = (time.perf_counter() - device_start) * 1000
        return device_idx, sample_rate, device_ms

    def _refresh_deepgram_websocket_prewarm(
        self,
        *,
        sample_rate: int | None = None,
    ) -> None:
        """Prepare the next Deepgram session without blocking startup/reload."""
        should_prewarm = (
            self.mode == "deepgram"
            and self.streaming
            and self._deepgram_warm_websocket_enabled()
            and bool(os.getenv("DEEPGRAM_API_KEY", "").strip())
        )
        manager = self._deepgram_connection_manager
        if not should_prewarm:
            if manager is not None:
                manager.invalidate()
            return

        if manager is None:
            from providers.deepgram_stream import DeepgramWarmConnectionManager

            manager = DeepgramWarmConnectionManager()
            self._deepgram_connection_manager = manager

        model, language = self._get_deepgram_streaming_config()
        manager.prewarm(
            model=model,
            language=language,
            sample_rate=sample_rate or self._warm_stream_sample_rate,
        )

    def _shutdown_deepgram_websocket(self) -> None:
        manager, self._deepgram_connection_manager = (
            self._deepgram_connection_manager,
            None,
        )
        if manager is not None:
            manager.shutdown(timeout=1.5)

    def _prefetch_streaming_dns(self) -> None:
        if not self.streaming:
            return
        try:
            import socket

            socket.getaddrinfo("api.deepgram.com", 443)
        except Exception:
            pass  # Ignorieren wenn es fehlschlägt

    def _preload_local_model_for_prewarm(self) -> float:
        if self.mode != "local":
            return 0.0
        try:
            return self._run_local_model_prewarm()
        except Exception as e:
            logger.warning(f"Local-Modell Preload fehlgeschlagen: {e}")
            return 0.0

    def _run_local_model_prewarm(self) -> float:
        preload_start = time.perf_counter()
        provider = self._get_provider("local")
        model, _language = self._get_transcription_config()
        if not self._mic_ready.is_set():
            self._set_state(AppState.LOADING, f"Loading {model}...")
        if hasattr(provider, "preload"):
            provider.preload(model=model)
        preload_ms = (time.perf_counter() - preload_start) * 1000
        runtime_info = self._format_local_provider_runtime_info(provider)
        logger.info(
            f"Local-Modell '{model}' vorab geladen ({preload_ms:.0f}ms{runtime_info})"
        )
        # Auditive Rückmeldung: User kann jetzt mit minimaler Latenz aufnehmen
        get_sound_player().play("warmup")
        return preload_ms

    @staticmethod
    def _format_local_provider_runtime_info(provider) -> str:
        if not hasattr(provider, "get_runtime_info"):
            return ""
        info = provider.get_runtime_info()
        device = (info.get("device") or "unknown").upper()
        compute = info.get("compute_type")
        runtime_info = f", Device: {device}"
        if compute:
            runtime_info += f", Compute: {compute}"
        return runtime_info

    def _log_prewarm_complete(
        self,
        total_ms: float,
        imports_ms: float,
        device_ms: float,
        preload_ms: float,
        device_idx: int | None,
        sample_rate: int,
    ) -> None:
        mode_desc = f"{self.mode} ({'Streaming' if self.streaming else 'REST'})"
        preload_info = f", Preload={preload_ms:.0f}ms" if self.mode == "local" else ""
        logger.info(
            f"Pre-Warm abgeschlossen ({total_ms:.0f}ms, {mode_desc}, Warm-Stream): "
            f"Imports={imports_ms:.0f}ms, Device={device_ms:.0f}ms{preload_info} "
            f"(idx={device_idx}, {sample_rate}Hz)"
        )

    def _show_settings_if_needed(self):
        """Zeigt Wizard beim ersten Start oder Settings wenn aktiviert.

        Analog zu macOS _show_welcome_if_needed(): Öffnet Wizard automatisch
        wenn Onboarding nicht abgeschlossen ist, sonst Settings wenn aktiviert.
        """
        from utils.preferences import (
            get_show_welcome_on_startup,
            is_onboarding_complete,
        )

        # Logik analog zu macOS:
        # 1. Erster Start (Onboarding nicht complete) → Wizard öffnen
        # 2. Sonst: Nur wenn "Show at startup" aktiviert → Settings öffnen
        if not is_onboarding_complete():
            logger.info("Erster Start erkannt - Onboarding-Wizard öffnen")
            self._show_onboarding_wizard()
        elif get_show_welcome_on_startup():
            logger.info("'Show at startup' aktiviert - Settings öffnen")
            self._show_settings()

    def _show_onboarding_wizard(self):
        """Zeigt den Onboarding-Wizard als separaten Prozess an.

        Analog zu _show_settings(): Qt-GUIs müssen in einem eigenen Prozess
        laufen, nicht in einem Thread, da Qt den Main-Thread benötigt.
        """
        # Bereits offen? Nicht nochmal starten
        if self._onboarding_process and self._onboarding_process.poll() is None:
            logger.debug("Onboarding-Wizard bereits offen")
            return

        try:
            process, start_label = self._start_onboarding_subprocess()
            if process is None:
                return
            if self._subprocess_failed_immediately(
                process,
                "Onboarding-Wizard",
                self._show_settings,
            ):
                return
            self._start_subprocess_stderr_drain(process, "onboarding")
            self._onboarding_process = process
            logger.info(f"Onboarding-Wizard gestartet ({start_label})")

            self._start_ipc_server()

        except Exception as e:
            logger.error(f"Onboarding-Wizard konnte nicht geöffnet werden: {e}")
            self._show_settings()

    def _start_onboarding_subprocess(self):
        return self._start_qt_subprocess(
            frozen_arg="--onboarding",
            script_path=PROJECT_ROOT / "ui" / "onboarding_wizard_windows.py",
            missing_script_label="Wizard-Script",
            missing_pyside_message="PySide6 nicht installiert - öffne Settings stattdessen",
            fallback=self._show_settings,
        )

    # =========================================================================
    # IPC for Wizard Communication
    # =========================================================================

    def _start_ipc_server(self) -> None:
        """Start the IPC server for wizard communication."""
        if self._ipc_server is not None:
            return

        try:
            from utils.ipc import IPCServer

            self._ipc_server = IPCServer(self._handle_ipc_command)
            self._ipc_server.start()
            logger.info("IPC-Server für Wizard gestartet")
        except Exception as e:
            logger.error(f"IPC-Server Start fehlgeschlagen: {e}")

    def _stop_ipc_server(self) -> None:
        """Stop the IPC server."""
        if self._ipc_server is None:
            return

        try:
            self._ipc_server.stop()
            logger.info("IPC-Server gestoppt")
        except Exception as e:
            logger.warning(f"IPC-Server Stop Fehler: {e}")
        finally:
            self._ipc_server = None
            self._ipc_test_cmd_id = None

    def _handle_ipc_command(self, cmd_id: str, command: str) -> None:
        """Handle IPC commands from the wizard."""
        from utils.ipc import CMD_START_TEST, CMD_STOP_TEST, STATUS_ERROR

        logger.debug(f"IPC-Command empfangen: {command} (id={cmd_id})")

        if command == CMD_START_TEST:
            self._start_ipc_test(cmd_id)
        elif command == CMD_STOP_TEST:
            self._stop_ipc_test(cmd_id)
        else:
            logger.warning(f"Unbekannter IPC-Command: {command}")
            if self._ipc_server:
                self._ipc_server.send_response(
                    cmd_id, STATUS_ERROR, error=f"Unknown command: {command}"
                )

    def _start_ipc_test(self, cmd_id: str) -> None:
        """Start test dictation via IPC."""
        from utils.ipc import STATUS_ERROR, STATUS_RECORDING

        # Already recording?
        if self.state in (
            AppState.LISTENING,
            AppState.RECORDING,
            AppState.TRANSCRIBING,
        ):
            if self._ipc_server:
                self._ipc_server.send_response(
                    cmd_id, STATUS_ERROR, error="Bereits in Aufnahme"
                )
            return

        # Start recording (uses existing mechanism)
        started = self._start_recording()
        if not started:
            if self._ipc_server:
                self._ipc_server.send_response(
                    cmd_id, STATUS_ERROR, error="Aufnahme konnte nicht gestartet werden"
                )
            return

        # Erst nach erfolgreichem Start setzen, um falsches Routing bei Race-Interleavings
        # mit einem parallel abschließenden vorherigen Testlauf zu vermeiden.
        self._ipc_test_cmd_id = cmd_id

        # Send "recording" status erst nach erfolgreichem Start
        if self._ipc_server:
            self._ipc_server.send_response(cmd_id, STATUS_RECORDING)
        logger.info(f"IPC-Test gestartet (id={cmd_id})")

    def _stop_ipc_test(self, cmd_id: str) -> None:
        """Stop test dictation via IPC."""
        from utils.ipc import STATUS_STOPPED

        if self.state in (AppState.LISTENING, AppState.RECORDING):
            self._stop_recording()
            logger.info(f"IPC-Test gestoppt (id={cmd_id})")
        else:
            # Not recording, just acknowledge
            if self._ipc_server:
                self._ipc_server.send_response(cmd_id, STATUS_STOPPED)

    def _format_hotkey_summary(self) -> str:
        hotkey_info = []
        if self.toggle_hotkey:
            hotkey_info.append(f"Toggle: {self.toggle_hotkey}")
        if self.hold_hotkey:
            hotkey_info.append(f"Hold: {self.hold_hotkey}")
        return ", ".join(hotkey_info) if hotkey_info else "Keiner"

    def _print_startup_banner(self) -> None:
        print(
            f"PulseScribe Windows gestartet (Hotkeys: {self._format_hotkey_summary()})"
        )
        print("Drücke Ctrl+C oder nutze Tray-Menü zum Beenden")

    def _setup_startup_hotkeys(self) -> None:
        # Hotkey ZUERST registrieren - User kann sofort starten
        # (Device-Erkennung läuft parallel im Pre-Warm)
        with self._hotkey_listener_lock:
            self._setup_hotkey()

    def _start_prewarm_thread(self) -> None:
        # LOADING-State während Pre-Warm anzeigen (für alle Modi mit Warm-Stream)
        self._is_prewarm_loading = True
        self._set_state(AppState.LOADING, "Starting up...")
        threading.Thread(
            target=self._finish_prewarm_startup,
            daemon=True,
            name="PreWarm",
        ).start()

    def _finish_prewarm_startup(self) -> None:
        self._prewarm_imports()
        # Falls kein Warm-Stream bereit wurde, erst nach vollständigem Pre-Warm
        # auf Ready gehen. Bei erfolgreichem Warm-Stream erledigt _mark_mic_ready()
        # das deutlich früher.
        if not self._is_prewarm_loading:
            return
        self._is_prewarm_loading = False
        if self.state == AppState.LOADING:
            self._set_state(AppState.IDLE)
            self._play_sound("ready")  # Signal: System bereit

    def _install_signal_handlers(self) -> None:
        signal.signal(signal.SIGINT, self._handle_sigint)

    def _handle_sigint(self, sig, frame) -> None:
        if self._stop_event.is_set():
            return
        print("\nCtrl+C erkannt, beende...")
        self._quit()

    def _run_tray_if_available(self) -> None:
        if self._tray:
            # Tray-Icon in Hintergrund-Thread, damit Hauptthread Ctrl+C empfängt
            self._tray.run_detached()

    def _wait_until_stopped(self) -> None:
        try:
            while not self._stop_event.is_set():
                time.sleep(0.5)
        except KeyboardInterrupt:
            self._quit()

    def run(self):
        """Startet den Daemon."""
        apply_windows_responsiveness_boost(logger)
        self._print_startup_banner()
        self._setup_startup_hotkeys()
        self._setup_overlay()
        self._start_prewarm_thread()
        self._setup_tray()
        self._start_env_watcher()
        self._show_settings_if_needed()
        self._install_signal_handlers()
        self._run_tray_if_available()
        self._wait_until_stopped()
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
    parser.add_argument(
        "--onboarding",
        action="store_true",
        help="Onboarding-Wizard öffnen (statt Daemon starten)",
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

    # --onboarding: Onboarding-Wizard öffnen und beenden (kein Daemon)
    if args.onboarding:
        try:
            from PySide6.QtWidgets import QApplication
            from ui.onboarding_wizard_windows import OnboardingWizardWindows

            app = QApplication(sys.argv)
            wizard = OnboardingWizardWindows()
            wizard.show()
            sys.exit(app.exec())
        except ImportError as e:
            print(f"Onboarding-Wizard nicht verfügbar: {e}", file=sys.stderr)
            sys.exit(1)

    effective_debug = args.debug or _env_flag(
        os.getenv("PULSESCRIBE_DEBUG"),
        default=False,
    )
    setup_logging(debug=effective_debug)

    # Refine: CLI > ENV > Default (False)
    effective_refine = args.refine or _env_flag(
        os.getenv("PULSESCRIBE_REFINE"),
        default=False,
    )
    effective_refine_model = args.refine_model or os.getenv("PULSESCRIBE_REFINE_MODEL")
    effective_refine_provider = args.refine_provider or os.getenv(
        "PULSESCRIBE_REFINE_PROVIDER"
    )
    effective_context = args.context or os.getenv("PULSESCRIBE_CONTEXT")
    effective_mode = args.mode

    # Streaming: Default True, kann via --no-streaming oder ENV deaktiviert werden
    effective_streaming = not args.no_streaming and _env_flag(
        os.getenv("PULSESCRIBE_STREAMING"), default=True
    )

    # Nur Deepgram unterstützt aktuell Streaming im Daemon
    if effective_mode != "deepgram" and effective_streaming:
        logging.info(
            f"Modus '{effective_mode}' unterstützt kein Streaming im Daemon -> Fallback auf REST"
        )
        effective_streaming = False

    # Overlay: Default True, kann via --no-overlay oder ENV deaktiviert werden
    effective_overlay = not args.no_overlay and _env_flag(
        os.getenv("PULSESCRIBE_OVERLAY"), default=True
    )

    # Hotkeys: CLI > ENV > Default
    # Konsistent mit macOS: PULSESCRIBE_TOGGLE_HOTKEY und PULSESCRIBE_HOLD_HOTKEY
    effective_toggle_hotkey = args.toggle_hotkey or os.getenv(
        "PULSESCRIBE_TOGGLE_HOTKEY"
    )
    effective_hold_hotkey = args.hold_hotkey or os.getenv("PULSESCRIBE_HOLD_HOTKEY")

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
