"""Zeitmessung für pulsescribe.

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


def redacted_text_summary(text: str) -> str:
    """Return a privacy-safe summary for user-provided text in logs."""
    char_count = len(text or "")
    if char_count <= 0:
        return "<redacted empty>"
    return f"<redacted {char_count} chars>"


@contextmanager
def timed_operation(
    name: str,
    *,
    logger=None,
    include_session: bool = True,
):
    """Kontextmanager für Zeitmessung mit automatischem Logging.

    Args:
        name: Name der Operation (Log-Label).
        logger: Optionaler Logger. Default: pulsescribe Root-Logger.
        include_session: Wenn False, wird keine Session-ID vorangestellt.

    Usage:
        with timed_operation("API-Call"):
            response = api.call()
        with timed_operation("Provider-Call", logger=provider_logger, include_session=False):
            ...
    """
    op_logger = logger or get_logger()
    session_id = get_session_id() if include_session else ""
    prefix = f"[{session_id}] " if session_id else ""
    start = time.perf_counter()
    try:
        yield
    finally:
        elapsed_ms = (time.perf_counter() - start) * 1000
        op_logger.info(f"{prefix}{name}: {format_duration(elapsed_ms)}")
