"""Tests fuer whisper_platform.clipboard."""

from __future__ import annotations

import ctypes
import subprocess
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


def test_macos_clipboard_copy_uses_utf8_locale(monkeypatch):
    from whisper_platform.clipboard import MacOSClipboard

    observed: dict[str, object] = {}

    def _mock_run(cmd, *args, **kwargs):
        observed["cmd"] = cmd
        observed["input"] = kwargs["input"]
        observed["timeout"] = kwargs["timeout"]
        observed["env"] = kwargs["env"]
        return subprocess.CompletedProcess(cmd, 0, stdout=b"", stderr=b"")

    monkeypatch.setattr(subprocess, "run", _mock_run)

    assert MacOSClipboard().copy("Grüße 你好") is True
    assert observed["cmd"] == ["pbcopy"]
    assert observed["input"] == "Grüße 你好".encode("utf-8")
    assert observed["timeout"] == 2
    assert observed["env"]["LANG"] == "en_US.UTF-8"
    assert observed["env"]["LC_ALL"] == "en_US.UTF-8"


def test_macos_clipboard_paste_uses_utf8_locale(monkeypatch):
    from whisper_platform.clipboard import MacOSClipboard

    observed: dict[str, object] = {}

    def _mock_run(cmd, *args, **kwargs):
        observed["cmd"] = cmd
        observed["timeout"] = kwargs["timeout"]
        observed["env"] = kwargs["env"]
        return subprocess.CompletedProcess(
            cmd,
            0,
            stdout="Grüße 你好".encode("utf-8"),
            stderr=b"",
        )

    monkeypatch.setattr(subprocess, "run", _mock_run)

    assert MacOSClipboard().paste() == "Grüße 你好"
    assert observed["cmd"] == ["pbpaste"]
    assert observed["timeout"] == 2
    assert observed["env"]["LANG"] == "en_US.UTF-8"
    assert observed["env"]["LC_ALL"] == "en_US.UTF-8"


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


def test_windows_clipboard_copy_does_not_clear_clipboard_when_alloc_fails(monkeypatch):
    from whisper_platform.clipboard import WindowsClipboard

    user32 = SimpleNamespace(
        OpenClipboard=Mock(return_value=1),
        EmptyClipboard=Mock(return_value=1),
        SetClipboardData=Mock(return_value=1),
        CloseClipboard=Mock(return_value=1),
    )
    kernel32 = SimpleNamespace(
        GlobalAlloc=Mock(return_value=0),
        GlobalLock=Mock(return_value=1),
        GlobalUnlock=Mock(return_value=1),
        GlobalFree=Mock(return_value=1),
    )

    monkeypatch.setattr(
        ctypes,
        "windll",
        SimpleNamespace(user32=user32, kernel32=kernel32),
        raising=False,
    )

    assert WindowsClipboard().copy("hello") is False
    user32.OpenClipboard.assert_not_called()
    user32.EmptyClipboard.assert_not_called()
    kernel32.GlobalFree.assert_not_called()


def test_windows_clipboard_copy_does_not_clear_clipboard_when_lock_fails(monkeypatch):
    from whisper_platform.clipboard import WindowsClipboard

    user32 = SimpleNamespace(
        OpenClipboard=Mock(return_value=1),
        EmptyClipboard=Mock(return_value=1),
        SetClipboardData=Mock(return_value=1),
        CloseClipboard=Mock(return_value=1),
    )
    kernel32 = SimpleNamespace(
        GlobalAlloc=Mock(return_value=1),
        GlobalLock=Mock(return_value=0),
        GlobalUnlock=Mock(return_value=1),
        GlobalFree=Mock(return_value=1),
    )

    monkeypatch.setattr(
        ctypes,
        "windll",
        SimpleNamespace(user32=user32, kernel32=kernel32),
        raising=False,
    )

    assert WindowsClipboard().copy("hello") is False
    user32.OpenClipboard.assert_not_called()
    user32.EmptyClipboard.assert_not_called()
    kernel32.GlobalFree.assert_called_once_with(1)


def test_windows_clipboard_copy_frees_memory_when_empty_clipboard_fails(monkeypatch):
    from whisper_platform.clipboard import WindowsClipboard

    user32 = SimpleNamespace(
        OpenClipboard=Mock(return_value=1),
        EmptyClipboard=Mock(return_value=0),
        SetClipboardData=Mock(return_value=1),
        CloseClipboard=Mock(return_value=1),
    )
    kernel32 = SimpleNamespace(
        GlobalAlloc=Mock(return_value=1),
        GlobalLock=Mock(return_value=1),
        GlobalUnlock=Mock(return_value=1),
        GlobalFree=Mock(return_value=1),
    )

    monkeypatch.setattr(
        ctypes,
        "windll",
        SimpleNamespace(user32=user32, kernel32=kernel32),
        raising=False,
    )
    monkeypatch.setattr(ctypes, "memmove", lambda _dest, _src, _size: None)

    assert WindowsClipboard().copy("hello") is False
    user32.OpenClipboard.assert_called_once()
    user32.EmptyClipboard.assert_called_once()
    kernel32.GlobalFree.assert_called_once_with(1)
    user32.SetClipboardData.assert_not_called()


def test_windows_clipboard_copy_frees_memory_when_memmove_raises(monkeypatch):
    from whisper_platform.clipboard import WindowsClipboard

    user32 = SimpleNamespace(
        OpenClipboard=Mock(return_value=1),
        EmptyClipboard=Mock(return_value=1),
        SetClipboardData=Mock(return_value=1),
        CloseClipboard=Mock(return_value=1),
    )
    kernel32 = SimpleNamespace(
        GlobalAlloc=Mock(return_value=1),
        GlobalLock=Mock(return_value=1),
        GlobalUnlock=Mock(return_value=1),
        GlobalFree=Mock(return_value=1),
    )

    monkeypatch.setattr(
        ctypes,
        "windll",
        SimpleNamespace(user32=user32, kernel32=kernel32),
        raising=False,
    )

    def raise_memmove(_dest, _src, _size):
        raise RuntimeError("boom")

    monkeypatch.setattr(ctypes, "memmove", raise_memmove)

    assert WindowsClipboard().copy("hello") is False
    kernel32.GlobalUnlock.assert_called_once_with(1)
    kernel32.GlobalFree.assert_called_once_with(1)
    user32.OpenClipboard.assert_not_called()


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
