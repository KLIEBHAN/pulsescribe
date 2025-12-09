"""Hotkey-Listener Implementierungen.

Plattformspezifische globale Hotkey-Registrierung.
macOS: QuickMacHotKey (Carbon API, keine Accessibility)
Windows: pynput
"""

import logging
import sys
from typing import Callable

logger = logging.getLogger("whisper_go.platform.hotkey")

# Hotkey-Callback Typ
HotkeyCallback = Callable[[], None]


# =============================================================================
# Hotkey-Parsing (gemeinsam für alle Plattformen)
# =============================================================================

# Virtual Key Codes (Carbon/macOS)
VIRTUAL_KEY_CODES = {
    # Funktionstasten
    "f1": 122, "f2": 120, "f3": 99, "f4": 118, "f5": 96, "f6": 97,
    "f7": 98, "f8": 100, "f9": 101, "f10": 109, "f11": 103, "f12": 111,
    "f13": 105, "f14": 107, "f15": 113, "f16": 106, "f17": 64, "f18": 79,
    "f19": 80, "f20": 90,
    # Buchstaben
    "a": 0, "b": 11, "c": 8, "d": 2, "e": 14, "f": 3, "g": 5, "h": 4,
    "i": 34, "j": 38, "k": 40, "l": 37, "m": 46, "n": 45, "o": 31,
    "p": 35, "q": 12, "r": 15, "s": 1, "t": 17, "u": 32, "v": 9,
    "w": 13, "x": 7, "y": 16, "z": 6,
    # Zahlen
    "0": 29, "1": 18, "2": 19, "3": 20, "4": 21, "5": 23, "6": 22,
    "7": 26, "8": 28, "9": 25,
    # Sondertasten
    "space": 49, "tab": 48, "return": 36, "enter": 36, "escape": 53,
    "esc": 53, "delete": 51, "backspace": 51,
}

# Modifier Masks (Carbon)
MODIFIER_MASKS = {
    "cmd": 1 << 8,      # cmdKey
    "command": 1 << 8,
    "shift": 1 << 9,    # shiftKey
    "option": 1 << 11,  # optionKey
    "alt": 1 << 11,
    "control": 1 << 12, # controlKey
    "ctrl": 1 << 12,
}


def parse_hotkey(hotkey_str: str) -> tuple[int, int]:
    """Parst Hotkey-String in (virtualKey, modifierMask).

    Args:
        hotkey_str: Hotkey als String (z.B. "f19", "cmd+shift+r")

    Returns:
        Tuple (virtualKey, modifierMask) für QuickMacHotKey

    Raises:
        ValueError: Bei ungültigem Hotkey
    """
    parts = [p.strip().lower() for p in hotkey_str.split("+")]

    if not parts:
        raise ValueError(f"Leerer Hotkey: {hotkey_str}")

    # Letzer Teil ist die Haupttaste
    key = parts[-1]
    modifiers = parts[:-1]

    # Virtual Key Code ermitteln
    if key not in VIRTUAL_KEY_CODES:
        raise ValueError(f"Unbekannte Taste: {key}")
    virtual_key = VIRTUAL_KEY_CODES[key]

    # Modifier Mask berechnen
    modifier_mask = 0
    for mod in modifiers:
        if mod not in MODIFIER_MASKS:
            raise ValueError(f"Unbekannter Modifier: {mod}")
        modifier_mask |= MODIFIER_MASKS[mod]

    return virtual_key, modifier_mask


class MacOSHotkeyListener:
    """macOS Hotkey-Listener via QuickMacHotKey.

    Nutzt die Carbon API (RegisterEventHotKey) für systemweite Hotkeys.
    Keine Accessibility-Berechtigung erforderlich!
    """

    def __init__(self, hotkey: str, callback: HotkeyCallback) -> None:
        self.hotkey = hotkey
        self.callback = callback
        self._hotkey_id = None
        self._registered = False

        # Parse Hotkey für QuickMacHotKey
        self.virtual_key, self.modifier_mask = parse_hotkey(hotkey)

    def register(self) -> None:
        """Registriert den Hotkey."""
        if self._registered:
            return

        try:
            from QuickMacHotKey import quickHotKey  # type: ignore[import-not-found]
            self._hotkey_id = quickHotKey(
                virtualKey=self.virtual_key,
                modifierMask=self.modifier_mask,
                handler=self.callback
            )
            self._registered = True
            logger.info(f"Hotkey '{self.hotkey}' registriert")
        except Exception as e:
            logger.error(f"Hotkey-Registrierung fehlgeschlagen: {e}")
            raise

    def unregister(self) -> None:
        """Deregistriert den Hotkey."""
        # QuickMacHotKey hat keine explizite Deregistrierung
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
                else:
                    # Normale Taste
                    self._hotkey_keys.add(keyboard.KeyCode.from_char(part))
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
                if self._current_keys == self._hotkey_keys:
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
