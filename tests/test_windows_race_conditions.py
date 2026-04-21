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
