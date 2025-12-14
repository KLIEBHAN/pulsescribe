"""Helpers for reading environment variables.

We use `.env` files (python-dotenv) plus runtime `os.environ` overrides.
These helpers standardize parsing and avoid duplicated ad-hoc logic across modules.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger("pulsescribe")

_TRUE_VALUES = {"1", "true", "yes", "on"}
_FALSE_VALUES = {"0", "false", "no", "off"}


def parse_bool(value: str | None) -> bool | None:
    """Parses common boolean string values.

    Returns:
        - True/False when recognized
        - None when value is None or unrecognized
    """
    if value is None:
        return None
    normalized = value.strip().lower()
    if normalized in _TRUE_VALUES:
        return True
    if normalized in _FALSE_VALUES:
        return False
    return None


def get_env_bool(name: str) -> bool | None:
    """Returns bool from env or None if unset/invalid (with warning)."""
    raw = os.getenv(name)
    if raw is None:
        return None
    parsed = parse_bool(raw)
    if parsed is None:
        logger.warning(f"Ungültiger {name}={raw!r}, ignoriere")
    return parsed


def get_env_bool_default(name: str, default: bool) -> bool:
    """Returns bool from env with a default when unset/invalid."""
    parsed = get_env_bool(name)
    return default if parsed is None else parsed


def get_env_int(name: str) -> int | None:
    """Returns int from env or None if unset/invalid (with warning)."""
    raw = os.getenv(name)
    if raw is None:
        return None
    try:
        return int(raw.strip())
    except ValueError:
        logger.warning(f"Ungültiger {name}={raw!r}, ignoriere")
        return None


__all__ = [
    "get_env_bool",
    "get_env_bool_default",
    "get_env_int",
    "parse_bool",
]
