"""Hotkey-Listener Implementierungen.

Plattformspezifische globale Hotkey-Registrierung.
macOS: Carbon (RegisterEventHotKey via quickmachotkey bindings)
Windows: pynput
"""

import logging
import sys
from typing import Callable, Any

# Reuse the canonical macOS hotkey parsing + maps from utils to avoid duplication
from utils.hotkey import (  # noqa: F401
    KEY_CODE_MAP as VIRTUAL_KEY_CODES,
    MODIFIER_MAP as MODIFIER_MASKS,
    parse_hotkey,
)
from utils.hotkey_windows import parse_windows_hotkey_for_pynput

logger = logging.getLogger("pulsescribe.platform.hotkey")

# Hotkey-Callback Typ
HotkeyCallback = Callable[[], None]


# =============================================================================
# Hotkey-Parsing (gemeinsam für alle Plattformen)
# =============================================================================
#
# Die eigentliche Implementierung liegt in utils.hotkey.parse_hotkey.
# Hier nur Re-Exports/ Aliases, damit whisper_platform stabil bleibt.


class MacOSHotkeyListener:
    """macOS Hotkey-Listener via Carbon.

    Nutzt die Carbon API (RegisterEventHotKey) für systemweite Hotkeys.
    Keine Accessibility-Berechtigung erforderlich!
    """

    def __init__(self, hotkey: str, callback: HotkeyCallback) -> None:
        self.hotkey = hotkey
        self.callback = callback
        self._registration = None
        self._registered = False

        # Parse Hotkey für Carbon (RegisterEventHotKey)
        self.virtual_key, self.modifier_mask = parse_hotkey(hotkey)

    def register(self) -> None:
        """Registriert den Hotkey."""
        if self._registered:
            return

        from utils.carbon_hotkey import CarbonHotKeyRegistration

        reg = CarbonHotKeyRegistration(
            virtual_key=self.virtual_key,
            modifier_mask=self.modifier_mask,
            callback=self.callback,
        )
        ok, err = reg.register()
        if not ok:
            raise RuntimeError(err or "RegisterEventHotKey failed")
        self._registration = reg
        self._registered = True
        logger.info(f"Hotkey '{self.hotkey}' registriert")

    def unregister(self) -> None:
        """Deregistriert den Hotkey."""
        reg = self._registration
        self._registration = None
        if reg is not None:
            try:
                reg.unregister()
            except Exception:
                pass
        self._registered = False

    def run(self) -> None:
        """Startet den Event-Loop (blockiert).

        Startet NSApplication Event-Loop für QuickMacHotKey.
        """
        try:
            from AppKit import NSApplication  # type: ignore[import-not-found]
            app = NSApplication.sharedApplication()
            app.run()
        except KeyboardInterrupt:
            logger.info("Hotkey-Listener beendet")


class WindowsHotkeyListener:
    """Windows Hotkey-Listener via pynput.

    Nutzt pynput für systemweite Hotkeys.
    Benötigt keine Admin-Rechte für die meisten Hotkeys.
    """

    def __init__(self, hotkey: str, callback: HotkeyCallback) -> None:
        self.hotkey = hotkey
        self.callback = callback
        self._listener = None
        self._current_keys: set[Any] = set()
        self._hotkey_keys: set[Any] = set()
        self._hotkey_active = False

        self._parse_hotkey()

    def _parse_hotkey(self) -> None:
        """Parst Hotkey für pynput über die kanonische Windows-Hotkey-Logik."""
        try:
            from pynput import keyboard  # type: ignore[import-not-found]
        except ImportError:
            logger.error("pynput nicht installiert")
            self._hotkey_keys = set()
            return

        self._hotkey_keys = parse_windows_hotkey_for_pynput(self.hotkey, keyboard)
        if not self._hotkey_keys and (self.hotkey or "").strip():
            logger.warning("Windows-Hotkey konnte nicht registriert werden: %s", self.hotkey)

    def register(self) -> None:
        """Registriert den Hotkey-Listener."""
        pass  # Wird in run() gemacht

    def unregister(self) -> None:
        """Stoppt den Listener und setzt den Tastenzustand zurück."""
        if self._listener:
            self._listener.stop()
            self._listener = None
        self._current_keys.clear()
        self._hotkey_active = False

    def _is_hotkey_active(self) -> bool:
        return bool(self._hotkey_keys) and self._hotkey_keys.issubset(self._current_keys)

    def run(self) -> None:
        """Startet den Keyboard-Listener (blockiert)."""
        try:
            from pynput import keyboard  # type: ignore[import-not-found]

            def on_press(key):
                self._current_keys.add(key)
                is_active = self._is_hotkey_active()
                if is_active and not self._hotkey_active:
                    self._hotkey_active = True
                    self.callback()

            def on_release(key):
                self._current_keys.discard(key)
                if not self._is_hotkey_active():
                    self._hotkey_active = False

            with keyboard.Listener(on_press=on_press, on_release=on_release) as listener:
                self._listener = listener
                listener.join()
        except ImportError:
            logger.error("pynput nicht installiert")
        except KeyboardInterrupt:
            logger.info("Hotkey-Listener beendet")


# Convenience-Funktion
def get_hotkey_listener(hotkey: str, callback: HotkeyCallback):
    """Gibt den passenden Hotkey-Listener für die aktuelle Plattform zurück."""
    if sys.platform == "darwin":
        return MacOSHotkeyListener(hotkey, callback)
    elif sys.platform == "win32":
        return WindowsHotkeyListener(hotkey, callback)
    raise NotImplementedError(f"Hotkeys nicht implementiert für {sys.platform}")


__all__ = [
    "MacOSHotkeyListener",
    "WindowsHotkeyListener",
    "get_hotkey_listener",
    "parse_hotkey",
    "HotkeyCallback",
]
