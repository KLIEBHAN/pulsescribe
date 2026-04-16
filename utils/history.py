"""Transkript-Historie für PulseScribe.

Speichert transkribierte Texte in ~/.pulsescribe/history.jsonl.
Jede Zeile ist ein JSON-Objekt mit Timestamp und Text.
"""

import json
import logging
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime

from config import USER_CONFIG_DIR
from utils.log_tail import read_file_tail_lines, read_file_text_from_offset
from utils.preferences import _write_text_atomic
from utils.timing import redacted_text_summary

HISTORY_FILE = USER_CONFIG_DIR / "history.jsonl"
MAX_HISTORY_SIZE_MB = 10  # Max file size before rotation
_RECENT_SCAN_BYTES_MIN = 128_000
_RECENT_SCAN_BYTES_MAX = 4_000_000
_RECENT_LINES_FACTOR = 3

logger = logging.getLogger(__name__)


def _build_transcript_entry(
    text: str,
    *,
    mode: str | None = None,
    language: str | None = None,
    refined: bool = False,
    app_context: str | None = None,
) -> dict[str, object]:
    """Build one persisted transcript entry while omitting empty optional fields."""
    entry: dict[str, object] = {
        "timestamp": datetime.now().isoformat(),
        "text": text,
    }
    for key, value in (("mode", mode), ("language", language), ("app", app_context)):
        if value:
            entry[key] = value
    if refined:
        entry["refined"] = True
    return entry


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
        refined: Ob LLM-Refine den gespeicherten Text tatsächlich verändert hat
        app_context: Aktive App beim Transkribieren

    Returns:
        True bei Erfolg, False bei Fehler
    """
    clean_text = (text or "").strip()
    if not clean_text:
        return False

    try:
        HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)

        # Check file size and rotate if needed
        _rotate_if_needed()

        entry = _build_transcript_entry(
            clean_text,
            mode=mode,
            language=language,
            refined=refined,
            app_context=app_context,
        )

        with HISTORY_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

        # Ein großer neuer Eintrag kann die Datei erst nach dem Append über das
        # Limit schieben. Direkt danach rotieren, damit die History nicht bis
        # zum nächsten Save unnötig groß bleibt.
        _rotate_if_needed()

        logger.debug("Transcript saved to history: %s", redacted_text_summary(clean_text))
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


def _recent_history_read_limits(count: int) -> tuple[int, int]:
    """Return the shared tail-read limits for recent history lookups."""
    tail_max_lines = max(count * _RECENT_LINES_FACTOR, count + 10)
    tail_max_scan_bytes = min(
        _RECENT_SCAN_BYTES_MAX,
        max(_RECENT_SCAN_BYTES_MIN, count * 4096),
    )
    return tail_max_lines, tail_max_scan_bytes


def _load_recent_transcript_entries(count: int) -> list[dict[str, object]]:
    """Load recent entries with a tail-first strategy and full-read fallback."""
    tail_max_lines, tail_max_scan_bytes = _recent_history_read_limits(count)
    tail_text = read_file_tail_lines(
        HISTORY_FILE,
        max_lines=tail_max_lines,
        errors="replace",
        max_scan_bytes=tail_max_scan_bytes,
    )
    tail_lines = tail_text.splitlines()
    entries = _parse_recent_entries(tail_lines, count)
    if len(entries) >= count:
        return entries

    file_size = HISTORY_FILE.stat().st_size

    # Wenn Tail-Read bereits die ganze Datei abdeckt, ist ein Full-Read unnötig.
    # Sonst kann der Tail entweder per Byte-Limit oder max_lines abgeschnitten
    # worden sein – dann brauchen wir für korrekte History-Einträge einen
    # vollständigen Reload.
    if file_size <= tail_max_scan_bytes and len(tail_lines) < tail_max_lines:
        return entries

    full_text = HISTORY_FILE.read_text(encoding="utf-8")
    return _parse_recent_entries(full_text.splitlines(), count)


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
        return _load_recent_transcript_entries(count)
    except Exception as e:
        logger.warning(f"Failed to read history: {e}")
        return []


def _iter_parsed_transcript_lines(
    lines: Sequence[str], *, reverse: bool = False
) -> Iterator[dict[str, object]]:
    """Yield valid transcript entries while optionally scanning newest-first."""
    source = reversed(lines) if reverse else lines
    for line in source:
        entry = _parse_transcript_line(line)
        if entry is not None:
            yield entry


def _parse_recent_entries(lines: list[str], count: int) -> list[dict[str, object]]:
    """Parst JSONL-Zeilen rückwärts und liefert max. ``count`` gültige Einträge."""
    entries: list[dict[str, object]] = []
    for entry in _iter_parsed_transcript_lines(lines, reverse=True):
        entries.append(entry)
        if len(entries) >= count:
            break
    return entries


def _coerce_transcript_entry(entry: object) -> dict[str, object] | None:
    """Return dictionary-backed transcript entries and ignore legacy payloads."""
    if not isinstance(entry, dict):
        return None
    return entry


def _parse_transcript_line(line: str) -> dict[str, object] | None:
    """Parse a single JSONL transcript entry or return ``None`` for invalid lines."""
    line = line.strip()
    if not line:
        return None
    try:
        entry = json.loads(line)
    except json.JSONDecodeError:
        return None
    return _coerce_transcript_entry(entry)


def read_transcripts_from_offset(
    start_offset: int, *, max_bytes: int | None = None
) -> list[dict[str, object]]:
    """Read newly appended transcript entries from ``history.jsonl`` in file order."""
    if start_offset < 0 or not HISTORY_FILE.exists():
        return []

    appended_text = read_file_text_from_offset(
        HISTORY_FILE,
        start_offset=start_offset,
        encoding="utf-8",
        errors="replace",
        max_bytes=max_bytes,
    )
    if not appended_text:
        return []
    return list(_iter_parsed_transcript_lines(appended_text.splitlines()))


def _iter_valid_transcript_entries(
    entries: Sequence[object],
) -> Iterator[dict[str, object]]:
    """Yield only dictionary-backed transcript entries."""
    for entry in entries:
        coerced_entry = _coerce_transcript_entry(entry)
        if coerced_entry is not None:
            yield coerced_entry


def _limit_ordered_transcript_entries(
    ordered_entries: Sequence[dict[str, object]],
    *,
    newest_first: bool,
    max_entries: int | None = None,
) -> list[dict[str, object]]:
    """Trim already ordered transcript entries without re-validating them."""
    entries = list(ordered_entries)
    if max_entries is None:
        return entries
    if max_entries <= 0:
        return []
    if newest_first:
        return entries[:max_entries]
    return entries[-max_entries:]


def _collect_valid_transcript_entries(
    entries: Sequence[object], *, newest_first: bool, max_entries: int | None = None
) -> list[dict[str, object]]:
    """Filter invalid entries and return them in the requested display order."""
    ordered_entries = list(_iter_valid_transcript_entries(entries))
    if newest_first:
        ordered_entries.reverse()
    return _limit_ordered_transcript_entries(
        ordered_entries,
        newest_first=newest_first,
        max_entries=max_entries,
    )


def _format_timestamp(timestamp: object) -> str:
    """Normalize transcript timestamps for UI display."""
    return str(timestamp or "")[:19].replace("T", " ")


def merge_recent_transcript_entries(
    previous_entries: Sequence[object],
    appended_entries: Sequence[object],
    *,
    max_entries: int,
) -> list[dict[str, object]]:
    """Merge append-only entries into an oldest-first visible transcript window."""
    if max_entries <= 0:
        return []

    previous_valid = list(_iter_valid_transcript_entries(previous_entries))
    appended_valid = list(_iter_valid_transcript_entries(appended_entries))
    visible_entries = (
        previous_valid
        if not appended_valid
        else [*previous_valid, *appended_valid]
    )
    return _limit_ordered_transcript_entries(
        visible_entries,
        newest_first=False,
        max_entries=max_entries,
    )


def _collect_entry_metadata(
    entry: dict[str, object],
    metadata_keys: Sequence[str],
    *,
    strip_values: bool,
) -> list[str]:
    """Collect non-empty stringified metadata values for transcript headers."""
    values: list[str] = []
    for key in metadata_keys:
        value = str(entry.get(key, ""))
        if strip_values:
            value = value.strip()
        if value:
            values.append(value)
    return values


def _format_entry_header(
    entry: dict[str, object],
    *,
    metadata_keys: Sequence[str] = (),
    strip_metadata: bool,
) -> str:
    """Build a normalized transcript header for display and welcome views."""
    ts = _format_timestamp(entry.get("timestamp", ""))
    header = f"[{ts}]"
    metadata_values = _collect_entry_metadata(
        entry,
        metadata_keys,
        strip_values=strip_metadata,
    )
    if metadata_values:
        header += f" ({' '.join(metadata_values)})"
    return header


def _format_transcript_blocks(
    entries: Sequence[object],
    *,
    newest_first: bool,
    formatter: Callable[[dict[str, object]], str],
) -> list[str]:
    """Format ordered transcript entries into non-empty blocks."""
    ordered_entries = _collect_valid_transcript_entries(
        entries,
        newest_first=newest_first,
    )
    return [block for block in (formatter(entry) for entry in ordered_entries) if block]


@dataclass(frozen=True)
class _TranscriptFormatSpec:
    """Formatting rules for one transcript presentation variant."""

    metadata_keys: tuple[str, ...]
    strip_metadata: bool
    body_renderer: Callable[[dict[str, object]], str]
    separator: str
    keep_header_when_body_empty: bool = False
    include_separator_when_body_empty: bool = False


def _join_transcript_blocks(blocks: Sequence[str], *, empty_message: str) -> str:
    """Join preformatted transcript blocks or return a caller-specific empty state."""
    if not blocks:
        return empty_message
    return "\n\n".join(block for block in blocks if block)


def _prepare_formatted_transcript_entry(
    entry: object,
    *,
    metadata_keys: Sequence[str],
    strip_metadata: bool,
) -> tuple[dict[str, object], str] | None:
    """Coerce one entry and compute its header for view-specific formatters."""
    transcript_entry = _coerce_transcript_entry(entry)
    if transcript_entry is None:
        return None

    header = _format_entry_header(
        transcript_entry,
        metadata_keys=metadata_keys,
        strip_metadata=strip_metadata,
    )
    return transcript_entry, header


def _render_display_body(entry: dict[str, object]) -> str:
    """Render the display-view body, including the refine marker when present."""
    text = _format_display_text(entry.get("text", ""))
    refined = "✨" if entry.get("refined") else ""
    return f"{refined}{text}"


_DISPLAY_TRANSCRIPT_FORMAT = _TranscriptFormatSpec(
    metadata_keys=("mode",),
    strip_metadata=False,
    body_renderer=_render_display_body,
    separator=" ",
    include_separator_when_body_empty=True,
)


def _render_welcome_body(entry: dict[str, object]) -> str:
    """Render the welcome-view body while collapsing whitespace-only text."""
    return str(entry.get("text", "")).strip()


_WELCOME_TRANSCRIPT_FORMAT = _TranscriptFormatSpec(
    metadata_keys=("mode", "language"),
    strip_metadata=True,
    body_renderer=_render_welcome_body,
    separator="\n",
    keep_header_when_body_empty=True,
)


def _format_transcript_entry(
    entry: object,
    *,
    spec: _TranscriptFormatSpec,
) -> str:
    """Format one transcript entry using a reusable view-specific specification."""
    prepared = _prepare_formatted_transcript_entry(
        entry,
        metadata_keys=spec.metadata_keys,
        strip_metadata=spec.strip_metadata,
    )
    if prepared is None:
        return ""

    transcript_entry, header = prepared
    body = spec.body_renderer(transcript_entry)
    if body:
        return f"{header}{spec.separator}{body}"
    if spec.keep_header_when_body_empty:
        return header
    if spec.include_separator_when_body_empty:
        return f"{header}{spec.separator}"
    return header


def format_transcript_entry_for_display(entry: object) -> str:
    """Format a single transcript entry for the Windows transcripts viewer."""
    return _format_transcript_entry(entry, spec=_DISPLAY_TRANSCRIPT_FORMAT)


def format_transcript_entries_for_display(
    entries: Sequence[object], *, newest_first: bool = True
) -> list[str]:
    """Format transcript entries into display-view blocks without joining them."""
    return _format_transcript_blocks(
        entries,
        newest_first=newest_first,
        formatter=format_transcript_entry_for_display,
    )


def format_transcripts_for_display(
    entries: Sequence[object], *, newest_first: bool = True
) -> str:
    """Format transcript entries for the Windows transcripts viewer."""
    blocks = format_transcript_entries_for_display(
        entries,
        newest_first=newest_first,
    )
    return _join_transcript_blocks(blocks, empty_message="No transcripts yet.")


def format_transcript_entry_for_welcome(entry: object) -> str:
    """Format a single transcript entry for the macOS welcome/history view."""
    return _format_transcript_entry(entry, spec=_WELCOME_TRANSCRIPT_FORMAT)


def format_transcript_entries_for_welcome(
    entries: Sequence[object], *, newest_first: bool = True
) -> list[str]:
    """Format transcript entries into welcome-view blocks without joining them."""
    return _format_transcript_blocks(
        entries,
        newest_first=newest_first,
        formatter=format_transcript_entry_for_welcome,
    )


def format_transcripts_for_welcome(
    entries: Sequence[object], *, newest_first: bool = True
) -> str:
    """Format transcript entries for the macOS welcome/history viewer."""
    blocks = format_transcript_entries_for_welcome(
        entries,
        newest_first=newest_first,
    )
    return _join_transcript_blocks(
        blocks,
        empty_message="No transcriptions yet.\n\nYour transcribed texts will appear here.",
    )


def _format_display_text(text: object) -> str:
    """Format transcript text for list-style viewers while preserving paragraphs."""
    normalized = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.splitlines()
    if not lines:
        return ""
    if len(lines) == 1:
        return lines[0]
    continuation_lines = [f"    {line}" if line else "    " for line in lines[1:]]
    return "\n".join([lines[0], *continuation_lines])


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
