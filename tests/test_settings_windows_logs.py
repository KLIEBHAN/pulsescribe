import pytest

pytest.importorskip("PySide6")

from ui.settings_windows import SettingsWindow


def test_open_logs_folder_selects_log_file_when_present(tmp_path, monkeypatch):
    import config
    import subprocess

    log_file = tmp_path / "pulsescribe.log"
    log_file.write_text("hello", encoding="utf-8")

    calls: list[list[str]] = []
    monkeypatch.setattr(config, "LOG_FILE", log_file)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda cmd, check=False: calls.append(cmd),
    )

    window = SettingsWindow.__new__(SettingsWindow)
    window._open_logs_folder()

    assert calls == [["explorer", "/select,", str(log_file)]]


def test_open_logs_folder_falls_back_to_parent_folder_when_log_missing(
    tmp_path, monkeypatch
):
    import config
    import subprocess

    log_file = tmp_path / "pulsescribe.log"

    calls: list[list[str]] = []
    monkeypatch.setattr(config, "LOG_FILE", log_file)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda cmd, check=False: calls.append(cmd),
    )

    window = SettingsWindow.__new__(SettingsWindow)
    window._open_logs_folder()

    assert calls == [["explorer", str(log_file.parent)]]
