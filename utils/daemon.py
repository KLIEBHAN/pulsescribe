"""
Daemon-Utilities und Prozess-Management.

Extrahiert aus transcribe.py, um die CLI-Logik von OS-Details zu trennen.
"""

import os
import signal
import subprocess
import sys
import time

from config import PID_FILE
from utils.logging import get_session_id
import logging

logger = logging.getLogger("whisper_go")


def is_whisper_go_process(pid: int) -> bool:
    """Prüft ob die PID zu einem whisper_go Prozess gehört."""
    try:
        # ps -p PID -o command=
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "command="],
            capture_output=True,
            text=True,
            timeout=1,
        )
        if result.returncode != 0:
            return False
        
        command = result.stdout.strip()
        # Prüfe auf transcribe.py und Argumente
        return "transcribe.py" in command and "--record-daemon" in command
    except Exception:
        return False


def daemonize() -> None:
    """
    Double-Fork für echte Daemon-Prozesse (verhindert Zombies).

    Wenn Raycast spawn(detached) + unref() nutzt, wird wait() nie aufgerufen.
    Der beendete Python-Prozess bleibt als Zombie. Lösung: Double-Fork.
    """
    if os.fork() > 0:
        # Parent 1 beenden -> Child 1 wird Orphan (adoptiert von init/launchd)
        sys.exit(0)

    # Child 1: Session Leader werden
    os.setsid()

    if os.fork() > 0:
        # Child 1 beenden -> Child 2 ist komplett entkoppelt
        sys.exit(0)

    # Child 2: Der eigentliche Daemon
    sys.stdout.flush()
    sys.stderr.flush()

    # PID schreiben
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(os.getpid()))


def cleanup_stale_pid_file() -> None:
    """
    Entfernt PID-File und killt alten Prozess falls nötig (Crash-Recovery).
    """
    if not PID_FILE.exists():
        return

    try:
        old_pid = int(PID_FILE.read_text().strip())

        # Eigene PID? Dann nicht killen!
        if old_pid == os.getpid():
            return

        # Prozess läuft noch?
        try:
            # Signal 0 ist ein "Ping" – prüft Existenz ohne Seiteneffekte
            os.kill(old_pid, 0)
        except ProcessLookupError:
            # Prozess tot -> PID-File löschen
            logger.info(f"[{get_session_id()}] Stale PID-File gelöscht (Prozess weg): {PID_FILE}")
            PID_FILE.unlink(missing_ok=True)
            return

        # SICHERHEIT: Nur killen wenn es wirklich ein whisper_go Prozess ist!
        if not is_whisper_go_process(old_pid):
            logger.warning(
                f"[{get_session_id()}] PID {old_pid} ist kein whisper_go Prozess, "
                f"lösche nur PID-File (PID-Recycling?)"
            )
            PID_FILE.unlink(missing_ok=True)
            return

        # Prozess läuft noch und ist whisper_go -> KILL
        logger.warning(
            f"[{get_session_id()}] Alter Daemon-Prozess {old_pid} läuft noch, beende ihn..."
        )

        # Erst freundlich (SIGTERM), dann hart (SIGKILL)
        try:
            os.kill(old_pid, signal.SIGTERM)
            time.sleep(0.5)
            try:
                os.kill(old_pid, 0)
                os.kill(old_pid, signal.SIGKILL)
                logger.info(
                    f"[{get_session_id()}] Alter Prozess {old_pid} gekillt (SIGKILL)"
                )
            except ProcessLookupError:
                logger.info(
                    f"[{get_session_id()}] Alter Prozess {old_pid} beendet (SIGTERM)"
                )
        except ProcessLookupError:
            pass

        PID_FILE.unlink(missing_ok=True)
    except (ValueError, ProcessLookupError):
        logger.info(f"[{get_session_id()}] Stale PID-File gelöscht: {PID_FILE}")
        PID_FILE.unlink(missing_ok=True)
    except PermissionError:
        logger.error(f"[{get_session_id()}] Keine Berechtigung PID-File zu löschen: {PID_FILE}")
