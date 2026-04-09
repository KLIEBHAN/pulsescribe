"""Clipboard-Implementierungen.

Plattformspezifische Clipboard-Operationen mit einheitlichem Interface.
macOS: pbcopy/pbpaste via subprocess
Windows: pyperclip oder win32clipboard
"""

import logging
import os
import subprocess
import sys
import time

logger = logging.getLogger("pulsescribe.platform.clipboard")

_CLIPBOARD_OPEN_RETRIES = 5
_CLIPBOARD_OPEN_RETRY_DELAY_SEC = 0.05


def _set_ctypes_signature(func, *, restype=None, argtypes=None) -> None:
    """Best-effort signature setup for ctypes functions.

    Real Win32 DLL callables allow setting ``restype``/``argtypes``. Test doubles
    may not, so we keep this helper forgiving.
    """
    try:
        if restype is not None:
            func.restype = restype
        if argtypes is not None:
            func.argtypes = argtypes
    except Exception:
        pass


def _set_optional_ctypes_signature(namespace, name: str, *, restype=None, argtypes=None) -> None:
    func = getattr(namespace, name, None)
    if func is None:
        return
    _set_ctypes_signature(func, restype=restype, argtypes=argtypes)


def _configure_windows_clipboard_api(ctypes_module, user32, kernel32) -> None:
    """Configure pointer-sized ctypes signatures for clipboard WinAPI calls."""
    from ctypes import wintypes

    _set_optional_ctypes_signature(
        user32,
        "OpenClipboard",
        restype=wintypes.BOOL,
        argtypes=[wintypes.HWND],
    )
    _set_optional_ctypes_signature(
        user32,
        "CloseClipboard",
        restype=wintypes.BOOL,
        argtypes=[],
    )
    _set_optional_ctypes_signature(
        user32,
        "EmptyClipboard",
        restype=wintypes.BOOL,
        argtypes=[],
    )
    _set_optional_ctypes_signature(
        user32,
        "SetClipboardData",
        restype=wintypes.HANDLE,
        argtypes=[wintypes.UINT, wintypes.HANDLE],
    )
    _set_optional_ctypes_signature(
        user32,
        "GetClipboardData",
        restype=wintypes.HANDLE,
        argtypes=[wintypes.UINT],
    )
    _set_optional_ctypes_signature(
        kernel32,
        "GlobalAlloc",
        restype=wintypes.HGLOBAL,
        argtypes=[wintypes.UINT, ctypes_module.c_size_t],
    )
    _set_optional_ctypes_signature(
        kernel32,
        "GlobalLock",
        restype=ctypes_module.c_void_p,
        argtypes=[wintypes.HGLOBAL],
    )
    _set_optional_ctypes_signature(
        kernel32,
        "GlobalUnlock",
        restype=wintypes.BOOL,
        argtypes=[wintypes.HGLOBAL],
    )
    _set_optional_ctypes_signature(
        kernel32,
        "GlobalFree",
        restype=wintypes.HGLOBAL,
        argtypes=[wintypes.HGLOBAL],
    )


def _get_utf8_env() -> dict:
    """Erstellt Environment mit UTF-8 Locale für pbcopy/pbpaste.

    Wichtig für PyInstaller Bundles, die keine Shell-Locale erben.
    Ohne dies werden Umlaute (ü → √º) falsch kodiert.
    """
    env = os.environ.copy()
    env["LANG"] = "en_US.UTF-8"
    env["LC_ALL"] = "en_US.UTF-8"
    return env


def _run_macos_clipboard_command(
    command: list[str],
    *,
    input_bytes: bytes | None = None,
    timeout: float = 2,
) -> subprocess.CompletedProcess[bytes]:
    """Run ``pbcopy``/``pbpaste`` with the project-standard UTF-8 locale."""
    return subprocess.run(
        command,
        input=input_bytes,
        timeout=timeout,
        capture_output=True,
        env=_get_utf8_env(),
    )


def _copy_text_via_pbcopy(
    text: str,
    *,
    timeout: float = 2,
    sync_delay_sec: float = 0.0,
    success_log_prefix: str = "pbcopy",
) -> bool:
    """Copy UTF-8 text via ``pbcopy`` with consistent logging and optional sync delay."""
    try:
        process = _run_macos_clipboard_command(
            ["pbcopy"],
            input_bytes=text.encode("utf-8"),
            timeout=timeout,
        )
        if process.returncode != 0:
            logger.error(
                f"pbcopy fehlgeschlagen: {process.stderr.decode(errors='replace')}"
            )
            return False
        logger.debug(f"{success_log_prefix}: {len(text)} Zeichen kopiert")
        if sync_delay_sec > 0:
            time.sleep(sync_delay_sec)
        return True
    except subprocess.TimeoutExpired:
        logger.error("pbcopy Timeout")
        return False
    except Exception as e:
        logger.error(f"Clipboard-Fehler: {e}")
        return False


