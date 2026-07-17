"""Tests für die entkoppelte Hotkey-/Tray-Verarbeitung des Windows-Daemons.

pynput ruft Callbacks auf Windows synchron aus dem Low-Level-Keyboard-Hook auf.
Diese Tests stellen sicher, dass Hook-Callbacks nie auf schwere Aktionen warten
(Start/Stop, Tray, History-IO) und dass die Reihenfolge der Aktionen erhalten
bleibt.
"""

import threading
import time

from utils.state import AppState

from tests.test_windows_race_conditions import _load_windows_module


def _make_daemon(**kwargs):
    windows_module = _load_windows_module()
    defaults = {"mode": "openai", "streaming": False, "overlay": False}
    defaults.update(kwargs)
    return windows_module, windows_module.PulseScribeWindows(**defaults)


# =============================================================================
# Hotkey-Action-Dispatcher
# =============================================================================


def test_toggle_dispatch_returns_fast_even_when_action_blocks():
    _, daemon = _make_daemon()

    action_started = threading.Event()
    action_release = threading.Event()
    action_done = threading.Event()

    def blocking_press():
        action_started.set()
        action_release.wait(timeout=2.0)
        action_done.set()

    daemon._on_hotkey_press = blocking_press

    start = time.monotonic()
    daemon._dispatch_windows_hotkey_match("toggle", "pynput:toggle:x")
    elapsed = time.monotonic() - start

    # Hook-Callback darf nicht auf die Aktion warten
    assert elapsed < 0.1
    assert action_started.wait(timeout=1.0)

    action_release.set()
    assert action_done.wait(timeout=1.0)


def test_hotkey_actions_run_in_fifo_order():
    _, daemon = _make_daemon()

    order: list[str] = []
    done = threading.Event()

    daemon._dispatch_hotkey_action(lambda: order.append("start"), "hold-start")
    daemon._dispatch_hotkey_action(lambda: order.append("stop"), "hold-stop")
    daemon._dispatch_hotkey_action(done.set, "sentinel")

    assert done.wait(timeout=1.0)
    assert order == ["start", "stop"]


def test_hotkey_worker_survives_action_exception():
    _, daemon = _make_daemon()

    done = threading.Event()
    daemon._dispatch_hotkey_action(
        lambda: (_ for _ in ()).throw(RuntimeError("boom")), "broken"
    )
    daemon._dispatch_hotkey_action(done.set, "after-error")

    assert done.wait(timeout=1.0)


def test_hold_release_dispatches_stop_off_hook_thread():
    windows_module, daemon = _make_daemon()
    daemon._play_sound = lambda _name: None

    # Hold aktiv markieren wie nach erfolgreichem Start
    source_id = "pynput:hold:ctrl+win"
    assert daemon._hold_state.should_start(source_id)
    daemon._hold_state.mark_started()
    daemon._set_state(AppState.RECORDING)

    stop_called = threading.Event()
    daemon._stop_recording_from_hotkey = stop_called.set

    class _FakeKeyboard:
        class Key:
            ctrl = "ctrl"

    parsed_hotkeys = [({"ctrl"}, "hold", source_id)]
    current_keys = {"ctrl": time.monotonic()}

    start = time.monotonic()
    daemon._handle_windows_hotkey_release(
        "ctrl", _FakeKeyboard, parsed_hotkeys, current_keys, set()
    )
    elapsed = time.monotonic() - start

    assert elapsed < 0.1
    assert stop_called.wait(timeout=1.0)


def test_quick_tap_release_before_start_aborts_cleanly():
    """Press+Release schneller als der Dispatcher: kein Stop, Start bricht ab."""
    _, daemon = _make_daemon()

    source_id = "pynput:hold:ctrl+win"
    started: list[str] = []
    daemon._start_recording = lambda: started.append("started") or True

    # Press: Buchhaltung inline, Start in Queue
    assert daemon._hold_state.should_start(source_id)

    # Release VOR Ausführung des Starts: started_by_hold ist noch False
    assert daemon._hold_state.should_stop(source_id) is False

    # Start-Aktion läuft jetzt - is_active() ist False, also kein Recording
    daemon._start_recording_from_hold(source_id)
    assert started == []


# =============================================================================
# Tray-Update-Worker (coalesced, latest-wins)
# =============================================================================


def test_tray_worker_applies_latest_state():
    _, daemon = _make_daemon()

    applied: list[tuple[object, object]] = []
    applied_event = threading.Event()

    def fake_update(state, text=None):
        applied.append((state, text))
        applied_event.set()

    daemon._update_tray_icon = fake_update

    daemon._set_state(AppState.LISTENING)
    daemon._set_state(AppState.RECORDING)
    daemon._start_tray_update_worker()

    assert applied_event.wait(timeout=1.0)
    time.sleep(0.05)

    # Latest-wins: Der Worker liest den aktuellsten State
    assert applied[-1][0] == AppState.RECORDING

    daemon._stop_event.set()
    daemon._tray_update_signal.set()


