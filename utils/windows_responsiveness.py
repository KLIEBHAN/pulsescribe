"""Best-effort Windows-Responsiveness-Tweaks für den Daemon-Prozess.

Zwei kleine, risikoarme System-Tweaks für geringere gefühlte Latenz:

1. ``timeBeginPeriod(1)``: Der Windows-Systemtimer tickt standardmäßig nur
   alle ~15.6ms. Kurze Timeouts (``Event.wait``, ``queue.get(timeout=...)``,
   20ms-Audio-Polls) werden dadurch spürbar gestreckt. Mit 1ms-Auflösung
   greifen die kurzen Polls im Hotkey-/Audio-/Stop-Pfad deutlich präziser.
   (``time.sleep`` ist seit Python 3.11 bereits hochauflösend, Lock-/Event-
   Timeouts sind es nicht.)

2. ``ABOVE_NORMAL_PRIORITY_CLASS``: Hält Hotkey-Reaktion und Audio-Callbacks
   snappy, wenn das System unter Last steht (Builds, Browser, Spiele).

Beides ist best-effort (Fehler werden nur geloggt) und kann per
``PULSESCRIBE_WINDOWS_RESPONSIVENESS_BOOST=false`` deaktiviert werden.
"""

from __future__ import annotations

import atexit
import logging
import os
import sys

from utils.env import parse_bool

_ABOVE_NORMAL_PRIORITY_CLASS = 0x00008000
_TIMER_RESOLUTION_MS = 1
_TIMERR_NOERROR = 0

_timer_period_active = False


def _boost_enabled() -> bool:
    """Return whether the responsiveness boost is enabled (default: on)."""
    parsed = parse_bool(os.getenv("PULSESCRIBE_WINDOWS_RESPONSIVENESS_BOOST"))
    return True if parsed is None else parsed


def _begin_timer_period(logger: logging.Logger) -> bool:
    """Raise the system timer resolution to 1ms (paired with timeEndPeriod)."""
    global _timer_period_active
    if _timer_period_active:
        return True
    try:
        import ctypes
        from ctypes import wintypes

        winmm = ctypes.WinDLL("winmm")
        # Explizite Signaturen: ctypes' c_int-Defaults sind für WinAPI-Typen
        # auf 64-Bit-Windows nicht garantiert korrekt.
        winmm.timeBeginPeriod.argtypes = [wintypes.UINT]
        winmm.timeBeginPeriod.restype = wintypes.UINT
        winmm.timeEndPeriod.argtypes = [wintypes.UINT]
        winmm.timeEndPeriod.restype = wintypes.UINT
        if winmm.timeBeginPeriod(_TIMER_RESOLUTION_MS) != _TIMERR_NOERROR:
            logger.debug("timeBeginPeriod(%d) abgelehnt", _TIMER_RESOLUTION_MS)
            return False
        _timer_period_active = True

        def _end_timer_period() -> None:
            global _timer_period_active
            if not _timer_period_active:
                return
            _timer_period_active = False
            try:
                winmm.timeEndPeriod(_TIMER_RESOLUTION_MS)
            except Exception:
                pass

        atexit.register(_end_timer_period)
        return True
    except Exception as e:
        logger.debug("timeBeginPeriod fehlgeschlagen: %s", e)
        return False


def _raise_priority_class(logger: logging.Logger) -> bool:
    """Raise the process priority class to ABOVE_NORMAL (best-effort)."""
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        # HANDLE ist pointer-sized: ohne explizite Signaturen würde ctypes den
        # Pseudo-Handle auf 64-Bit-Windows als c_int truncaten.
        kernel32.GetCurrentProcess.argtypes = []
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        kernel32.SetPriorityClass.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel32.SetPriorityClass.restype = wintypes.BOOL
        handle = kernel32.GetCurrentProcess()
        if not kernel32.SetPriorityClass(handle, _ABOVE_NORMAL_PRIORITY_CLASS):
            logger.debug(
                "SetPriorityClass fehlgeschlagen (WinError %d)",
                ctypes.get_last_error(),
            )
            return False
        return True
    except Exception as e:
        logger.debug("SetPriorityClass fehlgeschlagen: %s", e)
        return False


def apply_windows_responsiveness_boost(
    logger: logging.Logger,
) -> dict[str, bool]:
    """Apply best-effort responsiveness tweaks; returns what actually stuck.

    No-op on non-Windows platforms or when disabled via
    PULSESCRIBE_WINDOWS_RESPONSIVENESS_BOOST.
    """
    results = {"timer_resolution": False, "priority_class": False}
    if sys.platform != "win32" or not _boost_enabled():
        return results

    results["timer_resolution"] = _begin_timer_period(logger)
    results["priority_class"] = _raise_priority_class(logger)
    logger.info(
        "Windows-Responsiveness-Boost: timer=%s, priority=%s",
        "1ms" if results["timer_resolution"] else "default",
        "above_normal" if results["priority_class"] else "normal",
    )
    return results


__all__ = ["apply_windows_responsiveness_boost"]
