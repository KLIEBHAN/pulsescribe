import contextlib
import importlib.util
import io
import sys
import threading
import time
import types
from pathlib import Path
from unittest.mock import patch

from utils.state import AppState
import utils.preferences as preferences


def _load_windows_module():
    module_name = "_pulsescribe_windows_test"
    if module_name in sys.modules:
        return sys.modules[module_name]

    project_root = Path(__file__).resolve().parents[1]
    module_path = project_root / "pulsescribe_windows.py"
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:  # pragma: no cover
        raise RuntimeError("pulsescribe_windows.py konnte nicht geladen werden")

    module = importlib.util.module_from_spec(spec)
    original_exit = sys.exit
    try:
        # Für Tests außerhalb von Windows: Guard am Dateikopf nicht terminieren lassen.
        sys.exit = lambda _code=0: None
        with contextlib.redirect_stderr(io.StringIO()):
            spec.loader.exec_module(module)
    finally:
        sys.exit = original_exit

    sys.modules[module_name] = module
    return module


def test_start_recording_is_atomic_for_concurrent_triggers(monkeypatch):
    windows_module = _load_windows_module()
    daemon = windows_module.PulseScribeWindows(
        mode="openai",
        streaming=False,
        overlay=False,
    )
    daemon._play_sound = lambda _name: None

    start_count = 0
    start_count_lock = threading.Lock()

    def fake_recording_loop():
        nonlocal start_count
        with start_count_lock:
            start_count += 1

    daemon._recording_loop = fake_recording_loop
    monkeypatch.setattr(windows_module.time, "sleep", lambda _seconds: None)

    barrier = threading.Barrier(3)
    results: list[bool] = []

    def trigger_start():
        barrier.wait()
        results.append(daemon._start_recording())

    t1 = threading.Thread(target=trigger_start)
    t2 = threading.Thread(target=trigger_start)
    t1.start()
    t2.start()
    barrier.wait()
    t1.join()
    t2.join()

    worker = daemon._recording_thread
    if worker is not None:
        worker.join(timeout=1.0)

    assert sorted(results) == [False, True]
    assert start_count == 1


def test_stop_recording_is_idempotent_for_rest_mode():
    windows_module = _load_windows_module()
    daemon = windows_module.PulseScribeWindows(
        mode="openai",
        streaming=False,
        overlay=False,
    )
    daemon._play_sound = lambda _name: None
    daemon._set_state(AppState.RECORDING)

    transcribe_calls = 0
    transcribe_lock = threading.Lock()
    transcribe_called = threading.Event()

    def fake_transcribe_rest():
        nonlocal transcribe_calls
        with transcribe_lock:
            transcribe_calls += 1
        transcribe_called.set()

    daemon._transcribe_rest = fake_transcribe_rest

    barrier = threading.Barrier(3)

    def trigger_stop():
        barrier.wait()
        daemon._stop_recording()

    t1 = threading.Thread(target=trigger_stop)
    t2 = threading.Thread(target=trigger_stop)
    t1.start()
    t2.start()
    barrier.wait()
    t1.join()
    t2.join()

    transcribe_called.wait(timeout=1.0)
    time.sleep(0.05)

    assert transcribe_calls == 1
    assert daemon.state == AppState.TRANSCRIBING


def test_stop_recording_from_hotkey_allows_loading_state():
    windows_module = _load_windows_module()
    daemon = windows_module.PulseScribeWindows(
        mode="openai",
        streaming=False,
        overlay=False,
    )
    daemon._set_state(AppState.LOADING)

    stop_calls = 0

    def fake_stop_recording():
        nonlocal stop_calls
        stop_calls += 1

    daemon._stop_recording = fake_stop_recording

    daemon._stop_recording_from_hotkey()

    assert stop_calls == 1


