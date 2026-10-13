"""Qt update window process for Windows.

The Windows daemon starts this module (``PulseScribe.exe --update-window``)
with stdin/stdout pipes. It reads one JSON message per line from stdin:
``{"view": {...}, "activate": true|false|null}``. The first view is complete,
later ones carry only changed fields. ``activate`` shows the window (with or
without focus); ``null`` only re-renders. Button presses are
written to stdout as ``PULSESCRIBE-UPDATE {"action": ...}`` lines; the first
line is ``ready`` once the window exists. The process exits when the window
closes or stdin reaches EOF (daemon gone).

The shipped EXE is a windowed (``console=False``) build, where Python may leave
``sys.stdin``/``sys.stdout`` at ``None``; the pipes are then opened from the
process's standard handles instead.
"""

from __future__ import annotations

import json
import os
import sys
import threading

if __name__ == "__main__":  # Source runs: python ui/update_window_qt.py
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
)

from ui.styles_windows import COLORS, get_settings_stylesheet, ui_font
from ui.update_window_model import PROGRESS_INDETERMINATE, UpdateView

ACTION_PREFIX = "PULSESCRIBE-UPDATE "
WINDOW_WIDTH = 480
WINDOW_HEIGHT_WITH_NOTES = 460
WINDOW_HEIGHT_COMPACT = 170

_EXTRA_STYLES = f"""
QProgressBar {{
    background-color: {COLORS["bg_input"]};
    border: none;
    border-radius: 3px;
}}
QProgressBar::chunk {{
    background-color: {COLORS["accent"]};
    border-radius: 3px;
}}
"""


class UpdateWindow(QDialog):
    """Renders ``UpdateView`` objects; emits ``action`` for button presses."""

    action = Signal(str)

    def __init__(self):
        super().__init__(None, Qt.WindowType.Window | Qt.WindowType.WindowCloseButtonHint)
        self.setWindowTitle("PulseScribe Update")
        self.setStyleSheet(get_settings_stylesheet() + _EXTRA_STYLES)
        self.setFixedWidth(WINDOW_WIDTH)
        self._view: UpdateView | None = None
        self._closing_by_action = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(6)

        self._headline = QLabel()
        headline_font = ui_font("card_title")
        headline_font.setPixelSize(16)
        self._headline.setFont(headline_font)
        self._headline.setWordWrap(True)
        layout.addWidget(self._headline)

        self._detail = QLabel()
        self._detail.setFont(ui_font("body"))
        self._detail.setStyleSheet(f"color: {COLORS['text_secondary']};")
        self._detail.setWordWrap(True)
        layout.addWidget(self._detail)

        self._progress = QProgressBar()
        self._progress.setTextVisible(False)
        self._progress.setFixedHeight(6)
        layout.addWidget(self._progress)

        self._notice = QLabel()
        self._notice.setFont(ui_font("body"))
        self._notice.setStyleSheet(f"color: {COLORS['warning']};")
        self._notice.setWordWrap(True)
        layout.addWidget(self._notice)

        self._notes = QPlainTextEdit()
        self._notes.setReadOnly(True)
        self._notes.setFont(ui_font("body"))
        self._notes.setAccessibleName("Release notes")
        layout.addWidget(self._notes, 1)
        # Without notes (stretch 1) this spacer takes the free height instead
        # of the gaps between the labels.
        layout.addStretch(0)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self._close = QPushButton("Close")
        self._close.clicked.connect(self._on_close_clicked)
        buttons.addWidget(self._close)
        self._primary = QPushButton()
        self._primary.setObjectName("primary")
        self._primary.setMinimumWidth(150)
        self._primary.clicked.connect(self._on_primary_clicked)
        buttons.addWidget(self._primary)
        layout.addLayout(buttons)

    def present(self, view: UpdateView, activate: bool | None) -> None:
        """Render ``view``; ``activate`` True/False also shows the window."""
        self.apply(view)
        if activate is None:
            return
        if activate:
            self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, False)
            self.showNormal()
            self.raise_()
            self.activateWindow()
            return
        if not self.isVisible():
            self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
            self.show()
        QApplication.alert(self)  # Flash the taskbar button without taking focus

    def apply(self, view: UpdateView) -> None:
        """Update only the widgets whose content changed."""
        previous = self._view
        self._view = view
        if previous is None or previous.headline != view.headline:
            self._headline.setText(view.headline)
        if previous is None or previous.detail != view.detail:
            self._detail.setText(view.detail)
            self._detail.setVisible(bool(view.detail))
        if previous is None or previous.notice != view.notice:
            self._notice.setText(view.notice)
            self._notice.setVisible(bool(view.notice))
        if previous is None or previous.progress != view.progress:
            self._apply_progress(view.progress)
        if previous is None or previous.notes != view.notes:
            self._notes.setPlainText(view.notes)
            self._notes.setVisible(bool(view.notes))
            self.layout().activate()  # Drop the hidden notes from the minimum height
            self.resize(
                WINDOW_WIDTH,
                WINDOW_HEIGHT_WITH_NOTES if view.notes else WINDOW_HEIGHT_COMPACT,
            )
        self._primary.setText(view.primary_label)
        self._primary.setVisible(bool(view.primary_label))
        self._primary.setEnabled(view.primary_enabled)
        self._primary.setDefault(bool(view.primary_label))
        self._close.setText(view.close_label)
        self._close.setDefault(not view.primary_label)

    def _apply_progress(self, progress: int | None) -> None:
        if progress is None:
            self._progress.setVisible(False)
            self._progress.setRange(0, 100)  # Stops the busy animation
            return
        self._progress.setVisible(True)
        if progress == PROGRESS_INDETERMINATE:
            self._progress.setRange(0, 0)
            return
        self._progress.setRange(0, 100)
        self._progress.setValue(progress)

    def _on_primary_clicked(self) -> None:
        if self._view is not None and self._view.primary_action:
            self.action.emit(self._view.primary_action)

    def _on_close_clicked(self) -> None:
        self.close()

    def closeEvent(self, event):
        self.action.emit("close")
        super().closeEvent(event)
        QApplication.quit()


