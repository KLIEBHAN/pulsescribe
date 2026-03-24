"""
Hotkey-Parsing und Auto-Paste Utilities.

Extrahiert aus hotkey_daemon.py.
"""

import os
import subprocess
import time

from utils.env import parse_bool
from utils.logging import get_logger

logger = get_logger()

# =============================================================================
# Hotkey-Parsing (QuickMacHotKey)
# =============================================================================

# Key-Code Mapping: String → Carbon Virtual Key Code
# Basierend auf quickmachotkey.constants
KEY_CODE_MAP = {
    # Funktionstasten
    "f1": 122,
    "f2": 120,
    "f3": 99,
    "f4": 118,
    "f5": 96,
    "f6": 97,
    "f7": 98,
    "f8": 100,
    "f9": 101,
    "f10": 109,
    "f11": 103,
    "f12": 111,
    "f13": 105,
    "f14": 107,
    "f15": 113,
    "f16": 106,
    "f17": 64,
    "f18": 79,
    "f19": 80,
    "f20": 90,
    # Fn / Globe key (macOS)
    "fn": 63,  # kVK_Function
    # Buchstaben
    "a": 0,
    "b": 11,
    "c": 8,
    "d": 2,
    "e": 14,
    "f": 3,
    "g": 5,
    "h": 4,
    "i": 34,
    "j": 38,
    "k": 40,
    "l": 37,
    "m": 46,
    "n": 45,
    "o": 31,
    "p": 35,
    "q": 12,
    "r": 15,
    "s": 1,
    "t": 17,
    "u": 32,
    "v": 9,
    "w": 13,
    "x": 7,
    "y": 16,
    "z": 6,
    # Zahlen
    "0": 29,
    "1": 18,
    "2": 19,
    "3": 20,
    "4": 21,
    "5": 23,
    "6": 22,
    "7": 26,
    "8": 28,
    "9": 25,
    # Satzzeichen / Symbole (ANSI)
    ".": 47,  # kVK_ANSI_Period
    ",": 43,  # kVK_ANSI_Comma
    "/": 44,  # kVK_ANSI_Slash
    "\\": 42,  # kVK_ANSI_Backslash
    ";": 41,  # kVK_ANSI_Semicolon
    "'": 39,  # kVK_ANSI_Quote
    "`": 50,  # kVK_ANSI_Grave
    "-": 27,  # kVK_ANSI_Minus
    "=": 24,  # kVK_ANSI_Equal
    "[": 33,  # kVK_ANSI_LeftBracket
    "]": 30,  # kVK_ANSI_RightBracket
    # Sondertasten
    "space": 49,
    "return": 36,
    "enter": 36,
    "tab": 48,
    "escape": 53,
    "esc": 53,
    "delete": 51,
    "backspace": 51,
    "forwarddelete": 117,
    # Pfeiltasten
    "up": 126,
    "down": 125,
    "left": 123,
    "right": 124,
    # Navigation
    "home": 115,
    "end": 119,
    "pageup": 116,
    "pagedown": 121,
    # CapsLock
    "capslock": 57,  # kVK_CapsLock
    "caps_lock": 57,
}

# Modifier-Mapping: String → Carbon Modifier Mask
# cmdKey=256, shiftKey=512, optionKey=2048, controlKey=4096
MODIFIER_MAP = {
    "cmd": 256,
    "command": 256,
    "shift": 512,
    "alt": 2048,
    "option": 2048,
    "ctrl": 4096,
    "control": 4096,
}


def parse_hotkey(hotkey_str: str) -> tuple[int, int]:
    """
    Parst Hotkey-String in (virtualKey, modifierMask).

    Args:
        hotkey_str: Hotkey als String (z.B. "f19", "cmd+shift+r")

    Returns:
        Tuple (virtualKey, modifierMask) für QuickMacHotKey

    Raises:
        ValueError: Bei ungültigem Hotkey
    """
    hotkey_str = hotkey_str.strip().lower()
    parts = [p.strip() for p in hotkey_str.split("+")]

    if len(parts) == 1:
        # Einzelne Taste ohne Modifier
        key = parts[0]
        if key not in KEY_CODE_MAP:
            raise ValueError(f"Unbekannte Taste: {key}")
        return KEY_CODE_MAP[key], 0

    # Mit Modifier(n)
    *modifiers, key = parts

    if key not in KEY_CODE_MAP:
        raise ValueError(f"Unbekannte Taste: {key}")

    # Modifier kombinieren
    modifier_mask = 0
    for mod in modifiers:
        if mod not in MODIFIER_MAP:
            raise ValueError(f"Unbekannter Modifier: {mod}")
        modifier_mask |= MODIFIER_MAP[mod]

    return KEY_CODE_MAP[key], modifier_mask


