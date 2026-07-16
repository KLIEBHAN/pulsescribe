"""Tests für utils/windows_responsiveness.py."""

import logging
import sys

import utils.windows_responsiveness as responsiveness

logger = logging.getLogger("test.windows_responsiveness")


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
    """Auf Nicht-Windows-Systemen schlägt ctypes.WinDLL fehl -> False, kein Crash."""
    monkeypatch.setattr(responsiveness, "_timer_period_active", False)

    assert responsiveness._begin_timer_period(logger) is False


def test_raise_priority_class_handles_missing_kernel32():
    """Auf Nicht-Windows-Systemen schlägt ctypes.WinDLL fehl -> False, kein Crash."""
    assert responsiveness._raise_priority_class(logger) is False
