"""Logging-Setup für PulseScribe.

Konfiguriert Datei-Logging mit Rotation und optionalem stderr-Output.
"""

import logging
import sys
import uuid
from logging.handlers import RotatingFileHandler
from pathlib import Path

# Logger-Singleton
logger = logging.getLogger("pulsescribe")

# Session-ID für Korrelation (wird beim ersten setup_logging() generiert)
_session_id: str = ""
_fallback_stderr_handler: logging.Handler | None = None
_debug_stderr_handler: logging.Handler | None = None


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
    """Gibt den pulsescribe Logger zurück."""
    return logger


def _remove_debug_stderr_handler() -> None:
    """Remove the optional debug stderr handler if it is currently installed."""
    global _debug_stderr_handler
    if _debug_stderr_handler is None:
        return
    try:
        logger.removeHandler(_debug_stderr_handler)
    finally:
        try:
            _debug_stderr_handler.close()
        except Exception:
            pass
        _debug_stderr_handler = None



def _sync_stderr_handlers(*, debug: bool) -> None:
    """Keep stderr handlers aligned with the latest debug mode."""
    global _debug_stderr_handler

    if _fallback_stderr_handler is not None:
        _fallback_stderr_handler.setLevel(logging.DEBUG if debug else logging.INFO)

    # When we already use stderr as the primary fallback sink, avoid adding a
    # second debug-only stderr handler that would duplicate each log line.
    if _fallback_stderr_handler is not None:
        _remove_debug_stderr_handler()
        return

    if not debug:
        _remove_debug_stderr_handler()
        return

    if _debug_stderr_handler is not None:
        return

    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setLevel(logging.DEBUG)
    stderr_handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
    logger.addHandler(stderr_handler)
    _debug_stderr_handler = stderr_handler



def setup_logging(debug: bool = False) -> None:
    """Konfiguriert Logging: Datei mit Rotation + optional stderr.

    Args:
        debug: Wenn True, wird auch auf stderr geloggt
    """
    # Lazy import: bricht circular import (config → utils → logging → config)
    from config import LOG_FILE

    global _session_id, _fallback_stderr_handler

    # Session-ID nur einmal generieren
    if not _session_id:
        _session_id = _generate_session_id()

    # Verhindere doppelte Handler bei mehrfachem Aufruf, aber erlaube das
    # spaetere Nachschaerfen des Debug-Modus (wichtig fuer Windows --debug).
    if logger.handlers:
        logger.setLevel(logging.DEBUG if debug else logging.INFO)
        _sync_stderr_handlers(debug=debug)
        return

    logger.setLevel(logging.DEBUG if debug else logging.INFO)
    _fallback_stderr_handler = None

    # Log-Verzeichnis sicherstellen
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass  # Ignorieren falls keine Berechtigung, Handler wird dann meckern

    handler_added = False

    # Datei-Handler mit Rotation (max 5MB, 5 Backups)
    try:
        file_handler = RotatingFileHandler(
            LOG_FILE, maxBytes=5_000_000, backupCount=5, encoding="utf-8"
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%H:%M:%S")
        )
        logger.addHandler(file_handler)
        handler_added = True
    except PermissionError:
        # Fallback: /tmp, wenn Home-Verzeichnis nicht beschreibbar (z.B. Sandbox)
        try:
            fallback = Path("/tmp/pulsescribe.log")
            fallback.parent.mkdir(parents=True, exist_ok=True)
            file_handler = RotatingFileHandler(
                fallback, maxBytes=5_000_000, backupCount=5, encoding="utf-8"
            )
            file_handler.setLevel(logging.DEBUG)
            file_handler.setFormatter(
                logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%H:%M:%S")
            )
            logger.addHandler(file_handler)
            handler_added = True
        except Exception:
            pass
    except Exception:
        # Keine harten Fehler, Logging darf App-Start nicht blockieren
        pass

    if not handler_added:
        # Minimaler Fallback, um Stillstand zu vermeiden
        stderr_handler = logging.StreamHandler(sys.stderr)
        stderr_handler.setLevel(logging.DEBUG if debug else logging.INFO)
        stderr_handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%H:%M:%S")
        )
        logger.addHandler(stderr_handler)
        _fallback_stderr_handler = stderr_handler

    _sync_stderr_handlers(debug=debug)


def log(message: str) -> None:
    """Status-Meldung auf stderr.

    Warum stderr? Hält stdout sauber für Pipes (z.B. `transcribe.py | pbcopy`).
    """
    print(message, file=sys.stderr)


def error(message: str) -> None:
    """Fehlermeldung auf stderr."""
    print(f"Fehler: {message}", file=sys.stderr)
