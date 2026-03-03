"""Efficient tail helpers for large text/log files."""

from __future__ import annotations

from pathlib import Path

_TAIL_CHUNK_SIZE = 8192
_SCROLL_BOTTOM_TOLERANCE = 10


def _read_tail_bytes(
    path: Path,
    *,
    target_newlines: int | None = None,
    max_bytes: int | None = None,
) -> bytes:
    """Read bytes from the end of a file until constraints are satisfied."""
    if max_bytes is not None and max_bytes <= 0:
        return b""

    with path.open("rb") as handle:
        handle.seek(0, 2)
        position = handle.tell()
        if position <= 0:
            return b""

        chunks: list[bytes] = []
        collected_bytes = 0
        collected_newlines = 0

        while position > 0:
            read_size = min(_TAIL_CHUNK_SIZE, position)
            position -= read_size
            handle.seek(position)
            chunk = handle.read(read_size)
            if not chunk:
                break

            chunks.append(chunk)
            collected_bytes += len(chunk)

            if target_newlines is not None:
                collected_newlines += chunk.count(b"\n")
                if collected_newlines > target_newlines + 1:
                    break

            if max_bytes is not None and collected_bytes >= max_bytes:
                break

    data = b"".join(reversed(chunks))
    if max_bytes is not None and len(data) > max_bytes:
        data = data[-max_bytes:]
    return data


def read_file_tail_text(
    path: Path,
    *,
    max_chars: int,
    encoding: str = "utf-8",
    errors: str = "replace",
    truncated_prefix: str = "... (truncated)\n\n",
) -> str:
    """Return at most the last ``max_chars`` characters from a text file."""
    if max_chars <= 0 or not path.exists():
        return ""

    # Worst-case UTF-8 expansion is 4 bytes per character.
    max_bytes = max_chars * 4 + _TAIL_CHUNK_SIZE
    raw = _read_tail_bytes(path, max_bytes=max_bytes)
    text = raw.decode(encoding, errors=errors)
    if len(text) <= max_chars:
        return text
    return f"{truncated_prefix}{text[-max_chars:]}"


def read_file_tail_lines(
    path: Path,
    *,
    max_lines: int,
    encoding: str = "utf-8",
    errors: str = "replace",
    max_scan_bytes: int = 512_000,
) -> str:
    """Return the last ``max_lines`` lines from a text file."""
    if max_lines <= 0 or not path.exists():
        return ""

    raw = _read_tail_bytes(
        path,
        target_newlines=max_lines,
        max_bytes=max_scan_bytes,
    )
    text = raw.decode(encoding, errors=errors)
    lines = text.splitlines()
    if not lines:
        return ""
    return "\n".join(lines[-max_lines:])


def is_near_bottom(
    scroll_value: int,
    scroll_maximum: int,
    *,
    tolerance: int = _SCROLL_BOTTOM_TOLERANCE,
) -> bool:
    """Return True when a scroll position is at/near the bottom."""
    if scroll_maximum <= 0:
        return True
    threshold = max(0, scroll_maximum - max(0, tolerance))
    return scroll_value >= threshold


def clamp_scroll_value(scroll_value: int, scroll_maximum: int) -> int:
    """Clamp a scroll value into valid range [0, scroll_maximum]."""
    return max(0, min(scroll_value, max(0, scroll_maximum)))


def should_auto_refresh_logs(
    *,
    enabled: bool,
    is_logs_tab_active: bool,
    logs_view_index: int,
) -> bool:
    """Auto-refresh only when enabled and the logs view is visible."""
    return enabled and is_logs_tab_active and logs_view_index == 0
