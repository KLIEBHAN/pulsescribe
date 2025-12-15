"""Platform-Abstraktion für PulseScribe.

Dieses Modul stellt plattformunabhängige Interfaces bereit und
lädt automatisch die richtige Implementierung für das aktuelle OS.

Usage:
    from platform import get_sound_player, get_clipboard, get_app_detector

    # Sound abspielen
    player = get_sound_player()
    player.play("ready")

    # Clipboard
    clipboard = get_clipboard()
    clipboard.copy("Hello World")

    # Aktive App ermitteln
    detector = get_app_detector()
    app_name = detector.get_frontmost_app()
"""

import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .base import (
        SoundPlayer,
        ClipboardHandler,
        AppDetector,
        DaemonController,
        HotkeyListener,
    )


def get_platform() -> str:
    """Ermittelt die aktuelle Plattform.

    Returns:
        'macos', 'windows' oder 'linux'

    Raises:
        RuntimeError: Bei nicht unterstützter Plattform
    """
    if sys.platform == "darwin":
        return "macos"
    elif sys.platform == "win32":
        return "windows"
    elif sys.platform.startswith("linux"):
        return "linux"
    raise RuntimeError(f"Nicht unterstützte Plattform: {sys.platform}")


def get_sound_player() -> "SoundPlayer":
    """Factory für plattformspezifischen Sound-Player.

    Nutzt Singleton-Pattern für Performance (Sound-IDs werden gecacht).

    Returns:
        SoundPlayer-Implementierung für die aktuelle Plattform
    """
    from .sound import get_sound_player as _get_cached_player

    return _get_cached_player()


def get_clipboard() -> "ClipboardHandler":
    """Factory für plattformspezifischen Clipboard-Handler.

    Returns:
        ClipboardHandler-Implementierung für die aktuelle Plattform
    """
    platform = get_platform()
    if platform == "macos":
        from .clipboard import MacOSClipboard

        return MacOSClipboard()
    elif platform == "windows":
        from .clipboard import WindowsClipboard

        return WindowsClipboard()
    raise NotImplementedError(f"Clipboard nicht implementiert für {platform}")


def get_app_detector() -> "AppDetector":
    """Factory für plattformspezifischen App-Detector.

    Returns:
        AppDetector-Implementierung für die aktuelle Plattform
    """
    platform = get_platform()
    if platform == "macos":
        from .app_detection import MacOSAppDetector

        return MacOSAppDetector()
    elif platform == "windows":
        from .app_detection import WindowsAppDetector

        return WindowsAppDetector()
    raise NotImplementedError(f"App-Detection nicht implementiert für {platform}")


def get_daemon_controller() -> "DaemonController":
    """Factory für plattformspezifischen Daemon-Controller.

    Returns:
        DaemonController-Implementierung für die aktuelle Plattform
    """
    platform = get_platform()
    if platform == "macos":
        from .daemon import MacOSDaemonController

        return MacOSDaemonController()
    elif platform == "windows":
        from .daemon import WindowsDaemonController

        return WindowsDaemonController()
    raise NotImplementedError(f"Daemon nicht implementiert für {platform}")


def get_hotkey_listener(hotkey: str, callback) -> "HotkeyListener":
    """Factory für plattformspezifischen Hotkey-Listener.

    Args:
        hotkey: Hotkey-String (z.B. "f19", "cmd+shift+r")
        callback: Callback-Funktion bei Hotkey-Aktivierung

    Returns:
        HotkeyListener-Implementierung für die aktuelle Plattform
    """
    platform = get_platform()
    if platform == "macos":
        from .hotkey import MacOSHotkeyListener

        return MacOSHotkeyListener(hotkey, callback)
    elif platform == "windows":
        from .hotkey import WindowsHotkeyListener

        return WindowsHotkeyListener(hotkey, callback)
    raise NotImplementedError(f"Hotkeys nicht implementiert für {platform}")


__all__ = [
    "get_platform",
    "get_sound_player",
    "get_clipboard",
    "get_app_detector",
    "get_daemon_controller",
    "get_hotkey_listener",
]