def test_streaming_stop_switches_to_transcribing_immediately(monkeypatch):
    import asyncio

    monkeypatch.setattr(
        asyncio,
        "WindowsSelectorEventLoopPolicy",
        asyncio.DefaultEventLoopPolicy,
        raising=False,
    )
    windows_module = _load_windows_module()
    daemon = windows_module.PulseScribeWindows(
        mode="deepgram",
        streaming=True,
        overlay=False,
    )
    daemon._set_state(AppState.RECORDING)
    daemon._run_streaming = True
    daemon._start_transcribing_watchdog = lambda: None

    daemon._stop_recording()

    assert daemon._recording_stop_event.is_set()
    assert daemon.state == AppState.TRANSCRIBING


def test_audio_callback_recording_state_skips_tray_hotpath(monkeypatch):
    import asyncio

    monkeypatch.setattr(
        asyncio,
        "WindowsSelectorEventLoopPolicy",
        asyncio.DefaultEventLoopPolicy,
        raising=False,
    )
    windows_module = _load_windows_module()
    daemon = windows_module.PulseScribeWindows(
        mode="deepgram",
        streaming=True,
        overlay=False,
    )
    daemon._set_state(AppState.LISTENING)

    events: list[tuple[str, object, object | None]] = []
    daemon._overlay_update_state = lambda state, text=None: events.append(
        ("overlay", state, text)
    )
    daemon._update_tray_icon = lambda text=None: events.append(("tray", text, None))

    daemon._enter_recording_from_audio_callback()

    assert daemon.state == AppState.RECORDING
    assert events == [("overlay", "RECORDING", None)]


def test_mic_ready_unblocks_startup_before_optional_prewarm():
    windows_module = _load_windows_module()
    daemon = windows_module.PulseScribeWindows(
        mode="local",
        streaming=False,
        overlay=False,
    )
    daemon._is_prewarm_loading = True
    daemon._set_state(AppState.LOADING, "Starting up...")
    played: list[str] = []
    daemon._play_sound = lambda name: played.append(name)

    daemon._mark_mic_ready()

    assert daemon._mic_ready.is_set()
    assert daemon._is_prewarm_loading is False
    assert daemon.state == AppState.IDLE
    assert played == ["ready"]


def test_rest_cold_fallback_does_not_sleep_before_recording(monkeypatch):
    windows_module = _load_windows_module()
    daemon = windows_module.PulseScribeWindows(
        mode="openai",
        streaming=False,
        overlay=False,
    )
    daemon._warm_stream = None
    daemon._play_sound = lambda _name: None

    class _FakeThread:
        def __init__(self, *args, **kwargs):
            self.target = kwargs.get("target")

        def start(self):
            return None

    def fail_on_sleep(seconds):
        raise AssertionError(f"unexpected sleep before recording: {seconds}")

    monkeypatch.setattr(windows_module.threading, "Thread", _FakeThread)
    monkeypatch.setattr(windows_module.time, "sleep", fail_on_sleep)

    assert daemon._start_recording() is True


def test_warm_stream_is_armed_before_ready_feedback(monkeypatch):
    import asyncio

    monkeypatch.setattr(
        asyncio,
        "WindowsSelectorEventLoopPolicy",
        asyncio.DefaultEventLoopPolicy,
        raising=False,
    )
    windows_module = _load_windows_module()
    daemon = windows_module.PulseScribeWindows(
        mode="deepgram",
        streaming=True,
        overlay=False,
    )
    daemon._warm_stream = object()
    daemon._warm_stream_queue.put_nowait(b"stale-audio")

    events = []

    class _FakeThread:
        def __init__(self, *args, **kwargs):
            self.target = kwargs.get("target")
            events.append(("thread_init", daemon._warm_stream_armed.is_set()))

        def start(self):
            events.append(("thread_start", daemon._warm_stream_armed.is_set()))

    def fake_play_sound(name):
        events.append(
            (
                "sound",
                name,
                daemon._warm_stream_armed.is_set(),
                daemon._warm_stream_queue.empty(),
            )
        )

    monkeypatch.setattr(windows_module.threading, "Thread", _FakeThread)
    daemon._play_sound = fake_play_sound

    assert daemon._start_recording() is True

    assert ("sound", "ready", True, True) in events
    assert ("thread_init", True) in events
    assert daemon._warm_stream_armed.is_set()


