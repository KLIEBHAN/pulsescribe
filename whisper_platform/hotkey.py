"""Hotkey-Listener Implementierungen.

Plattformspezifische globale Hotkey-Registrierung.
macOS: Carbon (RegisterEventHotKey via quickmachotkey bindings)
Windows: pynput
"""

import logging
import sys
from typing import Callable

# Reuse the canonical macOS hotkey parsing + maps from utils to avoid duplication
from utils.hotkey import (  # noqa: F401
    KEY_CODE_MAP as VIRTUAL_KEY_CODES,
    MODIFIER_MAP as MODIFIER_MASKS,
    parse_hotkey,
)

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
        self._current_keys: set = set()
        self._hotkey_keys: set = set()

        self._parse_hotkey()

    def _parse_hotkey(self) -> None:
        """Parst Hotkey für pynput."""
        try:
            from pynput import keyboard  # type: ignore[import-not-found]

            parts = [p.strip().lower() for p in self.hotkey.split("+")]

            for part in parts:
                if part in ("ctrl", "control"):
                    self._hotkey_keys.add(keyboard.Key.ctrl)
                elif part in ("alt", "option"):
                    self._hotkey_keys.add(keyboard.Key.alt)
                elif part in ("shift",):
                    self._hotkey_keys.add(keyboard.Key.shift)
                elif part in ("cmd", "command", "win"):
                    self._hotkey_keys.add(keyboard.Key.cmd)
                elif part.startswith("f") and part[1:].isdigit():
                    # Funktionstasten
                    f_key = getattr(keyboard.Key, part, None)
                    if f_key:
                        self._hotkey_keys.add(f_key)
                elif part:  # Leere Parts überspringen (z.B. bei "f1++a")
                    # Normale Taste
                    try:
                        self._hotkey_keys.add(keyboard.KeyCode.from_char(part))
                    except (ValueError, TypeError) as e:
                        logger.warning(f"Ungültige Hotkey-Taste '{part}': {e}")
        except ImportError:
            logger.error("pynput nicht installiert")

    def register(self) -> None:
        """Registriert den Hotkey-Listener."""
        pass  # Wird in run() gemacht

    def unregister(self) -> None:
        """Stoppt den Listener."""
        if self._listener:
            self._listener.stop()

    def run(self) -> None:
        """Startet den Keyboard-Listener (blockiert)."""
        try:
            from pynput import keyboard  # type: ignore[import-not-found]

            def on_press(key):
                self._current_keys.add(key)
                if self._hotkey_keys.issubset(self._current_keys):
                    self.callback()

            def on_release(key):
                self._current_keys.discard(key)

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
