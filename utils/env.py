"""Helpers for reading and loading environment variables.

We use `.env` files (python-dotenv) plus runtime `os.environ` overrides.
These helpers standardize parsing and avoid duplicated ad-hoc logic across modules.

Precedence for load_environment (default `override_existing=False`):
1) Process environment (`os.environ`)
2) User config `.env` (`~/.pulsescribe/.env`)
3) Local project `.env` (current working directory)
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger("pulsescribe")

_TRUE_VALUES = {"1", "true", "yes", "on"}
_FALSE_VALUES = {"0", "false", "no", "off"}
_loaded_env_values: dict[str, str] = {}


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


def load_environment(*, override_existing: bool = False) -> None:
    """Loads `.env` values into `os.environ` if python-dotenv is available.

    On reload (`override_existing=True`), `.env` values override existing env vars,
    while user config still overrides the local project `.env`.
    """
    global _loaded_env_values

    try:
        from dotenv import dotenv_values  # type: ignore[import-not-found]
    except Exception:
        return

    from config import USER_CONFIG_DIR

    local_env = Path(".env")
    user_env = USER_CONFIG_DIR / ".env"

    merged: dict[str, str] = {}
    # Local first, then user (user wins).
    env_paths = [local_env, user_env]
    for env_path in env_paths:
        if not env_path.exists():
            continue
        for key, value in dotenv_values(env_path).items():
            if value is None:
                continue
            merged[str(key)] = str(value)

    if override_existing:
        # Entferne nur Keys, die zuvor von load_environment gesetzt wurden und
        # jetzt nicht mehr in den geladenen .env-Dateien vorkommen.
        for key, value in list(_loaded_env_values.items()):
            if key in merged:
                continue
            if os.environ.get(key) == value:
                del os.environ[key]
        _loaded_env_values = {}

    for key, value in merged.items():
        if override_existing or key not in os.environ:
            os.environ[key] = value
            _loaded_env_values[key] = value


__all__ = [
    "get_env_bool",
    "get_env_bool_default",
    "get_env_int",
    "load_environment",
    "parse_bool",
]
