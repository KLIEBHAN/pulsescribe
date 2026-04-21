"""Menübar-Controller für pulsescribe."""

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from config import LOG_FILE
from ui.daemon_status_feedback import (
    build_daemon_status_hint,
    build_daemon_status_label,
    normalize_daemon_status_text,
)
from utils.state import AppState

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

# Status-Icons für Menübar
MENUBAR_ICONS = {
    AppState.IDLE: "🎤",
    AppState.LOADING: "⬇️",  # Model is being loaded/downloaded
    AppState.LISTENING: "🟠",  # Hold hotkey active, waiting for speech
    AppState.RECORDING: "🔴",
    AppState.TRANSCRIBING: "⏳",
    AppState.REFINING: "⏳",  # Refining uses same icon as transcribing for now
    AppState.DONE: "✅",
    AppState.NO_SPEECH: "🔇",
    AppState.ERROR: "❌",
}
MENUBAR_PREVIEW_MAX_CHARS = 20
MENUBAR_STATUS_MAX_CHARS = 44
MENUBAR_HINT_MAX_CHARS = 84
MENUBAR_STATE_LABELS = {
    AppState.IDLE: "Ready",
    AppState.LOADING: "Loading…",
    AppState.LISTENING: "Listening…",
    AppState.RECORDING: "Recording…",
    AppState.TRANSCRIBING: "Transcribing…",
    AppState.REFINING: "Refining…",
    AppState.DONE: "Done",
    AppState.NO_SPEECH: "No speech",
    AppState.ERROR: "Error",
}


def _build_normalized_preview_prefix(
    text: str,
    *,
    max_chars: int,
) -> tuple[str, bool]:
    """Return a whitespace-normalized prefix plus whether more visible text exists.

    The menu bar only renders a tiny leading preview. Scanning the whole interim
    transcript on every update would be wasteful, so this helper collapses
    whitespace incrementally and stops as soon as the visible preview budget is
    known.
    """
    if max_chars <= 0:
        return "", False

    visible_limit = max_chars + 1
    preview_chars: list[str] = []
    saw_content = False
    pending_space = False

    for ch in text:
        if ch.isspace():
            if saw_content:
                pending_space = True
            continue

        if pending_space and preview_chars:
            preview_chars.append(" ")
            if len(preview_chars) >= visible_limit:
                return "".join(preview_chars), True
            pending_space = False

        saw_content = True
        preview_chars.append(ch)
        if len(preview_chars) >= visible_limit:
            return "".join(preview_chars), True

    return "".join(preview_chars), False


def _truncate_menubar_text(text: str | None, *, max_chars: int) -> str:
    normalized_prefix, is_truncated = _build_normalized_preview_prefix(
        text or "",
        max_chars=max_chars,
    )
    if not normalized_prefix:
        return ""

    preview = normalized_prefix[:max_chars]
    if is_truncated:
        preview = f"{preview}…"
    return preview



def build_menubar_status_text(state: AppState, text: str | None = None) -> str:
    """Return a friendly status sentence for the dropdown menu."""
    return build_daemon_status_label(
        state,
        text,
        prefer_detail=True,
        max_chars=MENUBAR_STATUS_MAX_CHARS,
    )



def build_menubar_hint_text(state: AppState, text: str | None = None) -> str:
    """Return a short contextual hint for the dropdown menu."""
    hint = build_daemon_status_hint(state, text, max_chars=MENUBAR_HINT_MAX_CHARS)
    return _truncate_menubar_text(hint, max_chars=MENUBAR_HINT_MAX_CHARS) or hint



def _set_menu_item_title_if_changed(item, title: str) -> bool:
    if item is None:
        return False
    current_title = getattr(item, "title", None)
    if callable(current_title):
        try:
            if str(current_title()) == title:
                return False
        except Exception:
            pass
    else:
        try:
            if str(current_title) == title:
                return False
        except Exception:
            pass
    try:
        item.setTitle_(title)
        return True
    except Exception:
        return False



