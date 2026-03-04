import contextlib
import importlib.util
import io
import sys
import threading
import time
from pathlib import Path

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