def _paste_text_via_pbpaste(*, timeout: float = 2) -> str | None:
    """Read UTF-8 text via ``pbpaste`` using the shared locale configuration."""
    try:
        process = _run_macos_clipboard_command(["pbpaste"], timeout=timeout)
        if process.returncode != 0:
            return None
        return process.stdout.decode("utf-8")
    except Exception:
        return None


def _open_clipboard_with_retry(user32) -> bool:
    """Oeffnet das Windows-Clipboard mit kurzem Retry bei Lock-Contention."""
    for attempt in range(_CLIPBOARD_OPEN_RETRIES):
        if user32.OpenClipboard(None):
            return True
        if attempt < _CLIPBOARD_OPEN_RETRIES - 1:
            time.sleep(_CLIPBOARD_OPEN_RETRY_DELAY_SEC)
    return False


class MacOSClipboard:
    """macOS Clipboard via pbcopy/pbpaste."""

    def copy(self, text: str) -> bool:
        """Kopiert Text in die Zwischenablage via pbcopy."""
        return _copy_text_via_pbcopy(text)

    def paste(self) -> str | None:
        """Liest Text aus der Zwischenablage via pbpaste."""
        return _paste_text_via_pbpaste()


class WindowsClipboard:
    """Windows Clipboard via ctypes (native, kein Tkinter).

    Verwendet Windows API direkt, um Tcl-Thread-Fehler zu vermeiden.
    """

    def copy(self, text: str) -> bool:
        """Kopiert Text in die Zwischenablage via Windows API."""
        import ctypes

        CF_UNICODETEXT = 13
        GMEM_MOVEABLE = 0x0002

        windll = getattr(ctypes, "windll", None)
        if windll is None:
            logger.error("Windows Clipboard API nicht verfügbar")
            return False

        user32 = windll.user32
        kernel32 = windll.kernel32
        _configure_windows_clipboard_api(ctypes, user32, kernel32)
        text_bytes = text.encode("utf-16-le") + b"\x00\x00"
        h_mem = None
        ownership_transferred = False

        try:
            # Text zuerst vorbereiten, damit ein Allokationsfehler das bestehende
            # Clipboard nicht leer räumt.
            h_mem = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(text_bytes))
            if not h_mem:
                logger.error("GlobalAlloc fehlgeschlagen")
                return False

            p_mem = kernel32.GlobalLock(h_mem)
            if not p_mem:
                logger.error("GlobalLock fehlgeschlagen")
                return False

            try:
                ctypes.memmove(p_mem, text_bytes, len(text_bytes))
            finally:
                kernel32.GlobalUnlock(h_mem)

            if not _open_clipboard_with_retry(user32):
                logger.error("Konnte Clipboard nicht öffnen")
                return False

            try:
                if not user32.EmptyClipboard():
                    logger.error("EmptyClipboard fehlgeschlagen")
                    return False

                # In Clipboard setzen (übernimmt Ownership von h_mem bei Erfolg)
                if not user32.SetClipboardData(CF_UNICODETEXT, h_mem):
                    logger.error("SetClipboardData fehlgeschlagen")
                    return False

                ownership_transferred = True
                return True
            finally:
                user32.CloseClipboard()

        except Exception as e:
            logger.error(f"Clipboard-Fehler: {e}")
            return False
        finally:
            if h_mem and not ownership_transferred:
                kernel32.GlobalFree(h_mem)

    def paste(self) -> str | None:
        """Liest Text aus der Zwischenablage via Windows API."""
        import ctypes

        CF_UNICODETEXT = 13

        windll = getattr(ctypes, "windll", None)
        if windll is None:
            return None

        user32 = windll.user32
        kernel32 = windll.kernel32
        _configure_windows_clipboard_api(ctypes, user32, kernel32)

        try:
            if not _open_clipboard_with_retry(user32):
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


def get_clipboard():
    """Gibt den passenden Clipboard-Handler für die aktuelle Plattform zurück."""
    if sys.platform == "darwin":
        return MacOSClipboard()
    elif sys.platform == "win32":
        return WindowsClipboard()
    raise NotImplementedError(f"Clipboard nicht unterstützt für Plattform: {sys.platform}")


__all__ = ["MacOSClipboard", "WindowsClipboard", "get_clipboard"]