def build_menubar_title(state: AppState, text: str | None = None) -> str:
    """Return the visible menu bar title for a given state payload."""
    icon = MENUBAR_ICONS.get(state, MENUBAR_ICONS[AppState.IDLE])
    if state == AppState.IDLE:
        return icon

    if state == AppState.RECORDING:
        preview = _truncate_menubar_text(text, max_chars=MENUBAR_PREVIEW_MAX_CHARS)
        if preview:
            return f"{icon} {preview}"
        return f"{icon} {MENUBAR_STATE_LABELS[state]}"

    if state == AppState.LOADING and not normalize_daemon_status_text(text):
        return f"{icon} {MENUBAR_STATE_LABELS[state]}"

    if state in (AppState.LISTENING, AppState.TRANSCRIBING, AppState.REFINING):
        return f"{icon} {MENUBAR_STATE_LABELS.get(state, MENUBAR_STATE_LABELS[AppState.IDLE])}"

    title_text = build_daemon_status_label(
        state,
        text,
        prefer_detail=True,
        max_chars=MENUBAR_PREVIEW_MAX_CHARS,
    )
    if not title_text:
        title_text = MENUBAR_STATE_LABELS.get(state, MENUBAR_STATE_LABELS[AppState.IDLE])
    return f"{icon} {title_text}"


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
    feedback_callback = None

    def initWithLogPath_(self, log_path: str):
        if objc is None:
            self.log_path = log_path
            self.feedback_callback = None
            return self
        self = objc.super(_MenuActionHandler, self).init()
        if self is None:
            return None
        self.log_path = log_path
        self.feedback_callback = None
        return self

    @_objc_signature(b"v@:@")
    def openLogs_(self, _sender) -> None:
        """Öffnet die Log-Datei im Standard-Viewer."""
        from AppKit import NSWorkspace  # type: ignore[import-not-found]

        log_path = Path(self.log_path)
        created_file = False
        try:
            if not log_path.exists():
                log_path.parent.mkdir(parents=True, exist_ok=True)
                log_path.touch()
                created_file = True
            opened = bool(NSWorkspace.sharedWorkspace().openFile_(str(log_path)))
        except Exception:
            logger.warning("Opening log file failed", exc_info=True)
            if callable(self.feedback_callback):
                self.feedback_callback("Could not open the log file.")
            return

        if callable(self.feedback_callback):
            if opened and created_file:
                self.feedback_callback("Created and opened a new log file.")
            elif opened:
                self.feedback_callback("Opened the log file.")
            else:
                self.feedback_callback("Could not open the log file.")

    @_objc_signature(b"v@:@")
    def showSetup_(self, _sender) -> None:
        """Öffnet das Settings/Welcome-Fenster."""
        if callable(self.feedback_callback):
            self.feedback_callback("Opening Setup & Settings…")
        if self.welcome_callback:
            self.welcome_callback()

    @_objc_signature(b"v@:@")
    def exportDiagnostics_(self, _sender) -> None:
        """Erstellt einen Diagnostics-Report (ohne Audio) und öffnet Finder."""
        try:
            from utils.diagnostics import export_diagnostics_report

            zip_path = export_diagnostics_report()
        except Exception:
            # Diagnostics is best-effort; avoid crashing the menu bar app.
            logger.warning("Diagnostics export failed", exc_info=True)
            if callable(self.feedback_callback):
                self.feedback_callback(
                    "Diagnostics export failed — check the log file and try again."
                )
            return

        if callable(self.feedback_callback):
            archive_name = getattr(zip_path, "name", None) or str(zip_path)
            self.feedback_callback(f"Diagnostics exported: {archive_name}")


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

        title_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "PulseScribe", None, ""
        )
        title_item.setEnabled_(False)
        menu.addItem_(title_item)
        self._menu_title_item = title_item

        state_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            build_menubar_status_text(AppState.IDLE), None, ""
        )
        state_item.setEnabled_(False)
        menu.addItem_(state_item)
        self._menu_status_item = state_item

        hint_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            build_menubar_hint_text(AppState.IDLE), None, ""
        )
        hint_item.setEnabled_(False)
        menu.addItem_(hint_item)
        self._menu_hint_item = hint_item

        menu.addItem_(NSMenuItem.separatorItem())

        setup_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Open Setup & Settings…", "showSetup:", ""
        )
        setup_item.setTarget_(self._action_handler)
        menu.addItem_(setup_item)

        logs_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Open Log File", "openLogs:", ""
        )
        logs_item.setTarget_(self._action_handler)
        menu.addItem_(logs_item)

        diag_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Export Redacted Diagnostics…", "exportDiagnostics:", ""
        )
        diag_item.setTarget_(self._action_handler)
        menu.addItem_(diag_item)

        menu.addItem_(NSMenuItem.separatorItem())

        quit_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Quit PulseScribe", "terminate:", ""
        )
        menu.addItem_(quit_item)

        self._action_handler.feedback_callback = self._set_menu_hint
        self._status_item.setMenu_(menu)

        self._current_state = AppState.IDLE

    def _set_menu_hint(self, text: str) -> None:
        _set_menu_item_title_if_changed(getattr(self, "_menu_hint_item", None), text)

    def update_state(self, state: AppState, text: str | None = None) -> None:
        """Aktualisiert Menübar-Icon und optional Text."""
        self._current_state = state
        title = build_menubar_title(state, text)
        status_text = build_menubar_status_text(state, text)
        hint_text = build_menubar_hint_text(state, text)

        if getattr(self, "_current_title", None) != title:
            self._status_item.setTitle_(title)
            self._current_title = title

        _set_menu_item_title_if_changed(getattr(self, "_menu_status_item", None), status_text)
        _set_menu_item_title_if_changed(getattr(self, "_menu_hint_item", None), hint_text)

    def set_welcome_callback(self, callback) -> None:
        """Setzt Callback für Settings-Menü-Item."""
        self._action_handler.welcome_callback = callback
