"""Tests fuer whisper_platform.clipboard."""

from __future__ import annotations

import ctypes
from types import SimpleNamespace
from unittest.mock import Mock


class _FakeCFunction:
    def __init__(self, impl):
        self._impl = impl
        self.restype = None
        self.argtypes = None

    def __call__(self, *args):
        result = self._impl(*args)
        if self.restype is None and isinstance(result, int):
            return ctypes.c_int(result).value
        return result


def test_windows_clipboard_copy_retries_until_clipboard_is_available(monkeypatch):
    from whisper_platform.clipboard import WindowsClipboard
    import whisper_platform.clipboard as clipboard

    open_calls = {"count": 0}

    def open_clipboard(_handle) -> int:
        open_calls["count"] += 1
        return 1 if open_calls["count"] == 3 else 0

    user32 = SimpleNamespace(
        OpenClipboard=open_clipboard,
        EmptyClipboard=lambda: 1,
        SetClipboardData=lambda _fmt, _handle: 1,
        CloseClipboard=Mock(return_value=1),
    )
    kernel32 = SimpleNamespace(
        GlobalAlloc=lambda _flags, _size: 1,
        GlobalLock=lambda _handle: 1,
        GlobalUnlock=lambda _handle: 1,
        GlobalFree=lambda _handle: 1,
    )

    monkeypatch.setattr(
        ctypes,
        "windll",
        SimpleNamespace(user32=user32, kernel32=kernel32),
        raising=False,
    )
    monkeypatch.setattr(ctypes, "memmove", lambda _dest, _src, _size: None)
    sleep_mock = Mock()
    monkeypatch.setattr(clipboard.time, "sleep", sleep_mock)

    assert WindowsClipboard().copy("hello") is True
    assert open_calls["count"] == 3
    assert sleep_mock.call_count == 2


def test_windows_clipboard_paste_retries_until_clipboard_is_available(monkeypatch):
    from whisper_platform.clipboard import WindowsClipboard
    import whisper_platform.clipboard as clipboard

    open_calls = {"count": 0}

    def open_clipboard(_handle) -> int:
        open_calls["count"] += 1
        return 1 if open_calls["count"] == 2 else 0

    user32 = SimpleNamespace(
        OpenClipboard=open_clipboard,
        GetClipboardData=lambda _fmt: 123,
        CloseClipboard=Mock(return_value=1),
    )
    kernel32 = SimpleNamespace(
        GlobalLock=lambda _handle: 456,
        GlobalUnlock=lambda _handle: 1,
    )

    monkeypatch.setattr(
        ctypes,
        "windll",
        SimpleNamespace(user32=user32, kernel32=kernel32),
        raising=False,
    )
    monkeypatch.setattr(ctypes, "wstring_at", lambda _ptr: "clipboard text")
    monkeypatch.setattr(clipboard.time, "sleep", lambda _delay: None)

    assert WindowsClipboard().paste() == "clipboard text"
    assert open_calls["count"] == 2


def test_windows_clipboard_copy_configures_pointer_sized_signatures(monkeypatch):
    from whisper_platform.clipboard import WindowsClipboard

    high_handle = 0x1_0000_0001
    high_pointer = 0x2_0000_0002

    user32 = SimpleNamespace(
        OpenClipboard=_FakeCFunction(lambda _handle: 1),
        EmptyClipboard=_FakeCFunction(lambda: 1),
        SetClipboardData=_FakeCFunction(
            lambda _fmt, handle: 1 if handle == high_handle else 0
        ),
        CloseClipboard=_FakeCFunction(lambda: 1),
    )
    kernel32 = SimpleNamespace(
        GlobalAlloc=_FakeCFunction(lambda _flags, _size: high_handle),
        GlobalLock=_FakeCFunction(
            lambda handle: high_pointer if handle == high_handle else 0
        ),
        GlobalUnlock=_FakeCFunction(lambda _handle: 1),
        GlobalFree=_FakeCFunction(lambda _handle: 0),
    )

    monkeypatch.setattr(
        ctypes,
        "windll",
        SimpleNamespace(user32=user32, kernel32=kernel32),
        raising=False,
    )
    monkeypatch.setattr(ctypes, "memmove", lambda _dest, _src, _size: None)

    assert WindowsClipboard().copy("hello") is True
    assert user32.SetClipboardData.argtypes is not None
    assert kernel32.GlobalAlloc.restype is not None
    assert kernel32.GlobalLock.restype is not None


def test_windows_clipboard_paste_configures_pointer_sized_signatures(monkeypatch):
    from whisper_platform.clipboard import WindowsClipboard

    high_handle = 0x1_0000_0001
    high_pointer = 0x2_0000_0002

    user32 = SimpleNamespace(
        OpenClipboard=_FakeCFunction(lambda _handle: 1),
        GetClipboardData=_FakeCFunction(lambda _fmt: high_handle),
        CloseClipboard=_FakeCFunction(lambda: 1),
    )
    kernel32 = SimpleNamespace(
        GlobalLock=_FakeCFunction(
            lambda handle: high_pointer if handle == high_handle else 0
        ),
        GlobalUnlock=_FakeCFunction(lambda _handle: 1),
    )

    monkeypatch.setattr(
        ctypes,
        "windll",
        SimpleNamespace(user32=user32, kernel32=kernel32),
        raising=False,
    )
    monkeypatch.setattr(ctypes, "wstring_at", lambda ptr: "clipboard text" if ptr == high_pointer else "")

    assert WindowsClipboard().paste() == "clipboard text"
    assert user32.GetClipboardData.restype is not None
    assert kernel32.GlobalLock.restype is not None
