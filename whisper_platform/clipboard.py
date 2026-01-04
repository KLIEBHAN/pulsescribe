"""Clipboard-Implementierungen.

Plattformspezifische Clipboard-Operationen mit einheitlichem Interface.
macOS: pbcopy/pbpaste via subprocess
Windows: pyperclip oder win32clipboard
"""

import logging
import os
import subprocess
import sys

logger = logging.getLogger("pulsescribe.platform.clipboard")


def _get_utf8_env() -> dict:
    """Erstellt Environment mit UTF-8 Locale für pbcopy/pbpaste.

    Wichtig für PyInstaller Bundles, die keine Shell-Locale erben.
    Ohne dies werden Umlaute (ü → √º) falsch kodiert.
    """
    env = os.environ.copy()
    env["LANG"] = "en_US.UTF-8"
    env["LC_ALL"] = "en_US.UTF-8"
    return env


class MacOSClipboard:
    """macOS Clipboard via pbcopy/pbpaste."""

    def copy(self, text: str) -> bool:
        """Kopiert Text in die Zwischenablage via pbcopy."""
        try:
            process = subprocess.run(
                ["pbcopy"],
                input=text.encode("utf-8"),
                timeout=2,
                capture_output=True,
                env=_get_utf8_env(),
            )
            if process.returncode != 0:
                logger.error(f"pbcopy fehlgeschlagen: {process.stderr.decode()}")
                return False
            logger.debug(f"pbcopy: {len(text)} Zeichen kopiert")
            return True
        except subprocess.TimeoutExpired:
            logger.error("pbcopy Timeout")
            return False
        except Exception as e:
            logger.error(f"Clipboard-Fehler: {e}")
            return False

    def paste(self) -> str | None:
        """Liest Text aus der Zwischenablage via pbpaste."""
        try:
            process = subprocess.run(
                ["pbpaste"],
                capture_output=True,
                timeout=2,
                env=_get_utf8_env(),
            )
            if process.returncode != 0:
                return None
            return process.stdout.decode("utf-8")
        except Exception:
            return None


class WindowsClipboard:
    """Windows Clipboard via ctypes (native, kein Tkinter).

    Verwendet Windows API direkt, um Tcl-Thread-Fehler zu vermeiden.
    """

    def copy(self, text: str) -> bool:
        """Kopiert Text in die Zwischenablage via Windows API."""
        import ctypes

        CF_UNICODETEXT = 13
        GMEM_MOVEABLE = 0x0002

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32

        try:
            # Clipboard öffnen
            if not user32.OpenClipboard(None):
                logger.error("Konnte Clipboard nicht öffnen")
                return False

            try:
                user32.EmptyClipboard()

                # Text in globalem Speicher ablegen
                text_bytes = text.encode("utf-16-le") + b"\x00\x00"
                h_mem = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(text_bytes))
                if not h_mem:
                    logger.error("GlobalAlloc fehlgeschlagen")
                    return False

                p_mem = kernel32.GlobalLock(h_mem)
                if not p_mem:
                    logger.error("GlobalLock fehlgeschlagen")
                    kernel32.GlobalFree(h_mem)
                    return False

                try:
                    ctypes.memmove(p_mem, text_bytes, len(text_bytes))
                finally:
                    kernel32.GlobalUnlock(h_mem)

                # In Clipboard setzen (übernimmt Ownership von h_mem bei Erfolg)
                if not user32.SetClipboardData(CF_UNICODETEXT, h_mem):
                    logger.error("SetClipboardData fehlgeschlagen")
                    kernel32.GlobalFree(h_mem)
                    return False

                return True
            finally:
                user32.CloseClipboard()

        except Exception as e:
            logger.error(f"Clipboard-Fehler: {e}")
            return False

    def paste(self) -> str | None:
        """Liest Text aus der Zwischenablage via Windows API."""
        import ctypes

        CF_UNICODETEXT = 13

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32

        try:
            if not user32.OpenClipboard(None):
                return None

            try:
                h_data = user32.GetClipboardData(CF_UNICODETEXT)
                if not h_data:
                    return None

                p_data = kernel32.GlobalLock(h_data)
                if not p_data:
                    return None

                try:
                    text = ctypes.wstring_at(p_data)
                    return text
                finally:
                    kernel32.GlobalUnlock(h_data)
            finally:
                user32.CloseClipboard()

        except Exception:
            return None


# Convenience-Funktion
def get_clipboard():
    """Gibt den passenden Clipboard-Handler für die aktuelle Plattform zurück."""
    if sys.platform == "darwin":
        return MacOSClipboard()
    elif sys.platform == "win32":
        return WindowsClipboard()
    # Linux Fallback auf pyperclip
    return WindowsClipboard()


__all__ = ["MacOSClipboard", "WindowsClipboard", "get_clipboard"]
