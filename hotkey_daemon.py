#!/usr/bin/env python3
"""
whisper_go Hotkey-Daemon – Systemweite Spracheingabe per Tastenkürzel.

Verwendet QuickMacHotKey (Carbon RegisterEventHotKey API) für globale Hotkeys.
Keine Accessibility-Berechtigung erforderlich!

Usage:
    python hotkey_daemon.py              # Startet Daemon im Vordergrund
    ./scripts/install_hotkey_daemon.sh   # Als LaunchAgent installieren

Konfiguration via .env:
    WHISPER_GO_HOTKEY="f19"              # Hotkey (default: F19)
    WHISPER_GO_HOTKEY_MODE="toggle"      # toggle | ptt
"""

import logging
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

# =============================================================================
# Konfiguration
# =============================================================================

SCRIPT_DIR = Path(__file__).resolve().parent
LOG_DIR = SCRIPT_DIR / "logs"
LOG_FILE = LOG_DIR / "hotkey_daemon.log"

# IPC-Dateien (gleich wie transcribe.py)
PID_FILE = Path("/tmp/whisper_go.pid")
TRANSCRIPT_FILE = Path("/tmp/whisper_go.transcript")
ERROR_FILE = Path("/tmp/whisper_go.error")

# Timeouts
TRANSCRIPT_TIMEOUT = 60.0  # Max. Wartezeit auf Transkript
POLL_INTERVAL = 0.1  # Polling-Intervall in Sekunden

# =============================================================================
# Logging
# =============================================================================

logger = logging.getLogger("hotkey_daemon")


def setup_logging(debug: bool = False) -> None:
    """Konfiguriert Logging mit Datei-Output."""
    LOG_DIR.mkdir(exist_ok=True)

    logger.setLevel(logging.DEBUG if debug else logging.INFO)

    # Datei-Handler
    from logging.handlers import RotatingFileHandler

    file_handler = RotatingFileHandler(
        LOG_FILE, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%H:%M:%S")
    )
    logger.addHandler(file_handler)

    # Stderr-Handler (für Debug)
    if debug:
        stderr_handler = logging.StreamHandler(sys.stderr)
        stderr_handler.setLevel(logging.DEBUG)
        stderr_handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
        logger.addHandler(stderr_handler)


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


# =============================================================================
# Auto-Paste
# =============================================================================


def paste_transcript(text: str) -> None:
    """
    Kopiert Text in Clipboard und fügt via Cmd+V ein.

    Args:
        text: Text zum Einfügen
    """
    import pyperclip

    # 1. In Clipboard kopieren
    pyperclip.copy(text)
    logger.debug(f"Text in Clipboard: {len(text)} Zeichen")

    # 2. Kurze Pause für Clipboard-Sync
    time.sleep(0.05)

    # 3. Cmd+V via AppleScript (zuverlässiger als pynput)
    subprocess.run(
        [
            "osascript",
            "-e",
            'tell application "System Events" to keystroke "v" using command down',
        ],
        capture_output=True,
    )

    logger.info("Auto-Paste ausgeführt")


# =============================================================================
# Recording Control
# =============================================================================


def is_recording() -> bool:
    """Prüft ob eine Aufnahme läuft."""
    if not PID_FILE.exists():
        return False

    try:
        pid = int(PID_FILE.read_text().strip())
        # Signal 0 = Existenz-Check
        os.kill(pid, 0)
        return True
    except (ValueError, ProcessLookupError, PermissionError):
        # PID ungültig oder Prozess existiert nicht
        PID_FILE.unlink(missing_ok=True)
        return False


