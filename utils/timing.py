"""Zeitmessung für whisper_go.

Context Manager und Hilfsfunktionen für Performance-Tracking.
"""

import time
from contextlib import contextmanager

from .logging import get_logger, get_session_id


def format_duration(milliseconds: float) -> str:
    """Formatiert Dauer menschenlesbar: ms für kurze, s für längere Zeiten."""
    if milliseconds >= 1000:
        return f"{milliseconds / 1000:.2f}s"
    return f"{milliseconds:.0f}ms"


def log_preview(text: str, max_length: int = 100) -> str:
    """Kürzt Text für Log-Ausgabe mit Ellipsis wenn nötig."""
    if len(text) <= max_length:
        return text
    return f"{text[:max_length]}..."


@contextmanager
def timed_operation(name: str):
    """Kontextmanager für Zeitmessung mit automatischem Logging.

    Usage:
        with timed_operation("API-Call"):
            response = api.call()
    """
    logger = get_logger()
    session_id = get_session_id()
    start = time.perf_counter()
    try:
        yield
    finally:
        elapsed_ms = (time.perf_counter() - start) * 1000
        logger.info(f"[{session_id}] {name}: {format_duration(elapsed_ms)}")
