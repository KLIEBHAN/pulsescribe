"""Tests für utils/windows_responsiveness.py."""

import ctypes
import logging
import sys

import utils.windows_responsiveness as responsiveness

logger = logging.getLogger("test.windows_responsiveness")


class _FakeDllFunc:
    """Aufzeichnender Fake für eine WinDLL-Funktion."""

    def __init__(self, return_value):
        self.return_value = return_value
        self.calls: list[tuple] = []
        self.argtypes = None
        self.restype = None

    def __call__(self, *args):
        self.calls.append(args)
        return self.return_value


class _FakeWinmm:
    def __init__(self, begin_result=0):
        self.timeBeginPeriod = _FakeDllFunc(begin_result)
        self.timeEndPeriod = _FakeDllFunc(0)


class _FakeKernel32:
    def __init__(self, *, handle=0x1234, set_result=1):
        self.GetCurrentProcess = _FakeDllFunc(handle)
        self.SetPriorityClass = _FakeDllFunc(set_result)


def _install_fake_windll(monkeypatch, dll_map):
    """Ersetzt ctypes.WinDLL hostunabhängig durch eine Fake-Factory.

    raising=False, weil ctypes.WinDLL auf Nicht-Windows-Hosts nicht existiert.
    """

    def fake_windll(name, **_kwargs):
        if name in dll_map:
            return dll_map[name]
        raise OSError(f"DLL nicht verfügbar: {name}")

    monkeypatch.setattr(ctypes, "WinDLL", fake_windll, raising=False)


def test_boost_is_noop_on_non_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    called = []
    monkeypatch.setattr(
        responsiveness, "_begin_timer_period", lambda _log: called.append("timer")
    )
    monkeypatch.setattr(
        responsiveness, "_raise_priority_class", lambda _log: called.append("prio")
    )

    result = responsiveness.apply_windows_responsiveness_boost(logger)

    assert result == {"timer_resolution": False, "priority_class": False}
    assert called == []


def test_boost_can_be_disabled_via_env(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("PULSESCRIBE_WINDOWS_RESPONSIVENESS_BOOST", "false")
    called = []
    monkeypatch.setattr(
        responsiveness, "_begin_timer_period", lambda _log: called.append("timer")
    )
    monkeypatch.setattr(
        responsiveness, "_raise_priority_class", lambda _log: called.append("prio")
    )

    result = responsiveness.apply_windows_responsiveness_boost(logger)

    assert result == {"timer_resolution": False, "priority_class": False}
    assert called == []


def test_boost_applies_both_tweaks_on_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.delenv("PULSESCRIBE_WINDOWS_RESPONSIVENESS_BOOST", raising=False)
    monkeypatch.setattr(responsiveness, "_begin_timer_period", lambda _log: True)
    monkeypatch.setattr(responsiveness, "_raise_priority_class", lambda _log: True)

    result = responsiveness.apply_windows_responsiveness_boost(logger)

    assert result == {"timer_resolution": True, "priority_class": True}


def test_boost_reports_partial_failure(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.delenv("PULSESCRIBE_WINDOWS_RESPONSIVENESS_BOOST", raising=False)
    monkeypatch.setattr(responsiveness, "_begin_timer_period", lambda _log: False)
    monkeypatch.setattr(responsiveness, "_raise_priority_class", lambda _log: True)

    result = responsiveness.apply_windows_responsiveness_boost(logger)

    assert result == {"timer_resolution": False, "priority_class": True}


def test_begin_timer_period_handles_missing_winmm(monkeypatch):
    """Fehlende/abgelehnte winmm-DLL -> False, kein Crash (hostunabhängig)."""
    monkeypatch.setattr(responsiveness, "_timer_period_active", False)
    _install_fake_windll(monkeypatch, {})  # jede DLL wirft OSError

    assert responsiveness._begin_timer_period(logger) is False


def test_raise_priority_class_handles_missing_kernel32(monkeypatch):
    """Fehlende/abgelehnte kernel32-DLL -> False, kein Crash (hostunabhängig)."""
    _install_fake_windll(monkeypatch, {})  # jede DLL wirft OSError

    assert responsiveness._raise_priority_class(logger) is False


def test_begin_timer_period_success_sets_signatures_and_registers_end(monkeypatch):
    """Erfolgspfad: 1ms angefordert, Signaturen gesetzt, timeEndPeriod gepaart."""
    monkeypatch.setattr(responsiveness, "_timer_period_active", False)
    winmm = _FakeWinmm(begin_result=0)
    _install_fake_windll(monkeypatch, {"winmm": winmm})

    registered = []
    monkeypatch.setattr(
        responsiveness.atexit, "register", lambda fn: registered.append(fn)
    )

    assert responsiveness._begin_timer_period(logger) is True
    assert winmm.timeBeginPeriod.calls == [(1,)]
    assert winmm.timeBeginPeriod.argtypes is not None
    assert winmm.timeBeginPeriod.restype is not None
    assert winmm.timeEndPeriod.argtypes is not None

    # Genau ein atexit-Pairing; Ausführung ruft timeEndPeriod(1) genau einmal.
    assert len(registered) == 1
    registered[0]()
    registered[0]()  # idempotent
    assert winmm.timeEndPeriod.calls == [(1,)]


def test_begin_timer_period_is_idempotent_across_calls(monkeypatch):
    """Zweiter Aufruf fordert keine zweite Timer-Periode an."""
    monkeypatch.setattr(responsiveness, "_timer_period_active", False)
    winmm = _FakeWinmm(begin_result=0)
    _install_fake_windll(monkeypatch, {"winmm": winmm})
    monkeypatch.setattr(responsiveness.atexit, "register", lambda fn: None)

    assert responsiveness._begin_timer_period(logger) is True
    assert responsiveness._begin_timer_period(logger) is True
    assert winmm.timeBeginPeriod.calls == [(1,)]


def test_begin_timer_period_rejected_result_returns_false(monkeypatch):
    """timeBeginPeriod != TIMERR_NOERROR -> False, kein atexit-Pairing."""
    monkeypatch.setattr(responsiveness, "_timer_period_active", False)
    winmm = _FakeWinmm(begin_result=97)  # TIMERR_NOCANDO
    _install_fake_windll(monkeypatch, {"winmm": winmm})

    registered = []
    monkeypatch.setattr(
        responsiveness.atexit, "register", lambda fn: registered.append(fn)
    )

    assert responsiveness._begin_timer_period(logger) is False
    assert registered == []


def test_raise_priority_class_success_uses_handle_and_above_normal(monkeypatch):
    """Erfolgspfad: SetPriorityClass(handle, ABOVE_NORMAL) mit Signaturen."""
    kernel32 = _FakeKernel32(handle=0xDEADBEEF, set_result=1)
    _install_fake_windll(monkeypatch, {"kernel32": kernel32})

    assert responsiveness._raise_priority_class(logger) is True
    assert kernel32.GetCurrentProcess.calls == [()]
    assert kernel32.SetPriorityClass.calls == [(0xDEADBEEF, 0x00008000)]
    assert kernel32.SetPriorityClass.argtypes is not None
    assert kernel32.SetPriorityClass.restype is not None
    assert kernel32.GetCurrentProcess.restype is not None


def test_raise_priority_class_failure_result_returns_false(monkeypatch):
    """SetPriorityClass == 0 -> False (best-effort, kein Crash)."""
    kernel32 = _FakeKernel32(set_result=0)
    _install_fake_windll(monkeypatch, {"kernel32": kernel32})

    assert responsiveness._raise_priority_class(logger) is False
