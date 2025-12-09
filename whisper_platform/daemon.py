"""Daemon/IPC Implementierungen.

Plattformspezifische Daemon-Prozess-Kontrolle und IPC.
macOS: Double-Fork + SIGUSR1 Signale
Windows: CREATE_NEW_PROCESS_GROUP + Named Events
"""

import logging
import os
import signal
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger("whisper_go.platform.daemon")

# Standard-PID-Datei Pfad (platform-aware)
if sys.platform == "win32":
    TEMP_DIR = Path(os.environ.get("TEMP", "C:\\Temp"))
else:
    TEMP_DIR = Path("/tmp")

PID_FILE = TEMP_DIR / "whisper_go.pid"


class MacOSDaemonController:
    """macOS Daemon-Controller mit Double-Fork und SIGUSR1.

    Double-Fork verhindert Zombie-Prozesse wenn der Parent (z.B. Raycast)
    den Prozess nicht ordnungsgemäß mit wait() aufräumt.
    """

    def __init__(self, pid_file: Path | None = None) -> None:
        self.pid_file = pid_file or PID_FILE

    def start(self, command: list[str]) -> int | None:
        """Startet Daemon via Double-Fork.

        Args:
            command: Kommando als Liste (z.B. ['python', 'transcribe.py', '--daemon'])

        Returns:
            PID des gestarteten Prozesses oder None bei Fehler
        """
        try:
            # Erster Fork: Parent kann sofort exit() machen
            pid = os.fork()
            if pid > 0:
                # Parent: Warte auf erstes Child und return
                os.waitpid(pid, 0)
                # PID-File lesen um die echte Daemon-PID zu bekommen
                if self.pid_file.exists():
                    return int(self.pid_file.read_text().strip())
                return None

            # Child: Neue Session starten (löst von Terminal)
            os.setsid()

            # Zweiter Fork: Verhindert Terminal-Übernahme
            pid = os.fork()
            if pid > 0:
                # Erstes Child: Exit sofort (wird von launchd adoptiert)
                os._exit(0)

            # Grandchild: Wir sind der echte Daemon
            # Starte das eigentliche Kommando
            os.execvp(command[0], command)

        except OSError as e:
            logger.error(f"Daemon-Start fehlgeschlagen: {e}")
            return None

        return None  # Wird nie erreicht im Grandchild

    def stop(self, pid: int) -> bool:
        """Stoppt Daemon via SIGUSR1.

        Args:
            pid: Prozess-ID des zu stoppenden Daemons

        Returns:
            True wenn erfolgreich gestoppt
        """
        try:
            # SIGUSR1 signalisiert dem Daemon zu stoppen
            os.kill(pid, signal.SIGUSR1)
            logger.debug(f"SIGUSR1 an PID {pid} gesendet")
            return True
        except ProcessLookupError:
            logger.debug(f"Prozess {pid} existiert nicht mehr")
            return False
        except PermissionError:
            logger.error(f"Keine Berechtigung für PID {pid}")
            return False

    def is_running(self, pid: int) -> bool:
        """Prüft ob Prozess läuft via Signal 0."""
        try:
            os.kill(pid, 0)  # Signal 0 = Existenz-Check
            return True
        except (ProcessLookupError, PermissionError):
            return False

    def kill(self, pid: int, force: bool = False) -> bool:
        """Beendet Prozess (SIGTERM oder SIGKILL).

        Args:
            pid: Prozess-ID
            force: Wenn True, SIGKILL statt SIGTERM

        Returns:
            True wenn Signal gesendet wurde
        """
        sig = signal.SIGKILL if force else signal.SIGTERM
        try:
            os.kill(pid, sig)
            return True
        except (ProcessLookupError, PermissionError):
            return False


class WindowsDaemonController:
    """Windows Daemon-Controller mit Named Events.

    Windows hat kein fork(), daher nutzen wir:
    - CREATE_NEW_PROCESS_GROUP für detached Prozesse
    - Named Events für IPC (statt SIGUSR1)
    """

    STOP_EVENT_PREFIX = "Global\\WhisperGoStop_"

    def __init__(self, pid_file: Path | None = None) -> None:
        self.pid_file = pid_file or PID_FILE
        self._win32event = None
        self._win32api = None

        try:
            import win32event  # type: ignore[import-not-found]
            import win32api  # type: ignore[import-not-found]
            self._win32event = win32event
            self._win32api = win32api
        except ImportError:
            logger.debug("pywin32 nicht verfügbar")

    def start(self, command: list[str]) -> int | None:
        """Startet Daemon als detached Prozess.

        Args:
            command: Kommando als Liste

        Returns:
            PID des gestarteten Prozesses
        """
        try:
            # CREATE_NEW_PROCESS_GROUP + DETACHED_PROCESS für echten Daemon
            process = subprocess.Popen(
                command,
                creationflags=(
                    subprocess.CREATE_NEW_PROCESS_GROUP |
                    subprocess.DETACHED_PROCESS
                ),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            logger.debug(f"Daemon gestartet mit PID {process.pid}")
            return process.pid
        except Exception as e:
            logger.error(f"Daemon-Start fehlgeschlagen: {e}")
            return None

    def stop(self, pid: int) -> bool:
        """Stoppt Daemon via Named Event.

        Setzt ein Event das der Daemon pollt.
        """
        if self._win32event is None:
            logger.error("pywin32 nicht verfügbar")
            return False

        try:
            event_name = f"{self.STOP_EVENT_PREFIX}{pid}"
            # Event öffnen und setzen
            event = self._win32event.OpenEvent(
                self._win32event.EVENT_MODIFY_STATE,
                False,
                event_name
            )
            self._win32event.SetEvent(event)
            self._win32api.CloseHandle(event)
            logger.debug(f"Stop-Event für PID {pid} gesetzt")
            return True
        except Exception as e:
            logger.debug(f"Stop-Event fehlgeschlagen: {e}")
            return False

    def is_running(self, pid: int) -> bool:
        """Prüft ob Prozess läuft via psutil."""
        try:
            import psutil
            return psutil.pid_exists(pid)
        except ImportError:
            # Fallback: tasklist
            try:
                result = subprocess.run(
                    ["tasklist", "/FI", f"PID eq {pid}"],
                    capture_output=True,
                    text=True,
                )
                return str(pid) in result.stdout
            except Exception:
                return False

    def kill(self, pid: int, force: bool = False) -> bool:
        """Beendet Prozess via taskkill."""
        try:
            cmd = ["taskkill"]
            if force:
                cmd.append("/F")
            cmd.extend(["/PID", str(pid)])
            subprocess.run(cmd, capture_output=True)
            return True
        except Exception:
            return False


# Convenience-Funktion
def get_daemon_controller(pid_file: Path | None = None):
    """Gibt den passenden Daemon-Controller für die aktuelle Plattform zurück."""
    if sys.platform == "darwin":
        return MacOSDaemonController(pid_file)
    elif sys.platform == "win32":
        return WindowsDaemonController(pid_file)
    # Linux: gleiche Implementierung wie macOS (POSIX)
    return MacOSDaemonController(pid_file)


__all__ = [
    "MacOSDaemonController",
    "WindowsDaemonController",
    "get_daemon_controller",
    "PID_FILE",
    "TEMP_DIR",
]
