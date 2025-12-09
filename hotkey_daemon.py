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
import threading
import time
from pathlib import Path

# =============================================================================
# Konfiguration
# =============================================================================

SCRIPT_DIR = Path(__file__).resolve().parent
LOG_DIR = SCRIPT_DIR / "logs"
LOG_FILE = LOG_DIR / "hotkey_daemon.log"

# IPC-Dateien (gleich wie transcribe.py)
# Alle whisper_go Prozesse teilen diese Dateien für Kommunikation
PID_FILE = Path("/tmp/whisper_go.pid")
TRANSCRIPT_FILE = Path("/tmp/whisper_go.transcript")
ERROR_FILE = Path("/tmp/whisper_go.error")
LOCK_FILE = Path("/tmp/whisper_go.lock")  # Atomarer Lock verhindert parallele Starts

# Timeouts und Intervalle
# Diese Werte sind empirisch ermittelt für gute UX bei verschiedenen Aufnahmelängen
TRANSCRIPT_TIMEOUT = 60.0  # Großzügig für lange Aufnahmen mit LLM-Refine
POLL_INTERVAL = 0.1        # 100ms = guter Kompromiss zwischen Latenz und CPU-Last
STALE_LOCK_TIMEOUT = 5.0   # Lock älter als 5s = Crash → automatische Recovery
DEBOUNCE_INTERVAL = 0.3    # 300ms verhindert Keyboard-Auto-Repeat Doppelauslösung

# =============================================================================
# Logging
# =============================================================================

logger = logging.getLogger("hotkey_daemon")


def setup_logging(debug: bool = False) -> None:
    """Konfiguriert Logging mit Datei-Output."""
    # Verhindere doppelte Handler bei mehrfachem Aufruf
    if logger.handlers:
        return
    
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
        logger.debug("pynput nicht installiert")
        return False
    except Exception as e:
        logger.debug(f"pynput fehlgeschlagen: {e}")
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
        logger.debug(f"Quartz nicht verfügbar: {e}")
        return False
    except Exception as e:
        logger.debug(f"CGEventPost fehlgeschlagen: {e}")
        return False