def test_warm_stream_prepare_moves_preroll_after_stale_queue(monkeypatch):
    import asyncio

    monkeypatch.setattr(
        asyncio,
        "WindowsSelectorEventLoopPolicy",
        asyncio.DefaultEventLoopPolicy,
        raising=False,
    )
    windows_module = _load_windows_module()
    daemon = windows_module.PulseScribeWindows(
        mode="deepgram",
        streaming=True,
        overlay=False,
    )
    daemon._warm_stream_queue.put_nowait(b"stale-audio")
    with daemon._warm_stream_preroll_lock:
        daemon._warm_stream_preroll.append(b"pre-a")
        daemon._warm_stream_preroll.append(b"pre-b")

    daemon._prepare_warm_stream_for_recording()

    queued = []
    while not daemon._warm_stream_queue.empty():
        queued.append(daemon._warm_stream_queue.get_nowait())

    assert queued == [b"pre-a", b"pre-b"]
    assert daemon._warm_stream_armed.is_set()


def test_streaming_worker_warm_passes_windows_stop_grace(monkeypatch):
    import asyncio

    monkeypatch.setattr(
        asyncio,
        "WindowsSelectorEventLoopPolicy",
        asyncio.DefaultEventLoopPolicy,
        raising=False,
    )
    windows_module = _load_windows_module()
    monkeypatch.setattr(
        windows_module,
        "get_windows_stop_grace_seconds",
        lambda: 0.42,
    )

    daemon = windows_module.PulseScribeWindows(
        mode="deepgram",
        streaming=True,
        overlay=False,
    )
    daemon._warm_stream = object()
    daemon._warm_stream_sample_rate = 16000
    daemon._handle_no_speech_result = lambda *_args, **_kwargs: None

    captured_kwargs: dict[str, object] = {}
    events: list[str] = []
    daemon._play_sound = lambda name: events.append(f"sound:{name}")

    async def fake_deepgram_stream_core(*_args, **kwargs):
        captured_kwargs.update(kwargs)
        events.append("core_return")
        return ""

    import providers.deepgram_stream as deepgram_stream

    monkeypatch.setattr(
        deepgram_stream,
        "deepgram_stream_core",
        fake_deepgram_stream_core,
    )

    daemon._streaming_worker_warm()

    assert captured_kwargs["stop_grace_seconds"] == 0.42
    assert events == ["core_return", "sound:stop"]


def test_recording_loop_warm_collects_audio_during_stop_grace(monkeypatch):
    import numpy as np

    windows_module = _load_windows_module()
    monkeypatch.setattr(
        windows_module,
        "get_windows_stop_grace_seconds",
        lambda: 0.05,
    )

    daemon = windows_module.PulseScribeWindows(
        mode="openai",
        streaming=False,
        overlay=False,
    )
    daemon._warm_stream_sample_rate = 16000

    worker = threading.Thread(target=daemon._recording_loop_warm)
    worker.start()
    assert daemon._warm_stream_armed.wait(timeout=1.0)

    daemon._warm_stream_queue.put(np.array([1000], dtype=np.int16).tobytes())
    time.sleep(0.01)
    daemon._recording_stop_event.set()
    time.sleep(0.01)
    daemon._warm_stream_queue.put(np.array([2000], dtype=np.int16).tobytes())

    worker.join(timeout=1.0)

    assert not worker.is_alive()
    with daemon._audio_lock:
        audio_data = np.concatenate(daemon._audio_buffer)

    assert audio_data.shape[0] >= 2
    assert np.isclose(audio_data[-1], 2000 / 32767)


def test_windows_stop_grace_reads_current_environment(monkeypatch):
    windows_module = _load_windows_module()
    daemon = windows_module.PulseScribeWindows(
        mode="openai",
        streaming=False,
        overlay=False,
    )

    monkeypatch.setenv("PULSESCRIBE_WINDOWS_STOP_GRACE_SECONDS", "0.47")

    assert daemon._windows_stop_grace_seconds() == 0.47


