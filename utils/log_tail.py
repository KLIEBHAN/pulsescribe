"""Efficient tail helpers for large text/log files."""

from __future__ import annotations

import os
from pathlib import Path
from typing import BinaryIO

from utils.file_signatures import build_file_signature

_TAIL_CHUNK_SIZE = 8192
_SCROLL_BOTTOM_TOLERANCE = 10


def _open_tail_handle(path: Path) -> tuple[BinaryIO | None, int]:
    """Open a file once for tail helpers and return its size."""
    try:
        handle = path.open("rb")
    except OSError:
        return None, 0

    try:
        handle.seek(0, 2)
        return handle, handle.tell()
    except OSError:
        handle.close()
        return None, 0


def _read_tail_bytes_from_open_handle(
    handle: BinaryIO,
    *,
    file_size: int,
    target_newlines: int | None = None,
    max_bytes: int | None = None,
) -> tuple[bytes, bool]:
    """Read bytes from the end of an already opened file handle."""
    if max_bytes is not None and max_bytes <= 0:
        return b"", False
    if file_size <= 0:
        return b"", False

    position = file_size
    chunks: list[bytes] = []
    collected_bytes = 0
    collected_newlines = 0
    truncated_from_start = False

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
                truncated_from_start = position > 0
                break

        if max_bytes is not None and collected_bytes >= max_bytes:
            truncated_from_start = position > 0
            break

    data = b"".join(reversed(chunks))
    if max_bytes is not None and len(data) > max_bytes:
        data = data[-max_bytes:]
        truncated_from_start = True
    return data, truncated_from_start


def _read_tail_bytes(
    path: Path,
    *,
    target_newlines: int | None = None,
    max_bytes: int | None = None,
) -> tuple[bytes, bool]:
    """Read bytes from the end of a file until constraints are satisfied."""
    handle, file_size = _open_tail_handle(path)
    if handle is None:
        return b"", False

    with handle:
        return _read_tail_bytes_from_open_handle(
            handle,
            file_size=file_size,
            target_newlines=target_newlines,
            max_bytes=max_bytes,
        )


def _truncate_visible_tail(
    text: str,
    *,
    max_chars: int,
    truncated_prefix: str,
    force_prefix: bool,
) -> str:
    """Apply the shared tail-budget/prefix rules for file and merge helpers."""
    if max_chars <= 0:
        return ""

    prefix = truncated_prefix or ""
    if not force_prefix and len(text) <= max_chars:
        return text

    if not prefix or len(prefix) >= max_chars:
        return text[-max_chars:]

    suffix_chars = max_chars - len(prefix)
    if len(text) <= suffix_chars:
        return f"{prefix}{text}" if force_prefix else text
    return f"{prefix}{text[-suffix_chars:]}"


def read_file_tail_text(
    path: Path,
    *,
    max_chars: int,
    encoding: str = "utf-8",
    errors: str = "replace",
    truncated_prefix: str = "... (truncated)\n\n",
) -> str:
    """Return at most the last ``max_chars`` characters from a text file."""
    if max_chars <= 0:
        return ""

    # Worst-case UTF-8 expansion is 4 bytes per character.
    max_bytes = max_chars * 4 + _TAIL_CHUNK_SIZE
    raw, _truncated_from_start = _read_tail_bytes(path, max_bytes=max_bytes)
    text = raw.decode(encoding, errors=errors)
    return _truncate_visible_tail(
        text,
        max_chars=max_chars,
        truncated_prefix=truncated_prefix,
        force_prefix=len(text) > max_chars,
    )


