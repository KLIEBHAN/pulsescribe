"""Menübar-Controller für pulsescribe."""

import logging
from pathlib import Path
from typing import TYPE_CHECKING

logger = logging.getLogger("pulsescribe.ui.menubar")

try:
    import objc  # type: ignore[import-not-found]
    from Foundation import NSObject  # type: ignore[import-not-found]
except Exception:
    objc = None

    class NSObject:  # type: ignore[no-redef]
        """Fallback-Basisklasse für import-sichere Tests ohne PyObjC."""

        pass

if TYPE_CHECKING:
    NSObjectBase = object
else:
    NSObjectBase = NSObject

from config import LOG_FILE
from utils.state import AppState

# Status-Icons für Menübar
MENUBAR_ICONS = {
    AppState.IDLE: "🎤",
    AppState.LOADING: "⬇️",  # Model is being loaded/downloaded
    AppState.LISTENING: "🟠",  # Hold hotkey active, waiting for speech
    AppState.RECORDING: "🔴",
    AppState.TRANSCRIBING: "⏳",
    AppState.REFINING: "⏳",  # Refining uses same icon as transcribing for now
    AppState.DONE: "✅",
    AppState.ERROR: "❌",
}


def _objc_signature(signature: bytes):
    """Return a no-op decorator when PyObjC is unavailable."""

    def _decorate(func):
        if objc is None:
            return func
        return objc.signature(signature)(func)

    return _decorate


class _MenuActionHandler(NSObjectBase):
    """Objective-C Target für Menü-Actions."""

    welcome_callback = None  # Callback für Settings-Fenster

    def initWithLogPath_(self, log_path: str):
        if objc is None:
            self.log_path = log_path
            return self
        self = objc.super(_MenuActionHandler, self).init()
        if self is None:
            return None
        self.log_path = log_path
        return self

    @_objc_signature(b"v@:@")
    def openLogs_(self, _sender) -> None:
        """Öffnet die Log-Datei im Standard-Viewer."""
        from AppKit import NSWorkspace  # type: ignore[import-not-found]

        log_path = Path(self.log_path)
        if not log_path.exists():
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.touch()
        NSWorkspace.sharedWorkspace().openFile_(str(log_path))

    @_objc_signature(b"v@:@")
    def showSetup_(self, _sender) -> None:
        """Öffnet das Settings/Welcome-Fenster."""
        if self.welcome_callback:
            self.welcome_callback()

    @_objc_signature(b"v@:@")
    def exportDiagnostics_(self, _sender) -> None:
        """Erstellt einen Diagnostics-Report (ohne Audio) und öffnet Finder."""
        try:
            from utils.diagnostics import export_diagnostics_report

            export_diagnostics_report()
        except Exception:
            # Diagnostics is best-effort; avoid crashing the menu bar app.
            logger.warning("Diagnostics export failed", exc_info=True)
            return


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

        # Target für Menü-Callbacks
        self._action_handler = _MenuActionHandler.alloc().initWithLogPath_(
            str(LOG_FILE)
        )

        self._status_bar = NSStatusBar.systemStatusBar()
        self._status_item = self._status_bar.statusItemWithLength_(
            NSVariableStatusItemLength
        )
        self._current_title = MENUBAR_ICONS[AppState.IDLE]
        self._status_item.setTitle_(self._current_title)

        # Dropdown Menü erstellen
        menu = NSMenu.alloc().init()

        # Titel-Item (Info)
        title_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "PulseScribe", None, ""
        )
        title_item.setEnabled_(False)
        menu.addItem_(title_item)

        menu.addItem_(NSMenuItem.separatorItem())

        # Settings öffnen
        setup_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Settings...", "showSetup:", ""
        )
        setup_item.setTarget_(self._action_handler)
        menu.addItem_(setup_item)

        # Logs öffnen
        logs_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Open Logs", "openLogs:", ""
        )
        logs_item.setTarget_(self._action_handler)
        menu.addItem_(logs_item)

        # Diagnostics export
        diag_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Export Diagnostics…", "exportDiagnostics:", ""
        )
        diag_item.setTarget_(self._action_handler)
        menu.addItem_(diag_item)

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
            title = f"{icon} {preview}"
        else:
            title = icon

        if getattr(self, "_current_title", None) == title:
            return

        self._status_item.setTitle_(title)
        self._current_title = title

    def set_welcome_callback(self, callback) -> None:
        """Setzt Callback für Settings-Menü-Item."""
        self._action_handler.welcome_callback = callback
