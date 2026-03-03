import pytest

pytest.importorskip("PySide6")

from ui.settings_windows import SettingsWindow


class _FakeLabel:
    def __init__(self):
        self.text = ""
        self.style = ""

    def setText(self, text: str) -> None:
        self.text = text

    def setStyleSheet(self, style: str) -> None:
        self.style = style


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


def test_refresh_transcripts_skips_reload_when_signature_unchanged(
    tmp_path, monkeypatch
):
    import ui.settings_windows as settings_mod
    import utils.history as history_mod

    history_file = tmp_path / "history.jsonl"
    history_file.write_text('{"timestamp":"2026-01-01T10:00:00","text":"hello"}\n')
    monkeypatch.setattr(history_mod, "HISTORY_FILE", history_file)

    read_calls: list[int] = []
    monkeypatch.setattr(
        settings_mod,
        "get_file_signature",
        lambda _path: (123, 456),
    )
    monkeypatch.setattr(
        history_mod,
        "get_recent_transcripts",
        lambda count: read_calls.append(count) or [],
    )

    window = SettingsWindow.__new__(SettingsWindow)
    window._last_transcripts_signature = (123, 456)
    window._transcripts_status = _FakeLabel()
    window._set_transcripts_text_if_changed = lambda _text: (_ for _ in ()).throw(
        AssertionError("transcripts should not refresh")
    )

    window._refresh_transcripts()

    assert read_calls == []


def test_refresh_transcripts_updates_when_signature_changes(tmp_path, monkeypatch):
    import ui.settings_windows as settings_mod
    import utils.history as history_mod

    history_file = tmp_path / "history.jsonl"
    history_file.write_text('{"timestamp":"2026-01-01T10:00:00","text":"hello"}\n')
    monkeypatch.setattr(history_mod, "HISTORY_FILE", history_file)

    monkeypatch.setattr(settings_mod, "get_file_signature", lambda _path: (99, 42))
    monkeypatch.setattr(
        history_mod,
        "get_recent_transcripts",
        lambda _count: [{"timestamp": "2026-01-01T10:00:00", "text": "hello"}],
    )
    monkeypatch.setattr(
        history_mod,
        "format_transcripts_for_display",
        lambda _entries: "formatted-transcripts",
    )

    captured_text: list[str] = []
    window = SettingsWindow.__new__(SettingsWindow)
    window._last_transcripts_signature = None
    window._transcripts_status = _FakeLabel()
    window._set_transcripts_text_if_changed = lambda text: captured_text.append(text)

    window._refresh_transcripts()

    assert captured_text == ["formatted-transcripts"]
    assert window._last_transcripts_signature == (99, 42)
    assert window._transcripts_status.text == "1 entries"
