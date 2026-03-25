from __future__ import annotations

import sys
from pathlib import Path


def _development_base_path() -> Path:
    """Return the project root for source checkouts."""
    return Path(__file__).resolve().parent.parent


def get_resource_path(relative_path: str) -> str:
    """Return an absolute path for bundled or source-tree resources."""
    base_path = (
        Path(sys._MEIPASS)  # type: ignore[attr-defined]
        if hasattr(sys, "_MEIPASS")
        else _development_base_path()
    )
    return str((base_path / relative_path).resolve(strict=False))
