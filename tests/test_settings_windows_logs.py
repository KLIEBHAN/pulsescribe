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
        self._interval_ms = 0
        self.set_interval_calls: list[int] = []

    def isActive(self) -> bool:
        return self._active

    def start(self, interval_ms: int) -> None:
        self._active = True
        self._interval_ms = interval_ms
        self.started_with.append(interval_ms)

    def stop(self) -> None:
        self._active = False
        self.stopped = True

    def interval(self) -> int:
        return self._interval_ms

    def setInterval(self, interval_ms: int) -> None:
        self._interval_ms = interval_ms
        self.set_interval_calls.append(interval_ms)


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


class _FakeScrollBar:
    def __init__(self, value: int = 100, maximum: int = 100):
        self._value = value
        self._maximum = maximum

    def value(self) -> int:
        return self._value

    def maximum(self) -> int:
        return self._maximum

    def setValue(self, value: int) -> None:
        self._value = value


class _FakeCursor:
    def __init__(self, viewer):
        self._viewer = viewer
        self.moved_to = None

    def movePosition(self, operation) -> None:
        self.moved_to = operation

    def insertText(self, text: str) -> None:
        self._viewer.text += text


class _FakeLogsViewer:
    def __init__(self, text: str, *, scroll_value: int = 100, scroll_maximum: int = 100):
        self.text = text
        self._scrollbar = _FakeScrollBar(scroll_value, scroll_maximum)
        self.set_plain_text_calls: list[str] = []

    def verticalScrollBar(self):
        return self._scrollbar

    def textCursor(self):
        return _FakeCursor(self)

    def setPlainText(self, text: str) -> None:
        self.text = text
        self.set_plain_text_calls.append(text)


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


def test_refresh_transcripts_skips_full_reload_when_incremental_append_succeeds(
    tmp_path, monkeypatch
):
    import ui.settings_windows as settings_mod
    import utils.history as history_mod

    original_line = '{"timestamp":"2026-01-01T10:00:00","text":"hello"}\n'
    appended_line = '{"timestamp":"2026-01-01T10:00:01","text":"world"}\n'
    history_file = tmp_path / "history.jsonl"
    history_file.write_text(original_line + appended_line, encoding="utf-8")
    monkeypatch.setattr(history_mod, "HISTORY_FILE", history_file)
    monkeypatch.setattr(
        settings_mod,
        "get_file_signature",
        lambda _path: (99, len((original_line + appended_line).encode("utf-8"))),
    )
    monkeypatch.setattr(
        history_mod,
        "get_recent_transcripts",
        lambda _count: (_ for _ in ()).throw(
            AssertionError("full transcript reload should be skipped")
        ),
    )

    window = SettingsWindow.__new__(SettingsWindow)
    window._transcripts_viewer = _FakeLogsViewer("[2026-01-01 10:00:00] hello")
    window._transcripts_status = _FakeLabel()
    window._last_transcripts_text = "[2026-01-01 10:00:00] hello"
    window._last_transcripts_signature = (1, len(original_line.encode("utf-8")))
    window._last_transcripts_entries = [
        {"timestamp": "2026-01-01T10:00:00", "text": "hello"}
    ]

    window._refresh_transcripts()

    assert window._transcripts_viewer.text == (
        "[2026-01-01 10:00:00] hello\n\n[2026-01-01 10:00:01] world"
    )
    assert window._transcripts_viewer.set_plain_text_calls == []
    assert window._transcripts_status.text == "2 entries"
    assert [entry["text"] for entry in window._last_transcripts_entries] == [
        "hello",
        "world",
    ]
    assert window._last_transcripts_blocks == [
        "[2026-01-01 10:00:00] hello",
        "[2026-01-01 10:00:01] world",
    ]


