import pytest

pytest.importorskip("PySide6")

import ui.settings_windows as settings_mod
from ui.settings_windows import SettingsWindow


class _FakeLabel:
    def __init__(self):
        self.text = ""
        self.style = ""

    def setText(self, text: str) -> None:
        self.text = text

    def setStyleSheet(self, style: str) -> None:
        self.style = style


class _FakeTimer:
    def __init__(self):
        self.started_with: list[int] = []
        self.stopped = False
        self._active = False

    def isActive(self) -> bool:
        return self._active

    def start(self, interval_ms: int) -> None:
        self._active = True
        self.started_with.append(interval_ms)

    def stop(self) -> None:
        self._active = False
        self.stopped = True


class _FakeCheckBox:
    def __init__(self, checked: bool):
        self._checked = checked

    def isChecked(self) -> bool:
        return self._checked


class _FakeStack:
    def __init__(self, current_index: int):
        self._current_index = current_index

    def currentIndex(self) -> int:
        return self._current_index


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


def test_update_logs_auto_refresh_state_stops_timer_when_window_not_visible():
    window = SettingsWindow.__new__(SettingsWindow)
    timer = _FakeTimer()
    timer._active = True
    window._logs_refresh_timer = timer
    window._logs_stack = _FakeStack(current_index=0)
    window._auto_refresh_checkbox = _FakeCheckBox(checked=True)
    window._is_logs_tab_active = lambda: True
    window._is_window_visible_for_logs = lambda: False

    window._update_logs_auto_refresh_state()

    assert timer.stopped is True
    assert timer.isActive() is False


def test_update_logs_auto_refresh_state_starts_timer_when_visible():
    window = SettingsWindow.__new__(SettingsWindow)
    timer = _FakeTimer()
    window._logs_refresh_timer = timer
    window._logs_stack = _FakeStack(current_index=0)
    window._auto_refresh_checkbox = _FakeCheckBox(checked=True)
    window._is_logs_tab_active = lambda: True
    window._is_window_visible_for_logs = lambda: True

    window._update_logs_auto_refresh_state()

    assert timer.started_with == [2000]
    assert timer.isActive() is True


def test_update_logs_auto_refresh_state_starts_timer_for_transcripts_view():
    window = SettingsWindow.__new__(SettingsWindow)
    timer = _FakeTimer()
    window._logs_refresh_timer = timer
    window._logs_stack = _FakeStack(current_index=1)
    window._auto_refresh_checkbox = _FakeCheckBox(checked=True)
    window._is_logs_tab_active = lambda: True
    window._is_window_visible_for_logs = lambda: True

    window._update_logs_auto_refresh_state()

    assert timer.started_with == [2000]
    assert timer.isActive() is True


def test_refresh_active_logs_view_routes_to_transcripts():
    window = SettingsWindow.__new__(SettingsWindow)
    window._logs_stack = _FakeStack(current_index=1)

    calls: list[str] = []
    window._refresh_logs = lambda: calls.append("logs")
    window._refresh_transcripts = lambda: calls.append("transcripts")

    window._refresh_active_logs_view()

    assert calls == ["transcripts"]


def test_clear_transcripts_requires_confirmation(monkeypatch):
    window = SettingsWindow.__new__(SettingsWindow)
    window._transcripts_status = _FakeLabel()

    refresh_calls: list[bool] = []
    window._refresh_transcripts = lambda: refresh_calls.append(True)

    monkeypatch.setattr(
        settings_mod.QMessageBox,
        "question",
        lambda *args, **kwargs: settings_mod.QMessageBox.StandardButton.Cancel,
    )

    clear_calls: list[bool] = []
    monkeypatch.setattr(
        "utils.history.clear_history",
        lambda: clear_calls.append(True) or True,
    )

    window._clear_transcripts()

    assert clear_calls == []
    assert refresh_calls == []
    assert window._transcripts_status.text == ""


def test_clear_transcripts_updates_success_status_after_confirm(monkeypatch):
    window = SettingsWindow.__new__(SettingsWindow)
    window._transcripts_status = _FakeLabel()

    refresh_calls: list[bool] = []
    window._refresh_transcripts = lambda: refresh_calls.append(True)

    monkeypatch.setattr(
        settings_mod.QMessageBox,
        "question",
        lambda *args, **kwargs: settings_mod.QMessageBox.StandardButton.Yes,
    )
    monkeypatch.setattr("utils.history.clear_history", lambda: True)

    window._clear_transcripts()

    assert refresh_calls == [True]
    assert window._transcripts_status.text == "History cleared"


def test_clear_transcripts_shows_error_when_delete_fails(monkeypatch):
    window = SettingsWindow.__new__(SettingsWindow)
    window._transcripts_status = _FakeLabel()

    refresh_calls: list[bool] = []
    window._refresh_transcripts = lambda: refresh_calls.append(True)

    monkeypatch.setattr(
        settings_mod.QMessageBox,
        "question",
        lambda *args, **kwargs: settings_mod.QMessageBox.StandardButton.Yes,
    )
    monkeypatch.setattr("utils.history.clear_history", lambda: False)

    window._clear_transcripts()

    assert refresh_calls == []
    assert window._transcripts_status.text == "Could not clear history. Try again."
