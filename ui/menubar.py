"""Menübar-Controller für whisper_go."""

from utils.state import AppState

# Status-Icons für Menübar
MENUBAR_ICONS = {
    AppState.IDLE: "🎤",
    AppState.RECORDING: "🔴",
    AppState.TRANSCRIBING: "⏳",
    AppState.REFINING: "⏳",  # Refining uses same icon as transcribing for now
    AppState.DONE: "✅",
    AppState.ERROR: "❌",
}


class MenuBarController:
    """
    Menübar-Status-Anzeige via NSStatusBar.

    Zeigt aktuellen State als Icon + optional Interim-Text.
    Kein Polling - wird direkt via Callback aktualisiert.
    """

    def __init__(self):
        from AppKit import (  # type: ignore[import-not-found]
            NSStatusBar,
            NSVariableStatusItemLength,
            NSMenu,
            NSMenuItem,
        )

        self._status_bar = NSStatusBar.systemStatusBar()
        self._status_item = self._status_bar.statusItemWithLength_(
            NSVariableStatusItemLength
        )
        self._status_item.setTitle_(MENUBAR_ICONS[AppState.IDLE])

        # Dropdown Menü erstellen
        menu = NSMenu.alloc().init()

        # Titel-Item (Info)
        title_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Whisper Go", None, ""
        )
        title_item.setEnabled_(False)
        menu.addItem_(title_item)

        menu.addItem_(NSMenuItem.separatorItem())

        # Quit-Item (kein Shortcut - CMD+Q läuft über Application Menu)
        quit_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Quit", "terminate:", ""
        )
        menu.addItem_(quit_item)

        self._status_item.setMenu_(menu)

        self._current_state = AppState.IDLE

    def update_state(self, state: AppState, text: str | None = None) -> None:
        """Aktualisiert Menübar-Icon und optional Text."""
        self._current_state = state
        icon = MENUBAR_ICONS.get(state, MENUBAR_ICONS[AppState.IDLE])

        if state == AppState.RECORDING and text:
            # Kürzen für Menübar
            preview = text[:20] + "…" if len(text) > 20 else text
            self._status_item.setTitle_(f"{icon} {preview}")
        else:
            self._status_item.setTitle_(icon)
