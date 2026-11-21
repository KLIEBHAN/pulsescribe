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
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from utils.state import AppState, DaemonErrorCode  # noqa: E402

GOLDEN_FILE = Path(__file__).resolve().parent / "core_contracts.json"


def build_core_contracts() -> dict[str, Any]:
    """Build deterministic core contract fixtures from the live Python code."""
    return {
        "schema_version": 1,
        "app_states": [state.value for state in AppState],
        "daemon_error_codes": [code.value for code in DaemonErrorCode],
    }


def render_core_contracts_json() -> str:
    return json.dumps(build_core_contracts(), indent=2, ensure_ascii=False) + "\n"


def main() -> None:
    GOLDEN_FILE.write_text(render_core_contracts_json(), encoding="utf-8")
    print(f"Wrote {GOLDEN_FILE.relative_to(PROJECT_ROOT)}")


if __name__ == "__main__":
    main()
