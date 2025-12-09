"""Utility-Module für whisper_go.

Gemeinsame Hilfsfunktionen für Logging und Zeitmessung.

Usage:
    from utils import setup_logging, log, error, timed_operation

    setup_logging(debug=True)
    with timed_operation("API-Call"):
        do_something()
"""

from .logging import setup_logging, log, error, get_logger, get_session_id
from .timing import timed_operation, format_duration

__all__ = [
    "setup_logging",
    "log",
    "error",
    "get_logger",
    "get_session_id",
    "timed_operation",
    "format_duration",
]
