"""Export language-neutral golden fixtures from the Python implementation.

Both pytest (`tests/test_golden_fixtures.py`) and Rust (`crates/core/tests/`)
validate against the exported JSON files so behavior cannot drift silently
during the Rust migration. Only contracts that already have a Rust counterpart
are exported here; later Phase 1 PRs extend this file alongside their Rust code.

Usage:
    python tests/golden/export_fixtures.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from utils.env import _read_dotenv_values, parse_bool, parse_env_line  # noqa: E402
from utils.onboarding import (  # noqa: E402
    ONBOARDING_ORDER,
    OnboardingChoice,
    next_step,
    prev_step,
    step_index,
    total_steps,
)
from utils.preferences import (  # noqa: E402
    _apply_env_updates_to_lines,
    _format_env_assignment,
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

_PARSE_ENV_LINE_INPUTS: tuple[str, ...] = (
    "",
    "   # comment",
    "NO_EQUALS",
    "=missing_key",
    "KEY=value",
    "KEY=",
    "  export MODE = local  ",
    'export MODE = "local" # keep',
    'export MODE = "" # keep',
    "MIC='Bob\\'s #1 \"USB\" \\\\Mic' # inline",
    "HASH_NO_SPACE=abc#def",
    "HASH_WITH_SPACE=abc # trailing comment",
    "QUOTED_HASH=\"abc # kept\" # trailing",
)

_FORMAT_ENV_INPUTS: tuple[tuple[str, str], ...] = (
    ("PULSESCRIBE_MODE", "local"),
    ("PULSESCRIBE_MODE", ""),
    ("PULSESCRIBE_INPUT_DEVICE", "MacBook Pro Microphone"),
    ("PULSESCRIBE_INPUT_DEVICE", "Bob's #1 \"USB\" \\Mic"),
)

_ENV_UPDATE_CASES: tuple[tuple[str, list[str], dict[str, str | None], bool], ...] = (
    (
        "single-unchanged-preserves-format-and-dup",
        ['export MODE = "local" # keep', "MODE=legacy"],
        {"MODE": "local"},
        True,
    ),
    (
        "batch-unchanged-canonicalizes-and-dedupes",
        ['export MODE = "local" # keep', "MODE=legacy"],
        {"MODE": "local"},
        False,
    ),
    (
        "single-changed-updates-first-keeps-dup",
        ['export MODE = "old"', "# keep", "MODE=legacy"],
        {"MODE": "local"},
        True,
    ),
    (
        "batch-changed-collapses-dup",
        ['export MODE = "old"', "# keep", "MODE=legacy"],
        {"MODE": "local"},
        False,
    ),
    (
        "single-empty-value-not-deletion",
        ["MODE=old", "MODE=legacy"],
        {"MODE": ""},
        True,
    ),
    (
        "batch-deletion-removes-all-matching",
        ["# keep", "MODE=old", "MODE=legacy", "OTHER=first", "OTHER=second"],
        {"MODE": None},
        False,
    ),
    (
        "batch-absent-deletion-is-noop",
        ["# keep", "OTHER=first"],
        {"MODE": None},
        False,
    ),
    (
        "batch-append-empty-value",
        ["# keep", "OTHER=value"],
        {"MODE": ""},
        False,
    ),
    (
        "batch-multi-key-order",
        ["PULSESCRIBE_HOTKEY=f18", "PULSESCRIBE_HOLD_HOTKEY=fn"],
        {
            "PULSESCRIBE_TOGGLE_HOTKEY": "f19",
            "PULSESCRIBE_HOTKEY": None,
            "PULSESCRIBE_HOTKEY_MODE": None,
        },
        False,
    ),
    (
        "quoted-key-deletion",
        ["'OPENAI_API_KEY'=secret", "OTHER=1"],
        {"OPENAI_API_KEY": None},
        False,
    ),
    (
        "quoted-key-replacement",
        ["'OPENAI_API_KEY'=old"],
        {"OPENAI_API_KEY": "new"},
        False,
    ),
    (
        "quoted-key-single-unchanged",
        ["'OPENAI_API_KEY'=same"],
        {"OPENAI_API_KEY": "same"},
        True,
    ),
    (
        "export-tab-key-replacement",
        ["export\tMODE=old"],
        {"MODE": "local"},
        False,
    ),
)

_DOTENV_RESOLVE_PROCESS_ENV: dict[str, str] = {
    "PULSESCRIBE_GOLDEN_PROC_VAR": "from-proc",
}

_DOTENV_RESOLVE_CONTENT = "\n".join(
    [
        "BASE=actual-key",
        "GROQ_API_KEY=${BASE}",
        "CHAINED=${GROQ_API_KEY}-suffix",
        "FROM_PROC=${PULSESCRIBE_GOLDEN_PROC_VAR}",
        "WITH_DEFAULT=${PULSESCRIBE_GOLDEN_UNSET:-fallback}",
        "UNSET_PLAIN=${PULSESCRIBE_GOLDEN_UNSET}",
        "SINGLE_EXP='${BASE}'",
        'DOUBLE_EXP="${BASE}"',
        "BARE_DOLLAR=$BASE",
        "'QUOTED_KEY'=quoted-val",
        'PULSESCRIBE_APP_CONTEXTS={"Slack": "chat"}',
        'JSON_WITH_COMMENT={"a": "b"} # comment',
        "UNQUOTED_APOSTROPHE=Bob's Mic # comment",
        "PULSESCRIBE_INPUT_DEVICE=USB ${PULSESCRIBE_GOLDEN_PROC_VAR}",
    ]
) + "\n"


def _export_env_update_case(
    name: str,
    lines: list[str],
    updates: dict[str, str | None],
    preserve_following_duplicates: bool,
) -> dict[str, Any]:
    out_lines, changed = _apply_env_updates_to_lines(
        lines,
        updates,
        preserve_following_duplicates=preserve_following_duplicates,
    )
    return {
        "name": name,
        "lines": lines,
        "updates": [[k, v] for k, v in updates.items()],
        "preserve_following_duplicates": preserve_following_duplicates,
        "expected_lines": out_lines,
        "expected_changed": changed,
    }


def _export_dotenv_resolve() -> dict[str, Any]:
    sentinel_unset = "PULSESCRIBE_GOLDEN_UNSET"
    saved = {
        key: os.environ.get(key)
        for key in (*_DOTENV_RESOLVE_PROCESS_ENV.keys(), sentinel_unset)
    }
    try:
        for key, value in _DOTENV_RESOLVE_PROCESS_ENV.items():
            os.environ[key] = value
        os.environ.pop(sentinel_unset, None)
        with tempfile.TemporaryDirectory() as tmp_dir:
            env_path = Path(tmp_dir) / ".env"
            env_path.write_text(_DOTENV_RESOLVE_CONTENT, encoding="utf-8")
            resolved = _read_dotenv_values(env_path)
    finally:
        for key, previous in saved.items():
            if previous is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = previous

    return {
        "process_env": dict(_DOTENV_RESOLVE_PROCESS_ENV),
        "content": _DOTENV_RESOLVE_CONTENT,
        "expected": dict(sorted(resolved.items())),
    }


def build_core_contracts() -> dict[str, Any]:
    """Build deterministic core contract fixtures from the live Python code."""
    return {
        "schema_version": 2,
        "app_states": [state.value for state in AppState],
        "daemon_error_codes": [code.value for code in DaemonErrorCode],
        "parse_bool": [
            {"input": value, "expected": parse_bool(value)}
            for value in _PARSE_BOOL_INPUTS
        ],
        "parse_env_line": [
            {
                "input": raw,
                "key": parse_env_line(raw)[0],
                "value": parse_env_line(raw)[1],
            }
            for raw in _PARSE_ENV_LINE_INPUTS
        ],
        "format_env_assignment": [
            {"key": key, "value": value, "expected": _format_env_assignment(key, value)}
            for key, value in _FORMAT_ENV_INPUTS
        ],
        "env_updates": [
            _export_env_update_case(name, lines, updates, preserve)
            for name, lines, updates, preserve in _ENV_UPDATE_CASES
        ],
        "dotenv_resolve": _export_dotenv_resolve(),
        "onboarding_choices": [choice.value for choice in OnboardingChoice],
        "onboarding_steps": {
            "total_steps": total_steps(),
            "steps": [
                {
                    "value": step.value,
                    "next": next_step(step).value,
                    "prev": prev_step(step).value,
                    "step_index": step_index(step),
                }
                for step in ONBOARDING_ORDER
            ],
        },
    }


def render_core_contracts_json() -> str:
    return json.dumps(build_core_contracts(), indent=2, ensure_ascii=False) + "\n"


def main() -> None:
    GOLDEN_FILE.write_text(render_core_contracts_json(), encoding="utf-8")
    print(f"Wrote {GOLDEN_FILE.relative_to(PROJECT_ROOT)}")


if __name__ == "__main__":
    main()
