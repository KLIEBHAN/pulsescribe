"""Shared .env loading.

Both `transcribe.py` (CLI) and `whisper_daemon.py` (macOS app/daemon) rely on
`.env` files. Historically they implemented slightly different loaders, which
made behavior drift over time.

Precedence (default `override_existing=False`):
1) Process environment (`os.environ`)
2) User config `.env` (`~/.whisper_go/.env`)
3) Local project `.env` (current working directory)

On reload (`override_existing=True`), `.env` values override existing env vars,
while user config still overrides the local project `.env`.
"""

from __future__ import annotations

import os
from pathlib import Path


def load_environment(*, override_existing: bool = False) -> None:
    """Loads `.env` values into `os.environ` if python-dotenv is available."""
    try:
        from dotenv import dotenv_values  # type: ignore[import-not-found]
    except Exception:
        return

    from config import USER_CONFIG_DIR

    local_env = Path(".env")
    user_env = USER_CONFIG_DIR / ".env"

    merged: dict[str, str] = {}
    # Local first, then user (user overrides local).
    for env_path in (local_env, user_env):
        if not env_path.exists():
            continue
        for key, value in dotenv_values(env_path).items():
            if value is None:
                continue
            merged[str(key)] = str(value)

    for key, value in merged.items():
        if override_existing or key not in os.environ:
            os.environ[key] = value


__all__ = ["load_environment"]