def hotkeys_conflict(hotkey_a: str | None, hotkey_b: str | None) -> bool:
    """Return True when two macOS hotkeys can trigger ambiguously.

    A conflict exists when both hotkeys use the same physical key and one
    modifier set is a subset of the other, e.g. ``space`` and ``cmd+space``.
    """

    def _signature(hotkey: str | None) -> tuple[int, int] | None:
        normalized = (hotkey or "").strip().lower()
        if not normalized:
            return None
        try:
            return parse_hotkey(normalized)
        except ValueError:
            return None

    sig_a = _signature(hotkey_a)
    sig_b = _signature(hotkey_b)
    if sig_a is None or sig_b is None:
        return False

    key_a, modifiers_a = sig_a
    key_b, modifiers_b = sig_b
    if key_a != key_b:
        return False

    return (
        modifiers_a == modifiers_b
        or (modifiers_a & modifiers_b) == modifiers_a
        or (modifiers_a & modifiers_b) == modifiers_b
    )


# =============================================================================
# Auto-Paste
# =============================================================================


def _paste_via_pynput() -> bool:
    """Paste via pynput (Cross-Platform, braucht Accessibility)."""
    try:
        from pynput.keyboard import Controller, Key

        keyboard = Controller()

        # Cmd+V senden
        keyboard.press(Key.cmd)
        keyboard.press("v")
        keyboard.release("v")
        keyboard.release(Key.cmd)

        logger.info("Auto-Paste: Cmd+V gesendet via pynput")
        return True

    except ImportError:
        logger.warning("pynput nicht installiert")
        return False
    except Exception as e:
        logger.warning(f"pynput fehlgeschlagen: {e}")
        return False


def _paste_via_quartz() -> bool:
    """Paste via CGEventPost (Quartz) - funktioniert nur mit TCC-Berechtigung."""
    try:
        from Quartz import (
            CGEventCreateKeyboardEvent,
            CGEventPost,
            CGEventSetFlags,
            kCGEventFlagMaskCommand,
            kCGHIDEventTap,
        )

        # Virtual Key Code für 'V' ist 9
        kVK_ANSI_V = 9

        # Key Down Event mit Command-Modifier
        event_down = CGEventCreateKeyboardEvent(None, kVK_ANSI_V, True)
        CGEventSetFlags(event_down, kCGEventFlagMaskCommand)

        # Key Up Event mit Command-Modifier
        event_up = CGEventCreateKeyboardEvent(None, kVK_ANSI_V, False)
        CGEventSetFlags(event_up, kCGEventFlagMaskCommand)

        # Events posten
        CGEventPost(kCGHIDEventTap, event_down)
        CGEventPost(kCGHIDEventTap, event_up)

        logger.info("Auto-Paste: Cmd+V gesendet via CGEventPost")
        return True

    except ImportError as e:
        logger.warning(f"Quartz nicht verfügbar: {e}")
        return False
    except Exception as e:
        logger.warning(f"CGEventPost fehlgeschlagen: {e}")
        return False


def _paste_via_osascript() -> bool:
    """Fallback: Paste via osascript (braucht Accessibility!)."""
    logger.info("Versuche osascript (braucht Accessibility-Berechtigung)")
    try:
        result = subprocess.run(
            [
                "osascript",
                "-e",
                'tell application "System Events" to keystroke "v" using command down',
            ],
            capture_output=True,
            text=True,
            timeout=2,
        )
    except subprocess.TimeoutExpired:
        logger.warning("osascript Timeout")
        return False
    except OSError as e:
        logger.warning(f"osascript konnte nicht gestartet werden: {e}")
        return False
    except Exception as e:
        logger.warning(f"osascript fehlgeschlagen: {e}")
        return False

    if result.returncode != 0:
        logger.warning(f"osascript fehlgeschlagen: {result.stderr}")
        return False
    logger.info("Auto-Paste: Cmd+V gesendet via osascript")
    return True


def _get_utf8_env() -> dict:
    """Erstellt Environment mit UTF-8 Locale für pbcopy/pbpaste."""
    env = os.environ.copy()
    env["LANG"] = "en_US.UTF-8"
    env["LC_ALL"] = "en_US.UTF-8"
    return env


def _get_clipboard_text() -> str | None:
    """Liest den aktuellen Text-Inhalt des Clipboards.

    Returns:
        Text-Inhalt oder None wenn kein Text im Clipboard.
    """
    try:
        from AppKit import NSPasteboard, NSStringPboardType  # type: ignore[import-not-found]

        pb = NSPasteboard.generalPasteboard()
        return pb.stringForType_(NSStringPboardType)
    except Exception:
        return None


