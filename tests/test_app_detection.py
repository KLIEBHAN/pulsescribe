import sys
from types import SimpleNamespace

import pytest

from whisper_platform import app_detection
from whisper_platform.app_detection import WindowsAppDetector, executable_display_name


class _Process:
    def __init__(self, name: str, exe=None) -> None:
        self._name = name
        self._exe = exe

    def name(self) -> str:
        return self._name

    def exe(self) -> str:
        if isinstance(self._exe, Exception):
            raise self._exe
        return self._exe


def _detector(process: _Process) -> WindowsAppDetector:
    detector = WindowsAppDetector.__new__(WindowsAppDetector)
    detector._win32gui = SimpleNamespace(GetForegroundWindow=lambda: 1)
    detector._win32process = SimpleNamespace(GetWindowThreadProcessId=lambda _hwnd: (0, 42))
    detector._psutil = SimpleNamespace(Process=lambda _pid: process)
    return detector


def test_label_uses_the_version_resource_and_keeps_the_process_name_for_refine(
    monkeypatch,
):
    names = {r"C:\Office\WINWORD.EXE": "Microsoft Word"}
    monkeypatch.setattr(app_detection, "executable_display_name", names.get)
    detector = _detector(_Process("WINWORD.EXE", r"C:\Office\WINWORD.EXE"))

    assert detector.get_frontmost_app_label() == "Microsoft Word"
    assert detector.get_frontmost_app() == "WINWORD"


@pytest.mark.parametrize("exe", [r"C:\tools\tool.exe", PermissionError("denied")])
def test_label_falls_back_to_the_process_name(monkeypatch, exe):
    monkeypatch.setattr(app_detection, "executable_display_name", lambda _path: None)

    assert _detector(_Process("tool.exe", exe)).get_frontmost_app_label() == "tool"


@pytest.mark.skipif(sys.platform == "win32", reason="non-Windows guard")
def test_display_name_is_none_off_windows():
    assert executable_display_name(sys.executable) is None


@pytest.mark.skipif(sys.platform != "win32", reason="reads version.dll")
def test_display_name_reads_the_windows_version_resource():
    executable_display_name.cache_clear()
    assert executable_display_name(sys.executable)  # python.exe: "Python"
    assert executable_display_name(r"C:\does\not\exist.exe") is None
