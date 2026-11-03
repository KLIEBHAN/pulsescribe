"""Keep PortAudio's device list in step with macOS audio hardware changes.

PortAudio enumerates audio devices once when it initialises. A microphone that
appears or disappears later (for example when docking a MacBook) stays invisible,
and the stale default device fails with "Internal PortAudio error [-9986]" until
the app restarts.

Use ``open_input_stream(sd, create)`` for every input stream and
``release_input_stream(stream)`` once that stream closed cleanly. Before opening,
the CoreAudio device list and default input are compared with the state at the
last PortAudio initialisation; after a change PortAudio is re-initialised. That
only happens while no tracked stream is open, so a stream whose close hangs keeps
re-initialisation disabled. ``refresh_if_devices_changed(sd)`` does the same check
before listing microphones. Outside macOS all functions just open streams.
"""

from __future__ import annotations

import ctypes
import logging
import struct
import sys
import threading
from collections.abc import Callable
from typing import Any, TypeVar

logger = logging.getLogger("pulsescribe.audio")

StreamT = TypeVar("StreamT")

_lock = threading.Lock()
_open_streams: set[int] = set()
_known_fingerprint: tuple[tuple[int, ...], int] | None = None
_coreaudio: Any | None = None


class _PropertyAddress(ctypes.Structure):
    _fields_ = [
        ("mSelector", ctypes.c_uint32),
        ("mScope", ctypes.c_uint32),
        ("mElement", ctypes.c_uint32),
    ]


def _fourcc(code: str) -> int:
    return struct.unpack(">I", code.encode("ascii"))[0]


_SYSTEM_OBJECT = 1  # kAudioObjectSystemObject
_DEVICES = _fourcc("dev#")  # kAudioHardwarePropertyDevices
_DEFAULT_INPUT = _fourcc("dIn ")  # kAudioHardwarePropertyDefaultInputDevice
_SCOPE_GLOBAL = _fourcc("glob")


def _load_coreaudio() -> Any:
    global _coreaudio
    if _coreaudio is None:
        lib = ctypes.CDLL("/System/Library/Frameworks/CoreAudio.framework/CoreAudio")
        size_fn = lib.AudioObjectGetPropertyDataSize
        size_fn.argtypes = [
            ctypes.c_uint32,
            ctypes.POINTER(_PropertyAddress),
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_uint32),
        ]
        size_fn.restype = ctypes.c_int32
        data_fn = lib.AudioObjectGetPropertyData
        data_fn.argtypes = [
            ctypes.c_uint32,
            ctypes.POINTER(_PropertyAddress),
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_uint32),
            ctypes.c_void_p,
        ]
        data_fn.restype = ctypes.c_int32
        _coreaudio = lib
    return _coreaudio


def device_fingerprint() -> tuple[tuple[int, ...], int] | None:
    """Return (CoreAudio device IDs, default input ID); None outside macOS or on errors.

    Costs well under a millisecond, so it runs before every input stream.
    """
    if sys.platform != "darwin":
        return None
    try:
        lib = _load_coreaudio()
        address = _PropertyAddress(_DEVICES, _SCOPE_GLOBAL, 0)
        size = ctypes.c_uint32(0)
        if lib.AudioObjectGetPropertyDataSize(
            _SYSTEM_OBJECT, ctypes.byref(address), 0, None, ctypes.byref(size)
        ):
            return None
        ids = (ctypes.c_uint32 * (size.value // 4))()
        if lib.AudioObjectGetPropertyData(
            _SYSTEM_OBJECT, ctypes.byref(address), 0, None, ctypes.byref(size), ids
        ):
            return None
        address = _PropertyAddress(_DEFAULT_INPUT, _SCOPE_GLOBAL, 0)
        default_input = ctypes.c_uint32(0)
        size = ctypes.c_uint32(ctypes.sizeof(default_input))
        if lib.AudioObjectGetPropertyData(
            _SYSTEM_OBJECT,
            ctypes.byref(address),
            0,
            None,
            ctypes.byref(size),
            ctypes.byref(default_input),
        ):
            return None
        return tuple(int(i) for i in ids), int(default_input.value)
    except (OSError, AttributeError, ValueError):
        return None


def _reinitialize_locked(sd: Any, fingerprint: tuple[tuple[int, ...], int] | None) -> bool:
    """Re-initialise PortAudio; caller holds ``_lock`` and ensured no stream is open."""
    global _known_fingerprint
    terminate = getattr(sd, "_terminate", None)
    initialize = getattr(sd, "_initialize", None)
    if not (callable(terminate) and callable(initialize)):
        return False
    terminate()
    initialize()
    _known_fingerprint = fingerprint
    from config import reset_input_device_cache

    reset_input_device_cache()
    logger.info("Audiogeräte geändert – Geräteliste neu eingelesen")
    return True


def _refresh_locked(sd: Any) -> bool:
    fingerprint = device_fingerprint()
    if fingerprint is None or fingerprint == _known_fingerprint or _open_streams:
        return False
    return _reinitialize_locked(sd, fingerprint)


def refresh_if_devices_changed(sd: Any) -> bool:
    """Re-read PortAudio's devices after a hardware change while no stream is open."""
    with _lock:
        return _refresh_locked(sd)


def open_input_stream(sd: Any, create: Callable[[], StreamT]) -> StreamT:
    """Open a tracked input stream via ``create()`` with up-to-date devices.

    ``create`` must resolve the device itself (e.g. via ``get_input_device()``),
    because indices change after a refresh. On macOS a PortAudio error while
    opening triggers one re-initialisation and a second attempt.
    """
    with _lock:
        _refresh_locked(sd)
        try:
            stream = create()
        except Exception as error:
            portaudio_error = getattr(sd, "PortAudioError", None)
            if (
                device_fingerprint() is None
                or _open_streams
                or not (isinstance(portaudio_error, type) and isinstance(error, portaudio_error))
                or not _reinitialize_locked(sd, device_fingerprint())
            ):
                raise
            logger.info("Mikrofon nach PortAudio-Fehler erneut geöffnet: %s", error)
            stream = create()
        _open_streams.add(id(stream))
        return stream


def release_input_stream(stream: Any) -> None:
    """Mark a stream from ``open_input_stream`` as cleanly closed."""
    with _lock:
        _open_streams.discard(id(stream))


__all__ = [
    "device_fingerprint",
    "open_input_stream",
    "refresh_if_devices_changed",
    "release_input_stream",
]
