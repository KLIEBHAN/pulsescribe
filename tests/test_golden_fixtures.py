"""Ensure language-neutral golden fixtures stay in sync with Python."""

from __future__ import annotations

import json

from tests.golden.export_fixtures import GOLDEN_FILE, build_core_contracts


def test_core_contracts_golden_fixture_matches_python() -> None:
    assert GOLDEN_FILE.exists(), (
        "Missing golden fixture; run `python tests/golden/export_fixtures.py`."
    )
    stored = json.loads(GOLDEN_FILE.read_text(encoding="utf-8"))
    assert stored == build_core_contracts(), (
        "Golden fixture drifted from Python behavior; regenerate with "
        "`python tests/golden/export_fixtures.py` and update Rust parity tests."
    )