def test_stop_recording_plays_stop_sound_after_rest_capture_finishes(monkeypatch):
    windows_module = _load_windows_module()
    daemon = windows_module.PulseScribeWindows(
        mode="openai",
        streaming=False,
        overlay=False,
    )
    daemon._set_state(AppState.RECORDING)
    daemon._run_streaming = False
    daemon._windows_stop_grace_seconds = lambda: 0.3
    daemon._start_transcribing_watchdog = lambda: None

    events: list[str] = []
    daemon._play_sound = lambda name: events.append(f"sound:{name}")

    class _RecordingThread:
        def __init__(self):
            self.alive = True

        def is_alive(self):
            return self.alive

        def join(self, timeout=None):
            events.append(f"join:{timeout}")
            self.alive = False
            events.append("capture_finished")

    class _TranscribeThread:
        def __init__(self, *args, **kwargs):
            self.target = kwargs.get("target")

        def start(self):
            events.append("transcribe_thread_start")

    daemon._recording_thread = _RecordingThread()
    monkeypatch.setattr(windows_module.threading, "Thread", _TranscribeThread)

    daemon._stop_recording()

    assert events == [
        "join:2.3",
        "capture_finished",
        "sound:stop",
        "transcribe_thread_start",
    ]


def test_provider_cache_reload_and_get_provider_are_thread_safe(monkeypatch):
    windows_module = _load_windows_module()

    class _FakeProvider:
        def __init__(self):
            self.invalidated = 0

        def invalidate_runtime_config(self):
            self.invalidated += 1

    daemon = windows_module.PulseScribeWindows(
        mode="local",
        streaming=False,
        overlay=False,
    )
    daemon.toggle_hotkey = windows_module._DEFAULT_TOGGLE_HOTKEY
    daemon.hold_hotkey = windows_module._DEFAULT_HOLD_HOTKEY

    created_providers: list[_FakeProvider] = []

    def fake_get_provider(_mode):
        provider = _FakeProvider()
        created_providers.append(provider)
        return provider

    monkeypatch.setattr(windows_module, "get_provider", fake_get_provider)
    monkeypatch.setattr(
        windows_module,
        "load_environment",
        lambda override_existing=True: None,
    )
    monkeypatch.setattr(
        preferences,
        "read_env_file",
        lambda: {
            "PULSESCRIBE_MODE": "openai",
            "PULSESCRIBE_TOGGLE_HOTKEY": windows_module._DEFAULT_TOGGLE_HOTKEY,
            "PULSESCRIBE_HOLD_HOTKEY": windows_module._DEFAULT_HOLD_HOTKEY,
            "PULSESCRIBE_STREAMING": "false",
            "PULSESCRIBE_OVERLAY": "false",
        },
    )

    errors: list[Exception] = []

    def worker_get_provider():
        try:
            for _ in range(300):
                daemon._get_provider("local")
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    def worker_reload():
        try:
            for _ in range(300):
                daemon.mode = "local"
                daemon._reload_settings()
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    t1 = threading.Thread(target=worker_get_provider)
    t2 = threading.Thread(target=worker_reload)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert not errors
    assert created_providers