def read_file_tail_text_with_signature(
    path: Path,
    *,
    max_chars: int,
    encoding: str = "utf-8",
    errors: str = "replace",
    truncated_prefix: str = "... (truncated)\n\n",
) -> tuple[str, tuple[int, int] | None]:
    """Return tail text together with one matching file signature.

    This avoids a second ``stat()`` call in UI refresh paths that need both the
    rendered tail text and a change-detection signature for the same file read.
    """
    if max_chars <= 0:
        return "", None

    handle, file_size = _open_tail_handle(path)
    if handle is None:
        return "", None

    with handle:
        try:
            stat_result = os.fstat(handle.fileno())
            signature = (int(stat_result.st_mtime_ns), int(stat_result.st_size))
        except (AttributeError, OSError):
            signature = None

        max_bytes = max_chars * 4 + _TAIL_CHUNK_SIZE
        raw, _truncated_from_start = _read_tail_bytes_from_open_handle(
            handle,
            file_size=file_size,
            max_bytes=max_bytes,
        )

    text = raw.decode(encoding, errors=errors)
    rendered_text = _truncate_visible_tail(
        text,
        max_chars=max_chars,
        truncated_prefix=truncated_prefix,
        force_prefix=len(text) > max_chars,
    )
    return rendered_text, signature


def read_file_tail_lines(
    path: Path,
    *,
    max_lines: int,
    encoding: str = "utf-8",
    errors: str = "replace",
    max_scan_bytes: int = 512_000,
) -> str:
    """Return the last ``max_lines`` lines from a text file."""
    if max_lines <= 0:
        return ""

    raw, truncated_from_start = _read_tail_bytes(
        path,
        target_newlines=max_lines,
        max_bytes=max_scan_bytes,
    )
    text = raw.decode(encoding, errors=errors)
    lines = text.splitlines()
    if truncated_from_start and raw[:1] not in (b"\n", b"\r") and lines:
        lines = lines[1:]
    if not lines:
        return ""
    return "\n".join(lines[-max_lines:])


def read_file_text_from_offset(
    path: Path,
    *,
    start_offset: int,
    encoding: str = "utf-8",
    errors: str = "replace",
    max_bytes: int | None = None,
) -> str:
    """Return text appended after ``start_offset`` or ``""`` when unsuitable.

    ``max_bytes`` acts as a safety budget for incremental UI refresh paths. If the
    appended region exceeds the budget, callers should fall back to a normal tail
    reload instead of trying to stream a large delta into the widget.
    """
    if start_offset < 0:
        return ""
    if max_bytes is not None and max_bytes <= 0:
        return ""

    handle, file_size = _open_tail_handle(path)
    if handle is None:
        return ""

    with handle:
        if start_offset >= file_size:
            return ""

        read_size = file_size - start_offset
        if max_bytes is not None and read_size > max_bytes:
            return ""

        handle.seek(start_offset)
        return handle.read(read_size).decode(encoding, errors=errors)


def merge_tail_lines(previous_text: str, appended_text: str, *, max_lines: int) -> str:
    """Merge appended text into a fixed line window."""
    if max_lines <= 0:
        return ""

    combined = f"{previous_text}{appended_text}"
    lines = combined.splitlines()
    if not lines:
        return ""
    return "\n".join(lines[-max_lines:])


def merge_tail_text(
    previous_text: str,
    appended_text: str,
    *,
    max_chars: int,
    truncated_prefix: str = "... (truncated)\n\n",
) -> str:
    """Merge appended text into a fixed-size tail buffer without duplicating prefixes."""
    if max_chars <= 0:
        return ""

    prefix = truncated_prefix or ""
    was_truncated = bool(prefix and previous_text.startswith(prefix))
    visible_previous_text = previous_text[len(prefix) :] if was_truncated else previous_text
    combined = f"{visible_previous_text}{appended_text}"
    return _truncate_visible_tail(
        combined,
        max_chars=max_chars,
        truncated_prefix=prefix,
        force_prefix=was_truncated or len(combined) > max_chars,
    )


def get_file_signature(path: Path) -> tuple[int, int] | None:
    """Return ``(mtime_ns, size)`` for change detection, or ``None`` if unavailable."""
    try:
        mtime_ns, size, _ctime_ns = build_file_signature(path)
    except (FileNotFoundError, OSError):
        return None

    return mtime_ns, size


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
    is_window_visible: bool = True,
    allow_transcripts: bool = False,
) -> bool:
    """Auto-refresh only when enabled and the selected logs view is visible."""
    visible_indexes = {0}
    if allow_transcripts:
        visible_indexes.add(1)
    return (
        enabled
        and is_logs_tab_active
        and logs_view_index in visible_indexes
        and is_window_visible
    )
