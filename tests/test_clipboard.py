"""Tests fuer whisper_platform.clipboard."""

from __future__ import annotations

import ctypes
from types import SimpleNamespace
from unittest.mock import Mock


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
