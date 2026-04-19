import types

from utils.state import AppState

from ui.menubar import (
    MENUBAR_ICONS,
    MenuBarController,
    _MenuActionHandler,
    build_menubar_hint_text,
    build_menubar_status_text,
    build_menubar_title,
)


class _FakeStatusItem:
    def __init__(self) -> None:
        self.title = ""
        self.calls = 0

    def setTitle_(self, title: str) -> None:
        self.title = title
        self.calls += 1


def test_menubar_icons_cover_listening_state() -> None:
    assert AppState.LISTENING in MENUBAR_ICONS
    assert MENUBAR_ICONS[AppState.LISTENING] != MENUBAR_ICONS[AppState.IDLE]


def test_update_state_uses_listening_icon() -> None:
    controller = MenuBarController.__new__(MenuBarController)
    controller._status_item = _FakeStatusItem()
    controller._menu_status_item = _FakeStatusItem()
    controller._menu_hint_item = _FakeStatusItem()
    controller._current_state = AppState.IDLE
    controller._current_title = None

    controller.update_state(AppState.LISTENING)

    assert controller._current_state == AppState.LISTENING
    assert controller._status_item.title == f"{MENUBAR_ICONS[AppState.LISTENING]} Listening…"
    assert controller._menu_status_item.title == "Listening"
    assert controller._menu_hint_item.title == build_menubar_hint_text(AppState.LISTENING)


def test_update_state_skips_duplicate_title_updates() -> None:
    controller = MenuBarController.__new__(MenuBarController)
    controller._status_item = _FakeStatusItem()
    controller._menu_status_item = _FakeStatusItem()
    controller._menu_hint_item = _FakeStatusItem()
    controller._current_state = AppState.IDLE
    controller._current_title = None

    controller.update_state(AppState.RECORDING, "abcdefghijklmnopqrstuv")
    controller.update_state(AppState.RECORDING, "abcdefghijklmnopqrstwx")

    assert controller._status_item.calls == 1


def test_build_menubar_title_depends_only_on_visible_preview() -> None:
    first = build_menubar_title(AppState.RECORDING, "abcdefghijklmnopqrstuv")
    second = build_menubar_title(AppState.RECORDING, "abcdefghijklmnopqrstwx")

    assert first == second


def test_build_menubar_title_collapses_multiline_whitespace() -> None:
    title = build_menubar_title(AppState.RECORDING, "alpha\n\tbeta   gamma")

    assert title == f"{MENUBAR_ICONS[AppState.RECORDING]} alpha beta gamma"
    assert "\n" not in title
    assert "\t" not in title


def test_build_menubar_title_hides_empty_whitespace_preview() -> None:
    assert build_menubar_title(AppState.RECORDING, "  \n\t  ") == (
        f"{MENUBAR_ICONS[AppState.RECORDING]} Recording…"
    )


def test_build_menubar_title_matches_visible_preview_for_whitespace_heavy_text() -> None:
    text = ("alpha   beta\n" * 2000) + "omega"

    title = build_menubar_title(AppState.RECORDING, text)

    assert title == f"{MENUBAR_ICONS[AppState.RECORDING]} alpha beta alpha bet…"


def test_build_menubar_title_uses_loading_text_preview() -> None:
    title = build_menubar_title(AppState.LOADING, "Loading large-v3 model")

    assert title.startswith(f"{MENUBAR_ICONS[AppState.LOADING]} Loading large-v3")


def test_build_menubar_status_text_uses_recording_preview() -> None:
    status = build_menubar_status_text(AppState.RECORDING, "alpha beta gamma")

    assert status == "Recording: alpha beta gamma"


def test_build_menubar_hint_text_guides_error_recovery() -> None:
    hint = build_menubar_hint_text(AppState.ERROR)

    assert "Setup & Settings" in hint
    assert "diagnostics" in hint.lower()


def test_set_menu_hint_updates_disabled_hint_item() -> None:
    controller = MenuBarController.__new__(MenuBarController)
    controller._menu_hint_item = _FakeStatusItem()

    controller._set_menu_hint("Opened the log file.")

    assert controller._menu_hint_item.title == "Opened the log file."


def test_menu_action_handler_open_logs_reports_created_file_feedback(monkeypatch, tmp_path) -> None:
    log_path = tmp_path / "logs" / "app.log"
    opened_paths: list[str] = []
    feedback: list[str] = []

    monkeypatch.setitem(
        __import__("sys").modules,
        "AppKit",
        types.SimpleNamespace(
            NSWorkspace=types.SimpleNamespace(
                sharedWorkspace=lambda: types.SimpleNamespace(
                    openFile_=lambda path: opened_paths.append(path) or True
                )
            )
        ),
    )

    handler = _MenuActionHandler().initWithLogPath_(str(log_path))
    handler.feedback_callback = feedback.append

    handler.openLogs_(None)

    assert log_path.exists()
    assert opened_paths == [str(log_path)]
    assert feedback == ["Created and opened a new log file."]


def test_menu_action_handler_export_diagnostics_reports_success(monkeypatch, tmp_path) -> None:
    archive = tmp_path / "diag.zip"
    feedback: list[str] = []

    monkeypatch.setitem(
        __import__("sys").modules,
        "utils.diagnostics",
        types.SimpleNamespace(export_diagnostics_report=lambda: archive),
    )

    handler = _MenuActionHandler().initWithLogPath_("ignored.log")
    handler.feedback_callback = feedback.append

    handler.exportDiagnostics_(None)

    assert feedback == [f"Diagnostics exported: {archive.name}"]


def test_menu_action_handler_export_diagnostics_reports_failure(monkeypatch) -> None:
    feedback: list[str] = []

    def _boom():
        raise RuntimeError("boom")

    monkeypatch.setitem(
        __import__("sys").modules,
        "utils.diagnostics",
        types.SimpleNamespace(export_diagnostics_report=_boom),
    )

    handler = _MenuActionHandler().initWithLogPath_("ignored.log")
    handler.feedback_callback = feedback.append

    handler.exportDiagnostics_(None)

    assert feedback == ["Diagnostics export failed — check the log file and try again."]
