from utils.state import AppState

from ui.menubar import MENUBAR_ICONS, MenuBarController


class _FakeStatusItem:
    def __init__(self) -> None:
        self.title = ""

    def setTitle_(self, title: str) -> None:
        self.title = title


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