def test_reload_settings_serializes_parallel_calls(monkeypatch):
    windows_module = _load_windows_module()
    daemon = windows_module.PulseScribeWindows(
        mode="openai",
        streaming=False,
        overlay=False,
    )
    daemon.toggle_hotkey = windows_module._DEFAULT_TOGGLE_HOTKEY
    daemon.hold_hotkey = windows_module._DEFAULT_HOLD_HOTKEY

    restart_calls = 0
    restart_lock = threading.Lock()
    entered = threading.Event()
    release = threading.Event()

    def fake_restart():
        nonlocal restart_calls
        with restart_lock:
            restart_calls += 1

    def fake_read_env_file():
        entered.set()
        release.wait(timeout=1.0)
        return {
            "PULSESCRIBE_MODE": "openai",
            "PULSESCRIBE_TOGGLE_HOTKEY": "ctrl+alt+x",
            "PULSESCRIBE_HOLD_HOTKEY": windows_module._DEFAULT_HOLD_HOTKEY,
            "PULSESCRIBE_STREAMING": "false",
            "PULSESCRIBE_OVERLAY": "false",
        }

    daemon._restart_hotkey_listeners = fake_restart
    monkeypatch.setattr(
        windows_module,
        "load_environment",
        lambda override_existing=True: None,
    )
    monkeypatch.setattr(preferences, "read_env_file", fake_read_env_file)

    t1 = threading.Thread(target=daemon._reload_settings)
    t1.start()
    entered.wait(timeout=1.0)

    t2 = threading.Thread(target=daemon._reload_settings)
    t2.start()

    time.sleep(0.05)
    release.set()

    t1.join()
    t2.join()

    assert restart_calls == 1


def test_maybe_refine_skips_empty_transcript_without_state_change(monkeypatch):
    windows_module = _load_windows_module()
    daemon = windows_module.PulseScribeWindows(
        mode="openai",
        streaming=False,
        overlay=False,
        refine=True,
    )
    daemon._set_state(AppState.TRANSCRIBING)

    import refine.llm as refine_llm

    def fail_if_called(*_args, **_kwargs):  # pragma: no cover - defensive guard
        raise AssertionError("maybe_refine_transcript should not be called")

    monkeypatch.setattr(refine_llm, "maybe_refine_transcript", fail_if_called)

    assert daemon._maybe_refine("") == ""
    assert daemon.state == AppState.TRANSCRIBING
    assert daemon._last_was_refined is False


def test_save_to_history_uses_actual_refinement_status(monkeypatch):
    windows_module = _load_windows_module()
    daemon = windows_module.PulseScribeWindows(
        mode="openai",
        streaming=False,
        overlay=False,
        refine=True,
    )

    import refine.llm as refine_llm
    import utils.history as history_mod

    saved_entries: list[dict[str, object]] = []

    def fake_save_transcript(_text, **kwargs):
        saved_entries.append(kwargs)
        return True

    monkeypatch.setattr(history_mod, "save_transcript", fake_save_transcript)

    monkeypatch.setattr(
        refine_llm,
        "maybe_refine_transcript",
        lambda transcript, **_kwargs: transcript,
    )
    unchanged = daemon._maybe_refine("raw transcript")
    daemon._save_to_history(unchanged)

    monkeypatch.setattr(
        refine_llm,
        "maybe_refine_transcript",
        lambda transcript, **_kwargs: f"{transcript} refined",
    )
    changed = daemon._maybe_refine("raw transcript")
    daemon._save_to_history(changed)

    assert saved_entries[0]["refined"] is False
    assert saved_entries[1]["refined"] is True


def test_stop_recording_uses_run_snapshot_when_settings_change():
    windows_module = _load_windows_module()
    daemon = windows_module.PulseScribeWindows(
        mode="deepgram",
        streaming=False,
        overlay=False,
    )
    daemon._play_sound = lambda _name: None
    daemon._set_state(AppState.RECORDING)

    transcribe_calls = 0
    transcribe_lock = threading.Lock()

    def fake_transcribe_rest():
        nonlocal transcribe_calls
        with transcribe_lock:
            transcribe_calls += 1

    daemon._transcribe_rest = fake_transcribe_rest

    # Snapshot eines laufenden Streaming-Runs; Settings wurden danach geändert.
    daemon._run_streaming = True
    daemon._run_mode = "deepgram"
    daemon.streaming = False
    daemon.mode = "openai"

    daemon._stop_recording()
    time.sleep(0.05)

    assert daemon._recording_stop_event.is_set()
    assert transcribe_calls == 0


