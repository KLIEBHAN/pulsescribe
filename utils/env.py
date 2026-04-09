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
from collections.abc import Iterator, Mapping
from io import StringIO
from pathlib import Path

logger = logging.getLogger("pulsescribe")

_TRUE_VALUES = {"1", "true", "yes", "on"}
_FALSE_VALUES = {"0", "false", "no", "off"}
_loaded_env_values: dict[str, str] = {}


def _remember_loaded_env_values(values: dict[str, str]) -> None:
    """Track env values that were injected from `.env` files.

    This is used by import-time preload code in `config.py` so a later
    `load_environment(override_existing=True)` can remove deleted keys again.
    """
    if not values:
        return
    _loaded_env_values.update(values)


def _get_local_env_path() -> Path:
    """Return the project-local `.env` path independent of the current cwd."""
    return Path(__file__).resolve().parent.parent / ".env"


def _iter_normalized_dotenv_items(
    raw_values: Mapping[object, object | None],
    *,
    strip_values: bool = False,
    include_none_as_empty: bool = False,
) -> Iterator[tuple[str, str]]:
    """Yield normalized ``KEY=value`` pairs from dotenv-style mappings."""
    for key, value in raw_values.items():
        normalized_key = str(key or "").strip()
        if not normalized_key:
            continue
        if value is None:
            if include_none_as_empty:
                yield normalized_key, ""
            continue
        normalized_value = str(value)
        if strip_values:
            normalized_value = normalized_value.strip()
        yield normalized_key, normalized_value


def _first_normalized_dotenv_item(
    raw_values: Mapping[object, object | None],
    *,
    strip_values: bool = False,
    include_none_as_empty: bool = False,
) -> tuple[str | None, str | None]:
    """Return the first normalized item from a dotenv-style mapping."""
    for key, value in _iter_normalized_dotenv_items(
        raw_values,
        strip_values=strip_values,
        include_none_as_empty=include_none_as_empty,
    ):
        return key, value
    return None, None


def parse_env_line(raw_line: str) -> tuple[str | None, str | None]:
    """Parse one ``.env`` line with minimal quote/comment handling."""
    line = raw_line.strip()
    if not line or line.startswith("#"):
        return None, None

    if line.startswith("export "):
        line = line[7:].lstrip()
    if "=" not in line:
        return None, None

    key, value = line.split("=", 1)
    key = key.strip()
    if not key:
        return None, None

    value = value.strip()
    if not value:
        return key, ""

    parsed: list[str] = []
    in_single = False
    in_double = False

    for index, char in enumerate(value):
        if char == "'" and not in_double:
            in_single = not in_single
            continue
        if char == '"' and not in_single:
            in_double = not in_double
            continue
        if char == "#" and not in_single and not in_double:
            prev = value[index - 1] if index > 0 else ""
            if not prev or prev.isspace():
                break
        parsed.append(char)

    return key, "".join(parsed).strip()


def parse_env_line_with_dotenv(raw_line: str) -> tuple[str | None, str | None]:
    """Parse one `.env` line with python-dotenv when available.

    This is primarily used by mutation code that needs to recognize existing
    assignments written with `export`, quotes, comments, or extra spacing before
    rewriting them into canonical `KEY=value` lines.
    """
    try:
        from dotenv import dotenv_values  # type: ignore[import-not-found]
    except Exception:
        return parse_env_line(raw_line)

    try:
        parsed = dotenv_values(stream=StringIO(f"{raw_line}\n"))
    except Exception:
        return parse_env_line(raw_line)

    return _first_normalized_dotenv_item(
        parsed,
        strip_values=True,
        include_none_as_empty=True,
    )


def _parse_env_line(raw_line: str) -> tuple[str | None, str | None]:
    """Backward-compatible alias for existing internal callers/tests."""
    return parse_env_line(raw_line)


def read_env_file_values(
    path: Path,
    *,
    encoding: str = "utf-8",
    errors: str = "replace",
    first_wins: bool = False,
) -> dict[str, str]:
    """Read one `.env` file via the lightweight line parser.

    Args:
        path: `.env` file to parse.
        encoding: Text encoding for the file read.
        errors: Error strategy forwarded to ``Path.read_text``.
        first_wins: Keep the first assignment for duplicate keys instead of the last.
    """
    values: dict[str, str] = {}
    try:
        raw_lines = path.read_text(encoding=encoding, errors=errors).splitlines()
    except OSError:
        return {}

    for raw_line in raw_lines:
        key, value = parse_env_line(raw_line)
        if key is None or value is None:
            continue
        if first_wins and key in values:
            continue
        values[key] = value
    return values


def _fallback_dotenv_values(path: Path) -> dict[str, str]:
    """Best-effort `.env` parser when python-dotenv is unavailable."""
    return read_env_file_values(path)


def _read_dotenv_values(path: Path) -> dict[str, str]:
    """Read one `.env` file and return normalized string values."""
    try:
        from dotenv import dotenv_values  # type: ignore[import-not-found]
    except Exception:
        return _fallback_dotenv_values(path)

    try:
        raw_values = dotenv_values(path)
    except Exception:
        return {}

    return dict(_iter_normalized_dotenv_items(raw_values))


def collect_env_values(
    *,
    user_config_dir: Path | None = None,
    local_env_path: Path | None = None,
) -> dict[str, str]:
    """Collect `.env` values with precedence `local < user`."""
    local_env = local_env_path or _get_local_env_path()
    user_env = (user_config_dir or (Path.home() / ".pulsescribe")) / ".env"

    merged: dict[str, str] = {}
    for env_path in (local_env, user_env):
        if not env_path.exists():
            continue
        merged.update(_read_dotenv_values(env_path))
    return merged


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

    from config import USER_CONFIG_DIR

    merged = collect_env_values(user_config_dir=USER_CONFIG_DIR)

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
    "parse_env_line",
    "parse_env_line_with_dotenv",
    "parse_bool",
    "read_env_file_values",
]