def start_recording() -> bool:
    """
    Startet Aufnahme via transcribe.py --record-daemon.

    Returns:
        True wenn erfolgreich gestartet
    """
    if is_recording():
        logger.warning("Aufnahme läuft bereits")
        return False

    # Alte IPC-Dateien aufräumen
    ERROR_FILE.unlink(missing_ok=True)
    TRANSCRIPT_FILE.unlink(missing_ok=True)

    # transcribe.py --record-daemon starten
    script_path = SCRIPT_DIR / "transcribe.py"
    if not script_path.exists():
        logger.error(f"transcribe.py nicht gefunden: {script_path}")
        return False

    # Python-Executable ermitteln (gleicher wie aktueller Prozess)
    python_path = sys.executable

    logger.info(f"Starte Aufnahme: {python_path} {script_path} --record-daemon")

    try:
        # Detached starten (wie Raycast)
        subprocess.Popen(
            [python_path, str(script_path), "--record-daemon"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        logger.info("Aufnahme-Prozess gestartet")
        return True
    except Exception as e:
        logger.error(f"Aufnahme-Start fehlgeschlagen: {e}")
        return False


def stop_recording() -> str | None:
    """
    Stoppt Aufnahme und wartet auf Transkript.

    Returns:
        Transkript-Text oder None bei Fehler
    """
    if not PID_FILE.exists():
        logger.warning("Keine aktive Aufnahme")
        return None

    try:
        pid = int(PID_FILE.read_text().strip())
    except (ValueError, FileNotFoundError):
        logger.warning("PID-Datei ungültig")
        return None

    # SIGUSR1 senden (stoppt Aufnahme in transcribe.py)
    try:
        os.kill(pid, signal.SIGUSR1)
        logger.info(f"SIGUSR1 an PID {pid} gesendet")
    except ProcessLookupError:
        logger.warning(f"Prozess {pid} existiert nicht mehr")
        PID_FILE.unlink(missing_ok=True)
        return None

    # Auf Transkript warten
    deadline = time.time() + TRANSCRIPT_TIMEOUT
    while time.time() < deadline:
        # Fehler prüfen
        if ERROR_FILE.exists():
            error_text = ERROR_FILE.read_text().strip()
            ERROR_FILE.unlink(missing_ok=True)
            logger.error(f"Transkription fehlgeschlagen: {error_text}")
            return None

        # Transkript prüfen
        if TRANSCRIPT_FILE.exists():
            transcript = TRANSCRIPT_FILE.read_text().strip()
            TRANSCRIPT_FILE.unlink(missing_ok=True)
            logger.info(f"Transkript erhalten: {len(transcript)} Zeichen")
            return transcript

        time.sleep(POLL_INTERVAL)

    logger.error("Timeout beim Warten auf Transkript")
    return None


# =============================================================================
# Hotkey Daemon (QuickMacHotKey)
# =============================================================================


class HotkeyDaemon:
    """
    Globaler Hotkey-Daemon für whisper_go.

    Verwendet QuickMacHotKey für systemweite Hotkeys ohne Accessibility.
    Unterstützt Toggle-Mode (PTT nicht unterstützt mit dieser API).
    """

    def __init__(self, hotkey: str = "f19", mode: str = "toggle"):
        """
        Initialisiert Daemon.

        Args:
            hotkey: Hotkey-String (z.B. "f19", "cmd+shift+r")
            mode: "toggle" (PTT nicht unterstützt)
        """
        self.hotkey = hotkey
        self.mode = mode
        self._recording = False

        if mode == "ptt":
            logger.warning(
                "PTT-Mode nicht unterstützt mit QuickMacHotKey. "
                "Verwende Toggle-Mode stattdessen."
            )
            self.mode = "toggle"

    def _on_hotkey(self) -> None:
        """Callback bei Hotkey-Aktivierung."""
        self._toggle_recording()

    def _toggle_recording(self) -> None:
        """Toggle-Mode: Start/Stop bei jedem Tastendruck."""
        if self._recording:
            logger.info("Toggle: Stop")
            transcript = stop_recording()
            self._recording = False
            if transcript:
                paste_transcript(transcript)
        else:
            logger.info("Toggle: Start")
            if start_recording():
                self._recording = True

    def run(self) -> None:
        """Startet Daemon (blockiert)."""
        from quickmachotkey import quickHotKey
        from AppKit import NSApplication

        # Hotkey parsen
        virtual_key, modifier_mask = parse_hotkey(self.hotkey)

        logger.info(
            f"Hotkey-Daemon gestartet: hotkey={self.hotkey}, "
            f"virtualKey={virtual_key}, modifierMask={modifier_mask}"
        )
        print("🎤 whisper_go Hotkey-Daemon läuft", file=sys.stderr)
        print(f"   Hotkey: {self.hotkey}", file=sys.stderr)
        print(f"   Modus:  {self.mode}", file=sys.stderr)
        print("   Beenden mit Ctrl+C", file=sys.stderr)

        # Hotkey registrieren
        @quickHotKey(virtualKey=virtual_key, modifierMask=modifier_mask)
        def hotkey_handler() -> None:
            self._on_hotkey()

        # NSApplication Event-Loop starten (erforderlich für QuickMacHotKey)
        app = NSApplication.sharedApplication()
        app.run()


# =============================================================================
# Environment Loading
# =============================================================================


def load_environment() -> None:
    """Lädt .env-Datei falls vorhanden."""
    try:
        from dotenv import load_dotenv

        env_file = SCRIPT_DIR / ".env"
        load_dotenv(env_file if env_file.exists() else None)
    except ImportError:
        pass


# =============================================================================
# Main
# =============================================================================


def main() -> int:
    """CLI-Einstiegspunkt."""
    import argparse

    parser = argparse.ArgumentParser(
        description="whisper_go Hotkey-Daemon",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Beispiele:
  %(prog)s                          # Mit Defaults aus .env
  %(prog)s --hotkey f19             # F19 als Hotkey
  %(prog)s --hotkey cmd+shift+r     # Tastenkombination
        """,
    )

    parser.add_argument(
        "--hotkey",
        default=None,
        help="Hotkey (default: WHISPER_GO_HOTKEY oder 'f19')",
    )
    parser.add_argument(
        "--mode",
        choices=["toggle", "ptt"],
        default=None,
        help="Modus: toggle (PTT nicht unterstützt)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Debug-Logging aktivieren",
    )

    args = parser.parse_args()

    # Environment laden
    load_environment()
    setup_logging(debug=args.debug)

    # Konfiguration: CLI > ENV > Default
    hotkey = args.hotkey or os.getenv("WHISPER_GO_HOTKEY", "f19")
    mode = args.mode or os.getenv("WHISPER_GO_HOTKEY_MODE", "toggle")

    # Daemon starten
    try:
        daemon = HotkeyDaemon(hotkey=hotkey, mode=mode)
        daemon.run()
    except ValueError as e:
        print(f"Konfigurationsfehler: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\n👋 Daemon beendet", file=sys.stderr)
        return 0
    except Exception as e:
        logger.exception(f"Unerwarteter Fehler: {e}")
        print(f"Fehler: {e}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
