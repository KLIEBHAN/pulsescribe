from utils.state import AppState

from ui.menubar import MENUBAR_ICONS, MenuBarController, build_menubar_title


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
    controller._current_state = AppState.IDLE

    controller.update_state(AppState.LISTENING)

    assert controller._current_state == AppState.LISTENING
    assert controller._status_item.title == MENUBAR_ICONS[AppState.LISTENING]


def test_update_state_skips_duplicate_title_updates() -> None:
    controller = MenuBarController.__new__(MenuBarController)
    controller._status_item = _FakeStatusItem()
    controller._current_state = AppState.IDLE
    controller._current_title = None

    controller.update_state(AppState.RECORDING, "abcdefghijklmnopqrstuv")
    controller.update_state(AppState.RECORDING, "abcdefghijklmnopqrstwx")

    assert controller._status_item.calls == 1


def test_build_menubar_title_depends_only_on_visible_preview() -> None:
    first = build_menubar_title(AppState.RECORDING, "abcdefghijklmnopqrstuv")
    second = build_menubar_title(AppState.RECORDING, "abcdefghijklmnopqrstwx")

    assert first == second
