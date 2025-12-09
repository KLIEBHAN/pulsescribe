"""App-Detection Implementierungen.

Ermittelt die aktuell aktive/fokussierte Anwendung.
macOS: NSWorkspace via PyObjC
Windows: win32gui + psutil
"""

import logging
import sys

logger = logging.getLogger("whisper_go.platform.app_detection")


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

    def get_frontmost_app(self) -> str | None:
        """Ermittelt aktive App via win32gui."""
        if self._win32gui is None or self._psutil is None:
            return None

        try:
            hwnd = self._win32gui.GetForegroundWindow()
            if not hwnd:
                return None

            _, pid = self._win32process.GetWindowThreadProcessId(hwnd)
            process = self._psutil.Process(pid)

            # Prozessname ohne .exe Endung
            name = process.name()
            if name.lower().endswith(".exe"):
                name = name[:-4]

            return name
        except Exception as e:
            logger.debug(f"App-Detection fehlgeschlagen: {e}")
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
