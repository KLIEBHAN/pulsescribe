from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import Mock

import build_windows


def test_build_variant_sets_local_flag_for_full_build(monkeypatch, tmp_path):
    monkeypatch.setattr(build_windows, "__file__", str(tmp_path / "build_windows.py"))
    (tmp_path / "build_windows.spec").write_text("", encoding="utf-8")

    mock_run = Mock()
    monkeypatch.setattr(build_windows.subprocess, "run", mock_run)

    assert (
        build_windows.build_variant(
            "build_windows.spec",
            "Full Version",
            build_local=True,
        )
        is True
    )

    _, kwargs = mock_run.call_args
    assert kwargs["cwd"] == str(tmp_path)
    assert kwargs["env"]["PULSESCRIBE_BUILD_LOCAL"] == "1"


def test_build_variant_clears_local_flag_for_light_build(monkeypatch, tmp_path):
    monkeypatch.setattr(build_windows, "__file__", str(tmp_path / "build_windows.py"))
    (tmp_path / "build_windows_light.spec").write_text("", encoding="utf-8")

    mock_run = Mock()
    monkeypatch.setattr(build_windows.subprocess, "run", mock_run)

    assert (
        build_windows.build_variant(
            "build_windows_light.spec",
            "Light Version",
            build_local=False,
        )
        is True
    )

    _, kwargs = mock_run.call_args
    assert kwargs["cwd"] == str(tmp_path)
    assert kwargs["env"]["PULSESCRIBE_BUILD_LOCAL"] == "0"


def test_essentials_only_build_collects_qt_enum_support():
    root = Path(__file__).resolve().parents[1]
    spec = ast.parse((root / "build_windows.spec").read_text(encoding="utf-8"))
    hiddenimports = next(
        ast.literal_eval(node.value)
        for node in spec.body
        if isinstance(node, ast.Assign)
        and isinstance(node.value, ast.List)
        and any(
            isinstance(target, ast.Name) and target.id == "hiddenimports"
            for target in node.targets
        )
    )
    assert "PySide6.support.deprecated" in hiddenimports
    requirements = (root / "requirements-build-windows.txt").read_text().splitlines()
    assert "PySide6-Essentials>=6,<7" in requirements
    assert not any(line.startswith("PySide6>=") for line in requirements)