def test_ipc_test_id_is_only_set_after_successful_start():
    windows_module = _load_windows_module()
    daemon = windows_module.PulseScribeWindows(
        mode="openai",
        streaming=False,
        overlay=False,
    )

    responses = []

    class _IPC:
        def send_response(self, cmd_id, status, transcript="", error=None):
            responses.append((cmd_id, status, transcript, error))

    daemon._ipc_server = _IPC()
    daemon._start_recording = lambda: False

    daemon._start_ipc_test("abc123")

    assert daemon._ipc_test_cmd_id is None
    assert responses
    assert responses[0][0] == "abc123"


def test_set_state_uses_overlay_snapshot_to_avoid_none_race():
    windows_module = _load_windows_module()
    daemon = windows_module.PulseScribeWindows(
        mode="openai",
        streaming=False,
        overlay=False,
    )

    class _OverlayThatUnsetsItself:
        def __init__(self, owner):
            self.owner = owner
            self.update_calls = 0

        def __bool__(self):
            # Simuliert ein konkurrierendes Overlay-Disable zwischen Check und Use.
            self.owner._overlay = None
            return True

        def update_state(self, state, text):
            self.update_calls += 1

    overlay = _OverlayThatUnsetsItself(daemon)
    daemon._overlay = overlay

    daemon._set_state(AppState.LISTENING)

    assert overlay.update_calls == 1


def test_set_state_updates_overlay_before_tray_for_snappy_feedback():
    windows_module = _load_windows_module()
    daemon = windows_module.PulseScribeWindows(
        mode="openai",
        streaming=False,
        overlay=False,
    )

    events: list[tuple[str, object, object | None]] = []
    daemon._overlay_update_state = lambda state, text=None: events.append(
        ("overlay", state, text)
    )
    daemon._update_tray_icon = lambda text=None: events.append(("tray", text, None))

    daemon._set_state(AppState.LISTENING)

    assert events == [("overlay", "LISTENING", None), ("tray", None, None)]


def test_low_latency_input_stream_falls_back_when_driver_rejects_latency():
    windows_module = _load_windows_module()
    calls: list[dict[str, object]] = []

    class _FakeSoundDevice:
        def InputStream(self, **kwargs):
            calls.append(kwargs)
            if kwargs.get("latency") == "low":
                raise RuntimeError("latency unsupported")
            return "stream"

    stream = windows_module._create_low_latency_input_stream(
        _FakeSoundDevice(),
        device=1,
        samplerate=48_000,
        channels=1,
    )

    assert stream == "stream"
    assert calls[0]["latency"] == "low"
    assert "latency" not in calls[1]


def test_windows_audio_blocksize_targets_20ms_chunks():
    windows_module = _load_windows_module()

    assert windows_module._windows_audio_blocksize(48_000) == 960
    assert windows_module._windows_audio_blocksize(16_000) == 320


def test_stale_hotkey_listener_callbacks_are_ignored_after_restart(monkeypatch):
    windows_module = _load_windows_module()
    daemon = windows_module.PulseScribeWindows(
        mode="openai",
        streaming=False,
        overlay=False,
    )
    daemon.toggle_hotkey = "x"
    daemon.hold_hotkey = None

    class _FakeKeyCode:
        @staticmethod
        def from_char(char):
            return char

    class _FakeKeyboard:
        class Key:
            ctrl = object()
            alt = object()
            shift = object()
            cmd = object()

        KeyCode = _FakeKeyCode

        class Listener:
            def __init__(self, on_press, on_release):
                self.on_press = on_press
                self.on_release = on_release
                self.stopped = False

            def start(self):
                return None

            def stop(self):
                self.stopped = True

    monkeypatch.setitem(
        sys.modules,
        "pynput",
        types.SimpleNamespace(keyboard=_FakeKeyboard),
    )
    monkeypatch.setattr(
        windows_module,
        "parse_windows_hotkey_for_pynput",
        lambda _hotkey_str, _keyboard: {"x"},
    )

    monotonic_time = [0.0]

    def fake_monotonic():
        monotonic_time[0] += 1.0
        return monotonic_time[0]

    monkeypatch.setattr(windows_module.time, "monotonic", fake_monotonic)

    press_count = 0

    def fake_on_hotkey_press():
        nonlocal press_count
        press_count += 1

    daemon._on_hotkey_press = fake_on_hotkey_press

    class _Key:
        def __init__(self, char):
            self.char = char

    key = _Key("x")

    daemon._setup_hotkey()
    old_listener = daemon._hotkey_listeners[0]
    old_listener.on_press(key)
    assert press_count == 1

    daemon._restart_hotkey_listeners()
    new_listener = daemon._hotkey_listeners[0]

    # Nach Restart darf ein nachlaufender alter Listener keine Events mehr auslösen.
    old_listener.on_press(key)
    assert press_count == 1

    # Der neue Listener funktioniert weiterhin.
    new_listener.on_press(key)
    assert press_count == 2