class _StdinReader(QObject):
    """Reads daemon messages on a thread and hands them to the GUI thread."""

    message = Signal(dict)
    finished = Signal()

    def start(self, stream) -> None:
        threading.Thread(
            target=self._run, args=(stream,), name="UpdateWindowReader", daemon=True
        ).start()

    def _run(self, stream) -> None:
        try:
            for raw in stream:
                try:
                    data = json.loads(raw.decode("utf-8"))
                except ValueError:
                    continue
                if isinstance(data, dict):
                    self.message.emit(data)
        except OSError:
            pass
        self.finished.emit()


def _pipe(stream, std_handle: int, flags: int, mode: str):
    """Binary pipe from ``stream`` or, if Python has none, the OS std handle."""
    if stream is not None:
        return stream.buffer
    if sys.platform != "win32":
        return None
    import ctypes
    import msvcrt

    get_std_handle = ctypes.windll.kernel32.GetStdHandle
    get_std_handle.restype = ctypes.c_void_p
    get_std_handle.argtypes = [ctypes.c_ulong]
    handle = get_std_handle(std_handle & 0xFFFFFFFF)
    if not handle or handle == ctypes.c_void_p(-1).value:
        return None
    return os.fdopen(msvcrt.open_osfhandle(handle, flags), mode, buffering=0)


def _action_writer(output):
    def write(action: str) -> None:
        line = ACTION_PREFIX + json.dumps({"action": action}) + "\n"
        try:
            output.write(line.encode("utf-8"))
            output.flush()
        except (OSError, ValueError):
            QApplication.quit()  # Daemon gone

    return write


def main() -> int:
    stdin = _pipe(sys.stdin, -10, os.O_RDONLY, "rb")  # STD_INPUT_HANDLE
    stdout = _pipe(sys.stdout, -11, os.O_WRONLY, "wb")  # STD_OUTPUT_HANDLE
    if stdin is None or stdout is None:
        return 2  # The daemon reports the missing "ready" and falls back
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(False)  # closeEvent decides when to quit
    window = UpdateWindow()
    write_action = _action_writer(stdout)
    window.action.connect(write_action)

    fields: dict = {}

    def on_message(data: dict) -> None:
        changes = data.get("view")
        if not isinstance(changes, dict):
            return
        fields.update(changes)
        try:
            view = UpdateView.from_dict(fields)
        except TypeError:
            return  # Incomplete first message
        activate = data.get("activate")
        window.present(view, activate if isinstance(activate, bool) else None)

    reader = _StdinReader()
    reader.message.connect(on_message)
    reader.finished.connect(app.quit)
    reader.start(stdin)
    write_action("ready")
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
