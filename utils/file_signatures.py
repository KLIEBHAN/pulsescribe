"""Helpers for stable file signatures used by lightweight caches."""

from __future__ import annotations

from pathlib import Path
from typing import Any

FileSignature = tuple[int, int, int]


def _ns_timestamp(stat_result: Any, ns_attr: str, seconds_attr: str) -> int:
    """Return a nanosecond timestamp with a seconds-based fallback."""
    return int(
        getattr(
            stat_result,
            ns_attr,
            int(getattr(stat_result, seconds_attr, 0.0) * 1_000_000_000),
        )
    )


def build_file_signature(path: Path) -> FileSignature:
    """Build a stable cache signature from stat metadata.

    Raises:
        FileNotFoundError: When the file does not exist.
        OSError: When the file metadata cannot be read.
    """
    stat_result = path.stat()
    return (
        _ns_timestamp(stat_result, "st_mtime_ns", "st_mtime"),
        int(getattr(stat_result, "st_size", 0)),
        _ns_timestamp(stat_result, "st_ctime_ns", "st_ctime"),
    )


__all__ = ["FileSignature", "build_file_signature"]