def test_windows_env_flag_parses_common_bool_variants():
    windows_module = _load_windows_module()

    assert windows_module._env_flag("YES", default=False) is True
    assert windows_module._env_flag("off", default=True) is False
    assert windows_module._env_flag("0", default=True) is False
    assert windows_module._env_flag("invalid", default=True) is True
    assert windows_module._env_flag(None, default=False) is False



def test_reload_settings_uses_shared_bool_parsing(monkeypatch):
    windows_module = _load_windows_module()
    daemon = windows_module.PulseScribeWindows(
        mode="deepgram",
        streaming=False,
        overlay=True,
        refine=False,
    )
    daemon._overlay = object()

    stop_overlay_calls = 0

    def fake_stop_overlay():
        nonlocal stop_overlay_calls
        stop_overlay_calls += 1
        daemon._overlay = None

    daemon._stop_overlay = fake_stop_overlay
    daemon._restart_hotkey_listeners = lambda: None

    monkeypatch.setattr(
        windows_module,
        "load_environment",
        lambda override_existing=True: None,
    )
    monkeypatch.setattr(
        preferences,
        "read_env_file",
        lambda: {
            "PULSESCRIBE_MODE": "deepgram",
            "PULSESCRIBE_REFINE": "YES",
            "PULSESCRIBE_STREAMING": "0",
            "PULSESCRIBE_OVERLAY": "off",
            "PULSESCRIBE_TOGGLE_HOTKEY": daemon.toggle_hotkey,
            "PULSESCRIBE_HOLD_HOTKEY": daemon.hold_hotkey,
        },
    )

    daemon._reload_settings()

    assert daemon.refine is True
    assert daemon.streaming is False
    assert daemon.overlay_enabled is False
    assert stop_overlay_calls == 1



def test_set_state_updates_tray_title_when_same_state_text_changes(monkeypatch):
    windows_module = _load_windows_module()
    daemon = windows_module.PulseScribeWindows(
        mode="local",
        streaming=False,
        overlay=False,
    )
    daemon._tray = types.SimpleNamespace(icon=None, title="")
    daemon._create_icon = lambda _color: object()
    daemon._overlay_update_state = lambda _state, _text=None: None

    monkeypatch.setattr(windows_module, "PIL_Image", object(), raising=False)
    monkeypatch.setattr(windows_module, "PIL_ImageDraw", object(), raising=False)

    daemon._set_state(AppState.LOADING, "Loading large-v3...")
    first_title = daemon._tray.title

    daemon._set_state(AppState.LOADING, "Warming up...")
    second_title = daemon._tray.title

    assert first_title != second_title
    assert "Loading large-v3" in first_title
    assert "Warming up local model" in second_title