def test_set_state_does_not_touch_tray_synchronously():
    _, daemon = _make_daemon()

    def fail_update(state, text=None):
        raise AssertionError("Tray-Update darf nicht synchron laufen")

    daemon._update_tray_icon = fail_update

    daemon._set_state(AppState.LISTENING)
    assert daemon._tray_update_signal.is_set()


# =============================================================================
# Result-Handling: Paste vor History-IO
# =============================================================================


def test_handle_result_pastes_before_history_io(monkeypatch):
    windows_module, daemon = _make_daemon()
    daemon._play_sound = lambda _name: None
    daemon.auto_paste = True

    order: list[str] = []

    monkeypatch.setattr(
        windows_module,
        "paste_transcript",
        lambda _text: order.append("paste") or True,
    )
    daemon._save_to_history = lambda _text, **_kwargs: order.append("history")

    daemon._handle_result("hello world")

    assert order == ["paste", "history"]
    assert daemon.state == AppState.DONE


# =============================================================================
# Press-Zyklus-Tracking statt Zeit-Debounce (V7)
# =============================================================================


class _FakeKeyboardForCycles:
    class Key:
        ctrl = "ctrl"
        alt = "alt"
        shift = "shift"
        cmd = "cmd"


def _press(daemon, key, parsed_hotkeys, current_keys, triggered_cycles):
    daemon._handle_windows_hotkey_press(
        key, _FakeKeyboardForCycles, parsed_hotkeys, current_keys, triggered_cycles
    )


def _release(daemon, key, parsed_hotkeys, current_keys, triggered_cycles):
    daemon._handle_windows_hotkey_release(
        key, _FakeKeyboardForCycles, parsed_hotkeys, current_keys, triggered_cycles
    )


def test_auto_repeat_of_held_hotkey_does_not_retrigger():
    """Auto-Repeat der gehaltenen Kombo löst den Toggle nur einmal aus."""
    _, daemon = _make_daemon()
    dispatched: list[str] = []
    daemon._dispatch_hotkey_action = lambda _action, desc: dispatched.append(desc)

    source_id = "pynput:toggle:ctrl+alt+r"
    parsed_hotkeys = [({"ctrl", "alt", "r"}, "toggle", source_id)]
    current_keys: dict = {}
    triggered_cycles: set = set()

    for key in ("ctrl", "alt", "r"):
        _press(daemon, key, parsed_hotkeys, current_keys, triggered_cycles)
    # Auto-Repeat: Windows liefert wiederholte Press-Events der gehaltenen Tasten
    for _ in range(3):
        _press(daemon, "r", parsed_hotkeys, current_keys, triggered_cycles)
        _press(daemon, "ctrl", parsed_hotkeys, current_keys, triggered_cycles)

    assert dispatched == ["toggle"]


def test_hotkey_retriggers_immediately_after_release_without_debounce():
    """Nach echtem Release ist die Kombo SOFORT wieder auslösbar (kein 300ms-Debounce).

    Regressionstest für V7: Vorher verschluckte das globale Zeit-Debounce einen
    legitimen zweiten Toggle-Press innerhalb von 300ms (z.B. Back-to-back-Diktat).
    """
    _, daemon = _make_daemon()
    dispatched: list[str] = []
    daemon._dispatch_hotkey_action = lambda _action, desc: dispatched.append(desc)

    source_id = "pynput:toggle:ctrl+alt+r"
    parsed_hotkeys = [({"ctrl", "alt", "r"}, "toggle", source_id)]
    current_keys: dict = {}
    triggered_cycles: set = set()

    # Erster Zyklus: Ctrl+Alt gehalten, R gedrückt
    for key in ("ctrl", "alt", "r"):
        _press(daemon, key, parsed_hotkeys, current_keys, triggered_cycles)
    # R loslassen (Modifier bleiben gehalten), sofort erneut drücken
    _release(daemon, "r", parsed_hotkeys, current_keys, triggered_cycles)
    _press(daemon, "r", parsed_hotkeys, current_keys, triggered_cycles)

    assert dispatched == ["toggle", "toggle"]


def test_stale_keys_release_stuck_trigger_cycles():
    """Verlorene Release-Events (Fokuswechsel) blockieren die Kombo nicht dauerhaft."""
    windows_module, daemon = _make_daemon()
    dispatched: list[str] = []
    daemon._dispatch_hotkey_action = lambda _action, desc: dispatched.append(desc)

    source_id = "pynput:toggle:ctrl+alt+r"
    parsed_hotkeys = [({"ctrl", "alt", "r"}, "toggle", source_id)]
    current_keys: dict = {}
    triggered_cycles: set = set()

    for key in ("ctrl", "alt", "r"):
        _press(daemon, key, parsed_hotkeys, current_keys, triggered_cycles)
    assert dispatched == ["toggle"]

    # Release-Events gingen verloren; Keys altern über das Stale-Timeout hinaus
    stale_age = windows_module._KEY_STALE_TIMEOUT_SEC + 0.1
    for key in list(current_keys):
        current_keys[key] -= stale_age

    for key in ("ctrl", "alt", "r"):
        _press(daemon, key, parsed_hotkeys, current_keys, triggered_cycles)

    assert dispatched == ["toggle", "toggle"]
