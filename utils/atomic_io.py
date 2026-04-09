"""Shared atomic file-writing helpers."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def write_text_atomic(path: Path, content: str, *, encoding: str = "utf-8") -> None:
    """Write text atomically to avoid truncated config files on crashes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        tmp_path.write_text(content, encoding=encoding)
        tmp_path.replace(path)
    except Exception:
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise


__all__ = ["write_text_atomic"]