def test_refresh_transcripts_reuses_cached_blocks_when_append_replaces_visible_text(
    tmp_path, monkeypatch
):
    import ui.settings_windows as settings_mod
    import utils.history as history_mod

    original_line = '{"timestamp":"2026-01-01T10:00:00","text":"hello"}\n'
    appended_line = '{"timestamp":"2026-01-01T10:00:01","text":"world"}\n'
    history_file = tmp_path / "history.jsonl"
    history_file.write_text(original_line + appended_line, encoding="utf-8")
    monkeypatch.setattr(history_mod, "HISTORY_FILE", history_file)
    monkeypatch.setattr(
        settings_mod,
        "get_file_signature",
        lambda _path: (99, len((original_line + appended_line).encode("utf-8"))),
    )
    monkeypatch.setattr(
        history_mod,
        "get_recent_transcripts",
        lambda _count: (_ for _ in ()).throw(
            AssertionError("full transcript reload should be skipped")
        ),
    )

    formatted_entries: list[str] = []
    original_formatter = history_mod.format_transcript_entry_for_display

    def _tracking_formatter(entry):
        text = entry.get("text", "") if isinstance(entry, dict) else ""
        formatted_entries.append(str(text))
        return original_formatter(entry)

    monkeypatch.setattr(
        history_mod,
        "format_transcript_entry_for_display",
        _tracking_formatter,
    )

    window = SettingsWindow.__new__(SettingsWindow)
    window._transcripts_viewer = _FakeLogsViewer(
        "[2026-01-01 10:00:00] hello",
        scroll_value=10,
        scroll_maximum=100,
    )
    window._transcripts_status = _FakeLabel()
    window._last_transcripts_text = "[2026-01-01 10:00:00] hello"
    window._last_transcripts_signature = (1, len(original_line.encode("utf-8")))
    window._last_transcripts_entries = [
        {"timestamp": "2026-01-01T10:00:00", "text": "hello"}
    ]
    window._last_transcripts_blocks = ["[2026-01-01 10:00:00] hello"]

    window._refresh_transcripts()

    assert window._transcripts_viewer.text == (
        "[2026-01-01 10:00:00] hello\n\n[2026-01-01 10:00:01] world"
    )
    assert window._transcripts_viewer.set_plain_text_calls == [
        "[2026-01-01 10:00:00] hello\n\n[2026-01-01 10:00:01] world"
    ]
    assert formatted_entries == ["world"]
    assert window._transcripts_status.text == "2 entries"
    assert window._last_transcripts_blocks == [
        "[2026-01-01 10:00:00] hello",
        "[2026-01-01 10:00:01] world",
    ]


def test_try_append_logs_delta_appends_only_new_text(tmp_path, monkeypatch):
    import config

    initial_text = "line-1"
    full_text = "line-1\nline-2"
    log_file = tmp_path / "pulsescribe.log"
    log_file.write_text(full_text, encoding="utf-8")
    monkeypatch.setattr(config, "LOG_FILE", log_file)

    window = SettingsWindow.__new__(SettingsWindow)
    window._logs_viewer = _FakeLogsViewer(initial_text)
    window._last_logs_text = initial_text
    window._last_logs_signature = (1, len(initial_text))

    assert window._try_append_logs_delta((2, len(full_text))) is True
    assert window._logs_viewer.text == full_text
    assert window._last_logs_text == full_text
    assert window._last_logs_signature == (2, len(full_text))


def test_refresh_logs_skips_full_tail_read_when_incremental_append_succeeds(
    tmp_path, monkeypatch
):
    import config

    full_text = "line-1\nline-2"
    log_file = tmp_path / "pulsescribe.log"
    log_file.write_text(full_text, encoding="utf-8")
    monkeypatch.setattr(config, "LOG_FILE", log_file)
    monkeypatch.setattr(settings_mod, "get_file_signature", lambda _path: (9, len(full_text)))
    monkeypatch.setattr(
        settings_mod,
        "read_file_tail_lines",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("full tail read should be skipped")
        ),
    )

    append_calls: list[tuple[int, int]] = []
    window = SettingsWindow.__new__(SettingsWindow)
    window._logs_viewer = object()
    window._last_logs_text = "line-1"
    window._last_logs_signature = (1, len("line-1"))
    window._try_append_logs_delta = lambda signature: append_calls.append(signature) or True

    window._refresh_logs()

    assert append_calls == [(9, len(full_text))]


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
    window._logs_auto_refresh_step = 0
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
    window._logs_auto_refresh_step = 0
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


def test_refresh_active_logs_view_backs_off_auto_refresh_when_idle():
    window = SettingsWindow.__new__(SettingsWindow)
    window._logs_stack = _FakeStack(current_index=0)
    window._logs_refresh_timer = _FakeTimer()
    window._logs_refresh_timer.start(2000)
    window._logs_auto_refresh_step = 0
    window._refresh_logs = lambda: False
    window._refresh_transcripts = lambda: (_ for _ in ()).throw(
        AssertionError("logs view should be refreshed")
    )

    window._refresh_active_logs_view()
    window._refresh_active_logs_view()

    assert window._logs_auto_refresh_step == 2
    assert window._logs_refresh_timer.set_interval_calls == [4000, 8000]


def test_refresh_active_logs_view_resets_auto_refresh_after_change():
    window = SettingsWindow.__new__(SettingsWindow)
    window._logs_stack = _FakeStack(current_index=1)
    window._logs_refresh_timer = _FakeTimer()
    window._logs_refresh_timer.start(8000)
    window._logs_auto_refresh_step = 2
    window._refresh_logs = lambda: (_ for _ in ()).throw(
        AssertionError("transcripts view should be refreshed")
    )
    window._refresh_transcripts = lambda: True

    window._refresh_active_logs_view()

    assert window._logs_auto_refresh_step == 0
    assert window._logs_refresh_timer.set_interval_calls == [2000]


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
