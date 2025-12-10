"""Logging-Setup für whisper_go.

Konfiguriert Datei-Logging mit Rotation und optionalem stderr-Output.
"""

import logging
import sys
import uuid
from logging.handlers import RotatingFileHandler

# Logger-Singleton
logger = logging.getLogger("whisper_go")

# Session-ID für Korrelation (wird beim ersten setup_logging() generiert)
_session_id: str = ""


def _generate_session_id() -> str:
    """Erzeugt kurze, lesbare Session-ID (8 Zeichen)."""
    return uuid.uuid4().hex[:8]


def get_session_id() -> str:
    """Gibt die aktuelle Session-ID zurück."""
    global _session_id
    if not _session_id:
        _session_id = _generate_session_id()
    return _session_id


def get_logger() -> logging.Logger:
    """Gibt den whisper_go Logger zurück."""
    return logger


def setup_logging(debug: bool = False) -> None:
    """Konfiguriert Logging: Datei mit Rotation + optional stderr.

    Args:
        debug: Wenn True, wird auch auf stderr geloggt
    """
    # Lazy import: bricht circular import (config → utils → logging → config)
    from config import LOG_FILE

    global _session_id

    # Session-ID nur einmal generieren
    if not _session_id:
        _session_id = _generate_session_id()

    # Verhindere doppelte Handler bei mehrfachem Aufruf
    if logger.handlers:
        logger.setLevel(logging.DEBUG if debug else logging.INFO)
        return

    logger.setLevel(logging.DEBUG if debug else logging.INFO)

    # Log-Verzeichnis sicherstellen
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass # Ignorieren falls keine Berechtigung, Handler wird dann meckern

    # Datei-Handler mit Rotation (max 1MB, 3 Backups)
    file_handler = RotatingFileHandler(
        LOG_FILE, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%H:%M:%S")
    )
    logger.addHandler(file_handler)

    # Stderr-Handler (nur im Debug-Modus)
    if debug:
        stderr_handler = logging.StreamHandler(sys.stderr)
        stderr_handler.setLevel(logging.DEBUG)
        stderr_handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
        logger.addHandler(stderr_handler)


def log(message: str) -> None:
    """Status-Meldung auf stderr.

    Warum stderr? Hält stdout sauber für Pipes (z.B. `transcribe.py | pbcopy`).
    """
    print(message, file=sys.stderr)


def error(message: str) -> None:
    """Fehlermeldung auf stderr."""
    print(f"Fehler: {message}", file=sys.stderr)
