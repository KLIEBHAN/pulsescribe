"""Export language-neutral golden fixtures from the Python implementation.

Both pytest (`tests/test_golden_fixtures.py`) and Rust (`crates/core/tests/`)
validate against the exported JSON files so behavior cannot drift silently
during the Rust migration.

Usage:
    python tests/golden/export_fixtures.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from utils.env import parse_bool  # noqa: E402
from utils.hotkey import hotkeys_conflict as macos_hotkeys_conflict  # noqa: E402
from utils.hotkey import parse_hotkey as macos_parse_hotkey  # noqa: E402
from utils.hotkey_windows import (  # noqa: E402
    hotkeys_conflict as windows_hotkeys_conflict,
    normalize_windows_hotkey,
)
from utils.state import AppState, DaemonErrorCode  # noqa: E402

GOLDEN_FILE = Path(__file__).resolve().parent / "core_contracts.json"

_PARSE_BOOL_INPUTS: tuple[str | None, ...] = (
    None,
    "",
    "   ",
    "1",
    "true",
    "TRUE",
    " yes ",
    "On",
    "0",
    "false",
    "FALSE",
    " no ",
    "Off",
    "maybe",
    "2",
)

_MACOS_HOTKEY_INPUTS: tuple[str, ...] = (
    "fn",
    "f19",
    "F1",
    "capslock",
    "caps_lock",
    "space",
    "cmd+shift+r",
    "command+option+space",
    "ctrl+alt+return",
    "  CMD + SHIFT + F12  ",
    "",
    "cmd+unknownkey",
    "super+a",
)

_MACOS_CONFLICT_PAIRS: tuple[tuple[str | None, str | None], ...] = (
    ("fn", "fn"),
    ("space", "cmd+space"),
    ("cmd+space", "cmd+shift+space"),
    ("cmd+r", "ctrl+r"),
    ("f19", "fn"),
    ("", "fn"),
    ("invalid_key", "fn"),
)

_WINDOWS_HOTKEY_INPUTS: tuple[str | None, ...] = (
    None,
    "",
    "   ",
    "ctrl+win",
    "WIN+CTRL",
    "cmd+alt+r",
    "option+shift+return",
    "ctrl+alt+r",
    "ctrl+ctrl+r",
    "f24",
    "f25",
    "ctrl++r",
    "ctrl+a+b",
    "ctrl+nonexistent",
)

_WINDOWS_CONFLICT_PAIRS: tuple[tuple[str | None, str | None], ...] = (
    ("ctrl+win", "ctrl+win"),
    ("ctrl+win", "ctrl+win+r"),
    ("ctrl+alt+r", "ctrl+win"),
    ("cmd+shift", "win+shift+a"),
    ("", "ctrl+win"),
    ("ctrl+nonexistent", "ctrl+win"),
)


def _export_macos_hotkey(raw: str) -> dict[str, Any]:
    try:
        virtual_key, modifier_mask = macos_parse_hotkey(raw)
    except ValueError as err:
        return {"input": raw, "ok": False, "error": str(err)}
    return {
        "input": raw,
        "ok": True,
        "virtual_key": virtual_key,
        "modifier_mask": modifier_mask,
    }


def _export_windows_hotkey(raw: str | None) -> dict[str, Any]:
    normalized, error = normalize_windows_hotkey(raw)
    return {"input": raw, "normalized": normalized, "error": error}


def build_core_contracts() -> dict[str, Any]:
    """Build deterministic core contract fixtures from the live Python code."""
    return {
        "schema_version": 1,
        "app_states": [state.value for state in AppState],
        "daemon_error_codes": [code.value for code in DaemonErrorCode],
        "parse_bool": [
            {"input": value, "expected": parse_bool(value)}
            for value in _PARSE_BOOL_INPUTS
        ],
        "macos_parse_hotkey": [_export_macos_hotkey(raw) for raw in _MACOS_HOTKEY_INPUTS],
        "macos_hotkeys_conflict": [
            {"a": a, "b": b, "conflict": macos_hotkeys_conflict(a, b)}
            for a, b in _MACOS_CONFLICT_PAIRS
        ],
        "windows_normalize_hotkey": [
            _export_windows_hotkey(raw) for raw in _WINDOWS_HOTKEY_INPUTS
        ],
        "windows_hotkeys_conflict": [
            {"a": a, "b": b, "conflict": windows_hotkeys_conflict(a, b)}
            for a, b in _WINDOWS_CONFLICT_PAIRS
        ],
    }


def render_core_contracts_json() -> str:
    return json.dumps(build_core_contracts(), indent=2, ensure_ascii=False) + "\n"


def main() -> None:
    GOLDEN_FILE.write_text(render_core_contracts_json(), encoding="utf-8")
    print(f"Wrote {GOLDEN_FILE.relative_to(PROJECT_ROOT)}")


if __name__ == "__main__":
    main()