def test_setup_tray_uses_current_loading_state_and_clear_recovery_labels(monkeypatch):
    windows_module = _load_windows_module()
    daemon = windows_module.PulseScribeWindows(
        mode="openai",
        streaming=False,
        overlay=False,
    )
    daemon._state = AppState.LOADING
    daemon._last_status_text = "Starting up..."
    daemon._create_icon = lambda _color: object()

    class _FakeMenuItem:
        def __init__(self, text, action, enabled=True):
            self.text = text
            self.action = action
            self.enabled = enabled

    class _FakeIcon:
        def __init__(self, name, icon, title, menu):
            self.name = name
            self.icon = icon
            self.title = title
            self.menu = menu

    fake_pystray = types.SimpleNamespace(
        Menu=lambda *items: items,
        MenuItem=lambda text, action, enabled=True: _FakeMenuItem(
            text, action, enabled=enabled
        ),
        Icon=_FakeIcon,
    )
    fake_pystray.Menu.SEPARATOR = object()

    monkeypatch.setattr(windows_module, "_load_tray_dependencies", lambda: True)
    monkeypatch.setattr(windows_module, "pystray", fake_pystray, raising=False)

    daemon._setup_tray()

    menu_labels = [item.text for item in daemon._tray.menu if hasattr(item, "text")]
    assert daemon._tray.title.startswith("PulseScribe — Starting up PulseScribe")
    assert "Open Setup & Settings…" in menu_labels
    assert "Reload Settings & Retry" in menu_labels
    assert "Quit PulseScribe" in menu_labels


def test_start_recording_allows_retry_from_no_speech_state(monkeypatch):
    windows_module = _load_windows_module()
    daemon = windows_module.PulseScribeWindows(
        mode="openai",
        streaming=False,
        overlay=False,
    )
    daemon._state = AppState.NO_SPEECH
    daemon._play_sound = lambda _name: None

    fake_thread = types.SimpleNamespace(start=lambda: None)
    thread_calls: list[dict[str, object]] = []

    def _fake_thread_factory(*args, **kwargs):
        thread_calls.append(kwargs)
        return fake_thread

    monkeypatch.setattr(windows_module.threading, "Thread", _fake_thread_factory)

    assert daemon._start_recording() is True
    assert daemon.state == AppState.LISTENING
    assert thread_calls


def test_handle_no_speech_result_preserves_ipc_empty_result_behavior():
    windows_module = _load_windows_module()
    daemon = windows_module.PulseScribeWindows(
        mode="openai",
        streaming=False,
        overlay=False,
    )
    daemon._ipc_test_cmd_id = "cmd-1"
    daemon._ipc_server = object()

    with (
        patch.object(daemon, "_set_state") as mock_set_state,
        patch.object(daemon, "_enter_no_speech_state") as mock_no_speech,
    ):
        daemon._handle_no_speech_result()

    mock_set_state.assert_called_once_with(AppState.IDLE)
    mock_no_speech.assert_not_called()


def test_main_reconfigures_logging_and_parses_bool_env_variants(monkeypatch):
    windows_module = _load_windows_module()

    setup_calls: list[bool] = []
    daemon_kwargs: list[dict[str, object]] = []

    class _FakeDaemon:
        def __init__(self, **kwargs):
            daemon_kwargs.append(kwargs)

        def run(self):
            return None

    monkeypatch.setattr(
        windows_module,
        "setup_logging",
        lambda debug=False: setup_calls.append(debug),
    )
    monkeypatch.setattr(windows_module, "PulseScribeWindows", _FakeDaemon)
    monkeypatch.setattr(
        windows_module.sys,
        "argv",
        ["pulsescribe_windows.py", "--debug"],
        raising=False,
    )
    monkeypatch.setenv("PULSESCRIBE_DEBUG", "off")
    monkeypatch.setenv("PULSESCRIBE_REFINE", "YES")
    monkeypatch.setenv("PULSESCRIBE_STREAMING", "0")
    monkeypatch.setenv("PULSESCRIBE_OVERLAY", "off")

    windows_module.main()

    assert setup_calls[-1] is True
    assert daemon_kwargs
    assert daemon_kwargs[0]["refine"] is True
    assert daemon_kwargs[0]["streaming"] is False
    assert daemon_kwargs[0]["overlay"] is False
