"""Transkript-Historie für PulseScribe.

Speichert transkribierte Texte in ~/.pulsescribe/history.jsonl.
Jede Zeile ist ein JSON-Objekt mit Timestamp und Text.
"""

import json
import logging
from collections.abc import Sequence
from datetime import datetime

from config import USER_CONFIG_DIR
from utils.log_tail import read_file_tail_lines
from utils.preferences import _write_text_atomic
from utils.timing import redacted_text_summary

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

        entry: dict[str, object] = {
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

        # Ein großer neuer Eintrag kann die Datei erst nach dem Append über das
        # Limit schieben. Direkt danach rotieren, damit die History nicht bis
        # zum nächsten Save unnötig groß bleibt.
        _rotate_if_needed()

        logger.debug(
            "Transcript saved to history: %s", redacted_text_summary(text.strip())
        )
        return True

    except Exception as e:
        logger.warning(f"Failed to save transcript to history: {e}")
        return False


def _rotate_if_needed() -> None:
    """Rotiert die Historie wenn sie zu groß wird."""
    if not HISTORY_FILE.exists():
        return

    try:
        max_size_bytes = max(1, int(MAX_HISTORY_SIZE_MB * 1024 * 1024))
        if HISTORY_FILE.stat().st_size < max_size_bytes:
            return

        lines = HISTORY_FILE.read_text(encoding="utf-8").splitlines()
        kept_lines = _select_recent_lines_within_bytes(lines, max_size_bytes)
        if kept_lines:
            _write_text_atomic(
                HISTORY_FILE, "\n".join(kept_lines) + "\n"
            )
            logger.info(
                f"History rotated: kept {len(kept_lines)} of {len(lines)} entries"
            )

    except Exception as e:
        logger.warning(f"History rotation failed: {e}")


def _select_recent_lines_within_bytes(
    lines: list[str], max_size_bytes: int
) -> list[str]:
    """Keep the newest JSONL lines that still fit inside the size budget.

    The newest entry is always preserved, even if a single line already exceeds
    the configured limit. This avoids corrupt partial entries while still
    shrinking multi-entry histories as much as possible.
    """
    if not lines:
        return []

    kept_reversed: list[str] = []
    total_bytes = 0

    for line in reversed(lines):
        line_size = len(line.encode("utf-8")) + 1  # JSONL newline
        if kept_reversed and total_bytes + line_size > max_size_bytes:
            break
        kept_reversed.append(line)
        total_bytes += line_size

    return list(reversed(kept_reversed))


def get_recent_transcripts(count: int = 10) -> list[dict[str, object]]:
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


def _parse_recent_entries(lines: list[str], count: int) -> list[dict[str, object]]:
    """Parst JSONL-Zeilen rückwärts und liefert max. ``count`` gültige Einträge."""
    entries: list[dict[str, object]] = []
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(entry, dict):
            continue
        entries.append(entry)
        if len(entries) >= count:
            break
    return entries


def _format_display_text(text: object) -> str:
    """Format transcript text for list-style viewers while preserving paragraphs."""
    normalized = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.splitlines()
    if not lines:
        return ""
    if len(lines) == 1:
        return lines[0]
    continuation_lines = [
        f"    {line}" if line else "    "
        for line in lines[1:]
    ]
    return "\n".join([lines[0], *continuation_lines])


def format_transcripts_for_display(entries: Sequence[object]) -> str:
    """Format transcript entries for the Windows transcripts viewer."""
    valid_entries = [entry for entry in entries if isinstance(entry, dict)]
    if not valid_entries:
        return "No transcripts yet."

    lines: list[str] = []
    for entry in reversed(valid_entries):  # oldest first for readable history flow
        ts = str(entry.get("timestamp", ""))[:19].replace("T", " ")
        text = _format_display_text(entry.get("text", ""))
        mode = str(entry.get("mode", ""))
        refined = "✨" if entry.get("refined") else ""

        line = f"[{ts}] {refined}{text}"
        if mode:
            line = f"[{ts}] ({mode}) {refined}{text}"
        lines.append(line)

    return "\n\n".join(lines)


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
