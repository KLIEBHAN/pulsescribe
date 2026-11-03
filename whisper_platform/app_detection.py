"""App-Detection Implementierungen.

Ermittelt die aktuell aktive/fokussierte Anwendung.
macOS: NSWorkspace via PyObjC
Windows: win32gui + psutil
"""

import functools
import logging
import sys

logger = logging.getLogger("pulsescribe.platform.app_detection")


class MacOSAppDetector:
    """macOS App-Detection via NSWorkspace.

    Warum NSWorkspace statt AppleScript? Performance: ~0.2ms vs ~207ms.
    """

    def __init__(self) -> None:
        self._ns_workspace = None
        try:
            from AppKit import NSWorkspace  # type: ignore[import-not-found]
            self._ns_workspace = NSWorkspace
        except ImportError:
            logger.debug("PyObjC/AppKit nicht verfügbar")

    def get_frontmost_app(self) -> str | None:
        """Ermittelt aktive App via NSWorkspace."""
        if self._ns_workspace is None:
            return None

        try:
            app = self._ns_workspace.sharedWorkspace().frontmostApplication()
            if app:
                return app.localizedName()
        except Exception as e:
            logger.debug(f"App-Detection fehlgeschlagen: {e}")

        return None


class WindowsAppDetector:
    """Windows App-Detection via win32gui + psutil.

    Nutzt GetForegroundWindow() für das aktive Fenster,
    dann GetWindowThreadProcessId() + psutil für den Prozessnamen.
    """

    def __init__(self) -> None:
        self._win32gui = None
        self._win32process = None
        self._psutil = None

        try:
            import win32gui  # type: ignore[import-not-found]
            import win32process  # type: ignore[import-not-found]
            import psutil

            self._win32gui = win32gui
            self._win32process = win32process
            self._psutil = psutil
        except ImportError:
            logger.debug("pywin32/psutil nicht verfügbar")

    def _foreground_process(self):
        """psutil.Process of the foreground window, or None."""
        if self._win32gui is None or self._psutil is None:
            return None
        try:
            hwnd = self._win32gui.GetForegroundWindow()
            if not hwnd:
                return None
            _, pid = self._win32process.GetWindowThreadProcessId(hwnd)
            return self._psutil.Process(pid)
        except Exception as e:
            logger.debug(f"App-Detection fehlgeschlagen: {e}")
            return None

    def get_frontmost_app(self) -> str | None:
        """Process name of the foreground app without ``.exe`` (``WINWORD``).

        Refine app contexts match on this name; for display use
        ``get_frontmost_app_label``.
        """
        process = self._foreground_process()
        if process is None:
            return None
        try:
            return _strip_exe(process.name())
        except Exception as e:
            logger.debug(f"App-Detection fehlgeschlagen: {e}")
            return None

    def get_frontmost_app_label(self) -> str | None:
        """Readable foreground app name for display (``Microsoft Word``).

        Uses the executable's version resource (cached per path) and falls
        back to the process name.
        """
        process = self._foreground_process()
        if process is None:
            return None
        try:
            name = _strip_exe(process.name())
        except Exception as e:
            logger.debug(f"App-Detection fehlgeschlagen: {e}")
            return None
        try:
            path = process.exe()
        except Exception:
            return name  # e.g. elevated process: path not readable
        return executable_display_name(path) or name


def _strip_exe(name: str) -> str:
    return name[:-4] if name.lower().endswith(".exe") else name


# Fallback string tables when the resource lists no translation (en-US).
_VERSION_FALLBACK_CODES = ("040904b0", "040904e4")


@functools.lru_cache(maxsize=64)
def executable_display_name(path: str) -> str | None:
    """FileDescription (else ProductName) from a Windows executable's version
    resource via ``version.dll``; None when missing or off Windows. Cached per path.
    """
    if sys.platform != "win32" or not path:
        return None
    try:
        import ctypes
        from ctypes import wintypes

        version = ctypes.WinDLL("version")
        version.GetFileVersionInfoSizeW.argtypes = (wintypes.LPCWSTR, wintypes.LPDWORD)
        version.GetFileVersionInfoSizeW.restype = wintypes.DWORD
        version.GetFileVersionInfoW.argtypes = (
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.LPVOID,
        )
        version.GetFileVersionInfoW.restype = wintypes.BOOL
        version.VerQueryValueW.argtypes = (
            wintypes.LPCVOID,
            wintypes.LPCWSTR,
            ctypes.POINTER(wintypes.LPVOID),
            ctypes.POINTER(wintypes.UINT),
        )
        version.VerQueryValueW.restype = wintypes.BOOL

        size = version.GetFileVersionInfoSizeW(path, None)
        if not size:
            return None
        data = ctypes.create_string_buffer(size)
        if not version.GetFileVersionInfoW(path, 0, size, data):
            return None

        pointer = wintypes.LPVOID()
        length = wintypes.UINT()

        def query(block: str) -> bool:
            return bool(
                version.VerQueryValueW(
                    data, block, ctypes.byref(pointer), ctypes.byref(length)
                )
            )

        codes: list[str] = []
        if query("\\VarFileInfo\\Translation") and pointer.value:
            words = ctypes.cast(pointer, ctypes.POINTER(wintypes.WORD))
            for pair in range(length.value // 4):
                codes.append(f"{words[2 * pair]:04x}{words[2 * pair + 1]:04x}")
        codes.extend(code for code in _VERSION_FALLBACK_CODES if code not in codes)

        for key in ("FileDescription", "ProductName"):
            for code in codes:
                block = f"\\StringFileInfo\\{code}\\{key}"
                if query(block) and pointer.value and length.value:
                    text = ctypes.wstring_at(pointer.value, length.value)
                    text = text.split("\x00", 1)[0].strip()
                    if text:
                        return text
    except Exception as e:
        logger.debug(f"Version resource unreadable for {path}: {e}")
    return None


# Convenience-Funktion
def get_app_detector():
    """Gibt den passenden App-Detector für die aktuelle Plattform zurück."""
    if sys.platform == "darwin":
        return MacOSAppDetector()
    elif sys.platform == "win32":
        return WindowsAppDetector()
    # Linux: keine Implementierung (optional: xdotool)
    raise NotImplementedError(f"App-Detection nicht implementiert für {sys.platform}")


__all__ = ["MacOSAppDetector", "WindowsAppDetector", "get_app_detector"]
