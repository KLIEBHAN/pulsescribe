from __future__ import annotations

from pathlib import Path

import utils.paths as paths_mod


def test_get_resource_path_uses_project_root_not_current_working_directory(
    tmp_path, monkeypatch
) -> None:
    project_root = tmp_path / "project"
    utils_dir = project_root / "utils"
    utils_dir.mkdir(parents=True)
    monkeypatch.setattr(paths_mod, "__file__", str(utils_dir / "paths.py"))

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    resolved = Path(paths_mod.get_resource_path("assets/icon.ico"))

    assert resolved == project_root / "assets" / "icon.ico"


def test_get_resource_path_prefers_pyinstaller_meipass(tmp_path, monkeypatch) -> None:
    bundle_root = tmp_path / "bundle"
    bundle_root.mkdir()
    monkeypatch.setattr(paths_mod.sys, "_MEIPASS", str(bundle_root), raising=False)

    resolved = Path(paths_mod.get_resource_path("assets/icon.ico"))

    assert resolved == bundle_root / "assets" / "icon.ico"
