from __future__ import annotations

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
