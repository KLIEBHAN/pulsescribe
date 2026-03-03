"""Transkript-Historie für PulseScribe.

Speichert transkribierte Texte in ~/.pulsescribe/history.jsonl.
Jede Zeile ist ein JSON-Objekt mit Timestamp und Text.
"""

import json
import logging
from datetime import datetime

from config import USER_CONFIG_DIR
from utils.log_tail import read_file_tail_lines

HISTORY_FILE = USER_CONFIG_DIR / "history.jsonl"
MAX_HISTORY_SIZE_MB = 10  # Max file size before rotation
_RECENT_SCAN_BYTES_MIN = 128_000
_RECENT_SCAN_BYTES_MAX = 4_000_000
_RECENT_LINES_FACTOR = 3

logger = logging.getLogger(__name__)


def save_transcript(
    text: str,
    *,
    mode: str | None = None,
    language: str | None = None,
    refined: bool = False,
    app_context: str | None = None,
) -> bool:
    """Speichert ein Transkript in der Historie.

    Args:
        text: Der transkribierte Text
        mode: Transkriptions-Modus (deepgram, local, etc.)
        language: Erkannte/gesetzte Sprache
        refined: Ob LLM-Refine angewendet wurde
        app_context: Aktive App beim Transkribieren

    Returns:
        True bei Erfolg, False bei Fehler
    """
    if not text or not text.strip():
        return False

    try:
        HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)

        # Check file size and rotate if needed
        _rotate_if_needed()

        entry = {
            "timestamp": datetime.now().isoformat(),
            "text": text.strip(),
        }

        # Optional fields (nur wenn gesetzt)
        if mode:
            entry["mode"] = mode
        if language:
            entry["language"] = language
        if refined:
            entry["refined"] = True
        if app_context:
            entry["app"] = app_context

        with HISTORY_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

        logger.debug(f"Transcript saved to history: {text[:50]}...")
        return True

    except Exception as e:
        logger.warning(f"Failed to save transcript to history: {e}")
        return False


def _rotate_if_needed() -> None:
    """Rotiert die Historie wenn sie zu groß wird."""
    if not HISTORY_FILE.exists():
        return

    try:
        size_mb = HISTORY_FILE.stat().st_size / (1024 * 1024)
        if size_mb < MAX_HISTORY_SIZE_MB:
            return

        # Rotate: Keep last 50% of entries
        lines = HISTORY_FILE.read_text(encoding="utf-8").splitlines()
        keep_count = len(lines) // 2
        if keep_count > 0:
            HISTORY_FILE.write_text(
                "\n".join(lines[-keep_count:]) + "\n",
                encoding="utf-8",
            )
            logger.info(f"History rotated: kept {keep_count} of {len(lines)} entries")

    except Exception as e:
        logger.warning(f"History rotation failed: {e}")


def get_recent_transcripts(count: int = 10) -> list[dict]:
    """Gibt die letzten N Transkripte zurück.

    Args:
        count: Anzahl der Einträge (default: 10)

    Returns:
        Liste von Transkript-Dictionaries (neueste zuerst)
    """
    if count <= 0 or not HISTORY_FILE.exists():
        return []

    try:
        tail_max_lines = max(count * _RECENT_LINES_FACTOR, count + 10)
        tail_max_scan_bytes = min(
            _RECENT_SCAN_BYTES_MAX,
            max(_RECENT_SCAN_BYTES_MIN, count * 4096),
        )

        tail_text = read_file_tail_lines(
            HISTORY_FILE,
            max_lines=tail_max_lines,
            errors="replace",
            max_scan_bytes=tail_max_scan_bytes,
        )
        entries = _parse_recent_entries(tail_text.splitlines(), count)
        if len(entries) >= count:
            return entries

        # Fallback für sehr lange Einzelzeilen oder ungewöhnlich große JSON-Objekte.
        file_size = HISTORY_FILE.stat().st_size
        if file_size > tail_max_scan_bytes:
            full_text = HISTORY_FILE.read_text(encoding="utf-8")
            return _parse_recent_entries(full_text.splitlines(), count)

        return entries

    except Exception as e:
        logger.warning(f"Failed to read history: {e}")
        return []


def _parse_recent_entries(lines: list[str], count: int) -> list[dict]:
    """Parst JSONL-Zeilen rückwärts und liefert max. ``count`` gültige Einträge."""
    entries: list[dict] = []
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
        if len(entries) >= count:
            break
    return entries


def clear_history() -> bool:
    """Löscht die gesamte Historie.

    Returns:
        True bei Erfolg, False bei Fehler
    """
    try:
        if HISTORY_FILE.exists():
            HISTORY_FILE.unlink()
        logger.info("History cleared")
        return True
    except Exception as e:
        logger.warning(f"Failed to clear history: {e}")
        return False