def _copy_to_clipboard_native(text: str) -> bool:
    """Kopiert Text direkt via NSPasteboard (in-process, kein Subprocess).

    Dies ist wichtig für das Einfügen in eigene App-Fenster, da pbcopy (Subprocess)
    den Clipboard-Update möglicherweise nicht sofort für NSTextView sichtbar macht.
    """
    try:
        from AppKit import NSPasteboard, NSStringPboardType  # type: ignore[import-not-found]

        pb = NSPasteboard.generalPasteboard()
        pb.clearContents()
        pb.setString_forType_(text, NSStringPboardType)
        logger.debug(f"NSPasteboard: {len(text)} Zeichen kopiert")
        return True
    except Exception as e:
        logger.warning(f"NSPasteboard-Kopieren fehlgeschlagen: {e}")
        return False


def _paste_via_pynput_windows() -> bool:
    """Paste via pynput auf Windows (Ctrl+V statt Cmd+V)."""
    try:
        from pynput.keyboard import Controller, Key

        keyboard = Controller()
        keyboard.press(Key.ctrl)
        keyboard.press("v")
        keyboard.release("v")
        keyboard.release(Key.ctrl)

        logger.info("Auto-Paste: Ctrl+V gesendet via pynput")
        return True

    except ImportError:
        logger.warning("pynput nicht installiert")
        return False
    except Exception as e:
        logger.warning(f"pynput fehlgeschlagen: {e}")
        return False


def _get_windows_clipboard_handler():
    """Return the retry-capable Windows clipboard handler when available."""
    try:
        from whisper_platform import get_clipboard

        return get_clipboard()
    except Exception as e:
        logger.debug(f"Native Windows-Clipboard nicht verfügbar: {e}")
        return None


def _copy_windows_clipboard_text(text: str, *, clipboard=None) -> bool:
    """Copy text on Windows, preferring the native retry-capable clipboard."""
    if clipboard is not None:
        try:
            if clipboard.copy(text):
                logger.debug(f"Native Windows-Clipboard: {len(text)} Zeichen kopiert")
                return True
            logger.debug("Native Windows-Clipboard Copy fehlgeschlagen, nutze Fallback")
        except Exception as e:
            logger.debug(f"Native Windows-Clipboard Copy fehlgeschlagen: {e}")

    try:
        import pyperclip

        pyperclip.copy(text)
        logger.debug(f"pyperclip Fallback: {len(text)} Zeichen kopiert")
        return True
    except ImportError:
        logger.error("pyperclip nicht installiert")
        return False
    except Exception as e:
        logger.error(f"Clipboard-Fehler: {e}")
        return False


def _get_windows_clipboard_text(*, clipboard=None) -> str | None:
    """Read current Windows clipboard text with native clipboard first."""
    if clipboard is not None:
        try:
            text = clipboard.paste()
            if text is not None:
                return text
        except Exception as e:
            logger.debug(f"Native Windows-Clipboard Paste fehlgeschlagen: {e}")

    try:
        import pyperclip

        return pyperclip.paste()
    except ImportError:
        logger.debug("pyperclip nicht installiert")
        return None
    except Exception as e:
        logger.debug(f"Clipboard lesen fehlgeschlagen: {e}")
        return None