def _paste_via_osascript() -> bool:
    """Fallback: Paste via osascript (braucht Accessibility!)."""
    logger.debug("Versuche osascript (braucht Accessibility-Berechtigung)")
    result = subprocess.run(
        [
            "osascript",
            "-e",
            'tell application "System Events" to keystroke "v" using command down',
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        logger.debug(f"osascript fehlgeschlagen: {result.stderr}")
        return False
    logger.info("Auto-Paste: Cmd+V gesendet via osascript")
    return True


def paste_transcript(text: str) -> bool:
    """
    Kopiert Text in Clipboard und fügt via Cmd+V ein.

    Strategie (in Prioritätsreihenfolge):
    1. pynput - Cross-Platform, braucht Accessibility-Berechtigung
    2. CGEventPost (Quartz) - Funktioniert wenn Python TCC-Rechte hat
    3. osascript - Fallback (braucht Accessibility)

    Args:
        text: Text zum Einfügen

    Returns:
        True wenn erfolgreich, False bei Fehler
    """
    logger.info(f"Auto-Paste: '{text[:50]}{'...' if len(text) > 50 else ''}'")

    # 1. In Clipboard kopieren via pbcopy
    try:
        process = subprocess.run(
            ["pbcopy"],
            input=text.encode("utf-8"),
            capture_output=True,
            timeout=5,
        )
        if process.returncode != 0:
            logger.error(f"pbcopy fehlgeschlagen: {process.stderr.decode()}")
            return False
        logger.debug(f"pbcopy: {len(text)} Zeichen kopiert")
    except subprocess.TimeoutExpired:
        logger.error("pbcopy Timeout")
        return False
    except Exception as e:
        logger.error(f"Clipboard-Fehler: {e}")
        return False

    # 2. Clipboard verifizieren via pbpaste
    try:
        result = subprocess.run(
            ["pbpaste"],
            capture_output=True,
            timeout=5,
        )
        clipboard_content = result.stdout.decode("utf-8")
        if clipboard_content != text:
            logger.warning(
                f"Clipboard-Mismatch: erwartet {len(text)} Zeichen, "
                f"bekommen {len(clipboard_content)} Zeichen"
            )
        else:
            logger.info(f"Clipboard verifiziert: {len(text)} Zeichen")
    except Exception as e:
        logger.warning(f"Clipboard-Verify fehlgeschlagen: {e}")

    # 3. Kurze Pause für Clipboard-Sync
    time.sleep(0.1)

    # 4. Cmd+V senden (verschiedene Methoden in Prioritätsreihenfolge)

    # 4a. pynput (Cross-Platform, bevorzugt)
    if _paste_via_pynput():
        return True

    # 4b. CGEventPost (Quartz)
    if _paste_via_quartz():
        return True

    # 4c. osascript (letzter Fallback)
    if _paste_via_osascript():
        return True

    logger.error(
        "Auto-Paste fehlgeschlagen. Bitte Terminal.app in Bedienungshilfen hinzufügen: "
        "Systemeinstellungen → Datenschutz → Bedienungshilfen"
    )
    return False


# =============================================================================
# Recording Control
# =============================================================================


def is_recording() -> bool:
    """Prüft ob eine Aufnahme läuft."""
    if not PID_FILE.exists():
        logger.debug("is_recording: PID-Datei existiert nicht")
        return False

    try:
        pid = int(PID_FILE.read_text().strip())
        # Signal 0 = Existenz-Check
        os.kill(pid, 0)
        logger.debug(f"is_recording: Prozess {pid} läuft")
        return True
    except ValueError:
        logger.debug("is_recording: PID-Datei enthält ungültigen Wert")
        PID_FILE.unlink(missing_ok=True)
        return False
    except ProcessLookupError:
        logger.debug("is_recording: Prozess existiert nicht mehr, entferne PID-Datei")
        PID_FILE.unlink(missing_ok=True)
        return False
    except PermissionError as e:
        logger.warning(f"is_recording: Permission-Fehler: {e}")
        return False


def acquire_start_lock() -> bool:
    """
    Atomarer Lock für Start-Operationen (wie Raycast).

    Verwendet O_EXCL für atomare Erstellung - verhindert Race-Conditions
    wenn mehrere Hotkey-Events gleichzeitig feuern.

    Returns:
        True wenn Lock erworben, False wenn bereits gelockt
    """
    try:
        # Atomare Erstellung: O_CREAT | O_EXCL = "create only if not exists"
        fd = os.open(str(LOCK_FILE), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(time.time()).encode())
        os.close(fd)
        logger.debug("Lock erworben")
        return True
    except FileExistsError:
        # Lock existiert - prüfe ob stale
        try:
            lock_time = float(LOCK_FILE.read_text().strip())
            lock_age = time.time() - lock_time

            if lock_age < STALE_LOCK_TIMEOUT:
                logger.warning(f"Lock existiert ({lock_age:.1f}s alt) - ignoriere")
                return False

            # Stale Lock - brechen und neu versuchen
            logger.warning(f"Stale Lock ({lock_age:.1f}s alt) - breche")
            LOCK_FILE.unlink(missing_ok=True)

            # Nochmal atomar versuchen
            fd = os.open(str(LOCK_FILE), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(time.time()).encode())
            os.close(fd)
            logger.debug("Lock erworben nach Stale-Break")
            return True

        except (ValueError, FileNotFoundError):
            # Korrupter Lock oder gerade gelöscht - nochmal versuchen
            logger.warning("Korrupter/gelöschter Lock - versuche erneut")
            LOCK_FILE.unlink(missing_ok=True)
            try:
                fd = os.open(str(LOCK_FILE), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, str(time.time()).encode())
                os.close(fd)
                return True
            except FileExistsError:
                logger.warning("Lock-Race verloren")
                return False
    except Exception as e:
        logger.error(f"Lock-Fehler: {e}")
        return False


def release_start_lock() -> None:
    """Gibt den Start-Lock frei."""
    LOCK_FILE.unlink(missing_ok=True)
    logger.debug("Lock freigegeben")


def start_recording() -> bool:
    """
    Startet Aufnahme via transcribe.py --record-daemon.

    Wartet auf PID-Datei um Race-Conditions zu vermeiden (Double-Fork braucht Zeit).

    Returns:
        True wenn erfolgreich gestartet
    """
    if is_recording():
        logger.warning("Aufnahme läuft bereits")
        return False

    # Alte IPC-Dateien aufräumen
    ERROR_FILE.unlink(missing_ok=True)
    TRANSCRIPT_FILE.unlink(missing_ok=True)
    PID_FILE.unlink(missing_ok=True)  # Wichtig: Alte PID entfernen für sauberen Start

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
        logger.debug("Popen erfolgreich, warte auf PID-Datei...")

        # Warte auf PID-Datei (Double-Fork braucht Zeit!)
        # Timeout: max 3 Sekunden, polling alle 50ms
        start_time = time.time()
        timeout = 3.0
        poll_interval = 0.05

        while (time.time() - start_time) < timeout:
            if PID_FILE.exists():
                try:
                    pid = int(PID_FILE.read_text().strip())
                    # Prüfe ob Prozess wirklich läuft
                    os.kill(pid, 0)
                    elapsed = (time.time() - start_time) * 1000
                    logger.info(f"✓ Aufnahme gestartet (PID {pid}, {elapsed:.0f}ms)")
                    return True
                except (ValueError, ProcessLookupError):
                    # PID-Datei noch nicht vollständig oder Prozess schon weg
                    pass
            time.sleep(poll_interval)

        # Timeout - Error-Datei prüfen
        if ERROR_FILE.exists():
            error_msg = ERROR_FILE.read_text().strip()
            logger.error(f"Aufnahme-Start fehlgeschlagen: {error_msg}")
        else:
            logger.error(f"Timeout: PID-Datei nicht innerhalb von {timeout}s erstellt")

        return False

    except Exception as e:
        logger.error(f"Aufnahme-Start fehlgeschlagen: {e}")
        return False


def stop_recording() -> str | None:
    """
    Stoppt Aufnahme und wartet auf Transkript.

    Returns:
        Transkript-Text oder None bei Fehler
    """
    logger.debug(f"stop_recording: PID_FILE={PID_FILE}, exists={PID_FILE.exists()}")

    if not PID_FILE.exists():
        logger.warning("stop_recording: Keine PID-Datei gefunden")
        return None

    try:
        pid = int(PID_FILE.read_text().strip())
        logger.debug(f"stop_recording: PID aus Datei gelesen: {pid}")
    except (ValueError, FileNotFoundError) as e:
        logger.warning(f"stop_recording: PID-Datei ungültig: {e}")
        return None

    # SIGUSR1 senden (stoppt Aufnahme in transcribe.py)
    try:
        os.kill(pid, signal.SIGUSR1)
        logger.info(f"stop_recording: SIGUSR1 an PID {pid} gesendet")
    except ProcessLookupError:
        logger.warning(f"stop_recording: Prozess {pid} existiert nicht mehr")
        PID_FILE.unlink(missing_ok=True)
        return None
    except PermissionError as e:
        logger.error(f"stop_recording: Keine Berechtigung für Signal: {e}")
        return None

    # Auf Transkript warten
    logger.debug(f"stop_recording: Warte auf Transkript (max {TRANSCRIPT_TIMEOUT}s)...")
    start_time = time.time()
    deadline = start_time + TRANSCRIPT_TIMEOUT

    while time.time() < deadline:
        elapsed = time.time() - start_time

        # Fehler prüfen
        if ERROR_FILE.exists():
            error_text = ERROR_FILE.read_text().strip()
            ERROR_FILE.unlink(missing_ok=True)
            logger.error(
                f"stop_recording: Transkription fehlgeschlagen nach {elapsed:.1f}s: {error_text}"
            )
            return None

        # Transkript prüfen
        if TRANSCRIPT_FILE.exists():
            transcript = TRANSCRIPT_FILE.read_text().strip()
            TRANSCRIPT_FILE.unlink(missing_ok=True)
            logger.info(
                f"stop_recording: Transkript nach {elapsed:.1f}s erhalten ({len(transcript)} Zeichen)"
            )
            return transcript

        time.sleep(POLL_INTERVAL)

    logger.error(f"stop_recording: Timeout nach {TRANSCRIPT_TIMEOUT}s")
    logger.debug(
        f"stop_recording: ERROR_FILE exists={ERROR_FILE.exists()}, TRANSCRIPT_FILE exists={TRANSCRIPT_FILE.exists()}"
    )
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

        # Lock verhindert Race-Conditions bei schnellen Hotkey-Drücken
        self._toggle_lock = threading.Lock()

        # Debouncing: Ignoriere Hotkey-Events die zu schnell kommen
        self._last_hotkey_time = 0.0

        # Stale IPC-Dateien beim Start aufräumen
        self._cleanup_stale_state()
        self._recording = False

        if mode == "ptt":
            logger.warning(
                "PTT-Mode nicht unterstützt mit QuickMacHotKey. "
                "Verwende Toggle-Mode stattdessen."
            )
            self.mode = "toggle"

    def _cleanup_stale_state(self) -> None:
        """Räumt stale IPC-Dateien von vorherigen Sessions auf."""
        # Prüfe ob eine alte Aufnahme noch läuft
        if PID_FILE.exists():
            try:
                pid = int(PID_FILE.read_text().strip())
                os.kill(pid, 0)  # Prüfe ob Prozess existiert
                logger.warning(
                    f"Alte Aufnahme läuft noch (PID {pid}). "
                    "Sende SIGTERM zum Beenden..."
                )
                os.kill(pid, signal.SIGTERM)

                # Warte bis Prozess wirklich beendet ist (max 2s)
                for _ in range(20):
                    time.sleep(0.1)
                    try:
                        os.kill(pid, 0)
                    except ProcessLookupError:
                        logger.debug(f"Prozess {pid} erfolgreich beendet")
                        break
                else:
                    # Prozess läuft noch nach Timeout - SIGKILL
                    logger.warning(
                        f"Prozess {pid} reagiert nicht auf SIGTERM, sende SIGKILL"
                    )
                    try:
                        os.kill(pid, signal.SIGKILL)
                        time.sleep(0.1)
                    except ProcessLookupError:
                        pass

            except (ValueError, ProcessLookupError):
                logger.info("Stale PID-Datei gefunden, räume auf...")
            except PermissionError:
                logger.warning(f"Keine Berechtigung für PID {pid}")

            # Aufräumen
            PID_FILE.unlink(missing_ok=True)
            TRANSCRIPT_FILE.unlink(missing_ok=True)
            ERROR_FILE.unlink(missing_ok=True)
            LOCK_FILE.unlink(missing_ok=True)
            logger.debug("IPC-Dateien aufgeräumt")

    def _on_hotkey(self) -> None:
        """Callback bei Hotkey-Aktivierung."""
        # 1. Debouncing: Ignoriere Events die zu schnell kommen (Key-Repeat)
        now = time.time()
        time_since_last = now - self._last_hotkey_time
        if time_since_last < DEBOUNCE_INTERVAL:
            logger.debug(f"Debounce: Event ignoriert ({time_since_last*1000:.0f}ms)")
            return
        self._last_hotkey_time = now

        # 2. Thread-Lock: Verhindert parallele Callback-Ausführung
        if not self._toggle_lock.acquire(blocking=False):
            logger.warning("Hotkey ignoriert - Toggle bereits aktiv")
            return

        try:
            logger.debug(f"Hotkey gedrückt! Recording-State: {self._recording}")
            self._toggle_recording()
        finally:
            self._toggle_lock.release()

    def _toggle_recording(self) -> None:
        """Toggle-Mode: Start/Stop bei jedem Tastendruck."""
        # State-Resync: Prüfe ob lokaler State mit Prozess-Zustand übereinstimmt
        actual_recording = is_recording()
        if self._recording != actual_recording:
            logger.warning(
                f"State-Mismatch: self._recording={self._recording}, "
                f"actual={actual_recording}. Synchronisiere..."
            )
            self._recording = actual_recording

        if self._recording:
            # === STOP ===
            logger.info("Toggle: Stop - Beende Aufnahme...")
            transcript = stop_recording()
            self._recording = False

            if transcript is not None:
                logger.info(f"Transkript: '{transcript}'")
                success = paste_transcript(transcript)
                if success:
                    logger.info("✓ Text erfolgreich eingefügt")
                else:
                    logger.error("✗ Auto-Paste fehlgeschlagen")
            else:
                logger.warning("Kein Transkript erhalten")
        else:
            # === START ===
            # Atomarer File-Lock verhindert parallele Starts (wie Raycast)
            if not acquire_start_lock():
                logger.warning("Start ignoriert - Lock nicht erworben")
                return

            try:
                logger.info("Toggle: Start - Starte Aufnahme...")
                if start_recording():
                    self._recording = True
                    logger.info("✓ Aufnahme gestartet")
                else:
                    logger.error("✗ Aufnahme konnte nicht gestartet werden")
            finally:
                release_start_lock()

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
