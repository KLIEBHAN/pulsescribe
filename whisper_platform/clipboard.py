"""Clipboard-Implementierungen.

Plattformspezifische Clipboard-Operationen mit einheitlichem Interface.
macOS: pbcopy/pbpaste via subprocess
Windows: pyperclip oder win32clipboard
"""

import logging
import os
import subprocess
import sys

logger = logging.getLogger("whisper_go.platform.clipboard")


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
    """Windows Clipboard via pyperclip (cross-platform Fallback).

    Alternativ: win32clipboard für native Unterstützung.
    """

    def __init__(self) -> None:
        self._pyperclip = None
        try:
            import pyperclip

            self._pyperclip = pyperclip
        except ImportError:
            logger.warning("pyperclip nicht installiert, Clipboard nicht verfügbar")

    def copy(self, text: str) -> bool:
        """Kopiert Text in die Zwischenablage."""
        if self._pyperclip is None:
            return False
        try:
            self._pyperclip.copy(text)
            return True
        except Exception as e:
            logger.error(f"Clipboard-Fehler: {e}")
            return False

    def paste(self) -> str | None:
        """Liest Text aus der Zwischenablage."""
        if self._pyperclip is None:
            return None
        try:
            return self._pyperclip.paste()
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