def paste_transcript(text: str) -> bool:
    """
    Kopiert Text in Clipboard und fügt via Cmd+V (macOS) bzw. Ctrl+V (Windows) ein.

    Strategie:
    - Windows: pyperclip + pynput (Ctrl+V)
    - macOS (in Prioritätsreihenfolge):
      1. pynput - Cross-Platform, braucht Accessibility-Berechtigung
      2. CGEventPost (Quartz) - Funktioniert wenn Python TCC-Rechte hat
      3. osascript - Fallback (braucht Accessibility)

    Args:
        text: Text zum Einfügen

    Returns:
        True wenn erfolgreich, False bei Fehler
    """
    import sys

    logger.info(f"Auto-Paste: '{text[:50]}{'...' if len(text) > 50 else ''}'")

    # Windows: pyperclip + pynput direkt
    if sys.platform == "win32":
        clipboard = _get_windows_clipboard_handler()

        # Optional: Vorherigen Clipboard-Text merken für Restore nach dem Paste
        # (ENV: PULSESCRIBE_CLIPBOARD_RESTORE=true)
        restore_clipboard = (
            parse_bool(os.getenv("PULSESCRIBE_CLIPBOARD_RESTORE")) is True
        )
        previous_text = None
        if restore_clipboard:
            try:
                previous_text = _get_windows_clipboard_text(clipboard=clipboard)
            except Exception:
                previous_text = None

        if not _copy_windows_clipboard_text(text, clipboard=clipboard):
            return False

        # Kurze Pause damit Zielanwendung das Clipboard-Update sieht
        time.sleep(0.05)

        if not _paste_via_pynput_windows():
            logger.error(
                "Auto-Paste fehlgeschlagen. "
                "Text wurde in Zwischenablage kopiert - manuell mit Ctrl+V einfügen."
            )
            return False

        # Optional: Vorherigen Text erneut ins Clipboard kopieren
        # Safety check: Nur restore wenn Clipboard noch unseren Text enthält
        # (verhindert Überschreiben wenn User/App zwischenzeitlich kopiert hat)
        if previous_text is not None:
            try:
                time.sleep(0.5)  # Kurz warten bis Paste verarbeitet wurde
                current = _get_windows_clipboard_text(clipboard=clipboard)
                if current == text:
                    if _copy_windows_clipboard_text(
                        previous_text, clipboard=clipboard
                    ):
                        logger.debug("Vorheriger Clipboard-Text wiederhergestellt")
                    else:
                        logger.warning("Clipboard-Restore fehlgeschlagen")
                else:
                    logger.debug(
                        "Clipboard-Restore übersprungen: Inhalt wurde verändert"
                    )
            except Exception as e:
                logger.warning(f"Clipboard-Restore fehlgeschlagen: {e}")

        return True

    # macOS: Bestehende komplexe Logik mit Fallbacks
    # Optional: Vorherigen Clipboard-Text merken für Re-Copy nach dem Paste
    # (ENV: PULSESCRIBE_CLIPBOARD_RESTORE=true)
    # Dies fügt den alten Text ERNEUT ins Clipboard ein, sodass Clipboard-History
    # Tools beide Einträge sehen (Transkription + vorheriger Text).
    restore_clipboard = (
        parse_bool(os.getenv("PULSESCRIBE_CLIPBOARD_RESTORE")) is True
    )
    previous_text = _get_clipboard_text() if restore_clipboard else None

    # 1. In Clipboard kopieren via NSPasteboard (in-process, kein Subprocess)
    # Dies ist wichtig für das Einfügen in eigene App-Fenster (z.B. Settings)
    if not _copy_to_clipboard_native(text):
        # Fallback zu pbcopy wenn NSPasteboard fehlschlägt
        utf8_env = _get_utf8_env()
        try:
            process = subprocess.run(
                ["pbcopy"],
                input=text.encode("utf-8"),
                capture_output=True,
                timeout=5,
                env=utf8_env,
            )
            if process.returncode != 0:
                logger.error(f"pbcopy fehlgeschlagen: {process.stderr.decode()}")
                return False
            logger.debug(f"pbcopy Fallback: {len(text)} Zeichen kopiert")
            # Kurze Pause für Clipboard-Sync bei Subprocess-Fallback
            time.sleep(0.1)
        except subprocess.TimeoutExpired:
            logger.error("pbcopy Timeout")
            return False
        except Exception as e:
            logger.error(f"Clipboard-Fehler: {e}")
            return False

    # 2. Cmd+V senden (verschiedene Methoden in Prioritätsreihenfolge)
    pasted_ok = False

    # 2a. pynput (Cross-Platform, bevorzugt)
    if _paste_via_pynput():
        pasted_ok = True

    # 2b. CGEventPost (Quartz)
    elif _paste_via_quartz():
        pasted_ok = True

    # 2c. osascript (letzter Fallback)
    elif _paste_via_osascript():
        pasted_ok = True

    # 3. Ergebnis verarbeiten
    if not pasted_ok:
        logger.error(
            "Auto-Paste fehlgeschlagen (alle 3 Methoden). "
            "Mögliche Ursachen:\n"
            "  1. PulseScribe.app fehlt in: Systemeinstellungen → Datenschutz → Bedienungshilfen\n"
            "  2. Nach App-Neubuild: App entfernen und neu hinzufügen (Signatur geändert)\n"
            "  3. Text wurde in Zwischenablage kopiert - manuell mit CMD+V einfügen"
        )
        return False

    # 4. Optional: Vorherigen Text erneut ins Clipboard kopieren
    # Dies ist besser als ein kompletter Restore, weil Clipboard-History Tools
    # beide Einträge sehen (Transkription + vorheriger Text).
    if previous_text is not None:
        time.sleep(1.0)  # Warten bis Paste verarbeitet wurde
        current_clipboard = _get_clipboard_text()
        if current_clipboard == text:
            _copy_to_clipboard_native(previous_text)
            logger.debug("Vorheriger Clipboard-Text erneut kopiert")
        else:
            logger.debug(
                "Clipboard-Restore übersprungen: Inhalt wurde zwischenzeitlich verändert"
            )

    return True
