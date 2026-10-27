from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

from ui.settings_windows import SettingsWindow


class _Editor:
    def __init__(self, text):
        self.text = text

    def toPlainText(self):
        return self.text


@pytest.fixture(scope="module")
def qt_app():
    app = QApplication.instance() or QApplication([])
    yield app


def _window():
    window = SettingsWindow.__new__(SettingsWindow)
    window._is_closed = False
    window._saved_settings_signature = ("saved",)
    window._get_current_settings_signature = lambda: ("saved",)
    window._prompts_loaded = False
    window._prompts_cache = {}
    window._dirty_prompt_contexts = set()
    window._prompt_editor = None
    window._current_prompt_context = "default"
    window._vocabulary_loaded = False
    window._vocab_editor = None
    window._saved_vocabulary_keywords = []
    return window


def test_close_check_does_not_load_unopened_editors(monkeypatch):
    window = _window()
    monkeypatch.setattr(
        window, "_get_loaded_prompts_data", lambda: pytest.fail("must not load prompts")
    )
    monkeypatch.setattr(
        window,
        "_get_saved_vocabulary_keywords",
        lambda: pytest.fail("must not load vocabulary"),
    )

    assert window._has_unsaved_changes() is False


@pytest.mark.parametrize(
    "section", ["settings", "current_prompt", "other_prompt", "vocabulary"]
)
def test_close_check_detects_changes_in_each_store(section):
    window = _window()
    if section == "settings":
        window._get_current_settings_signature = lambda: ("edited",)
    elif section == "current_prompt":
        window._prompts_loaded = True
        window._prompt_editor = _Editor("unsaved")
        window._get_prompt_text_for_context = lambda _context: "saved"
    elif section == "other_prompt":
        window._dirty_prompt_contexts = {"email"}
    else:
        window._vocabulary_loaded = True
        window._vocab_editor = _Editor("unsaved")
        window._saved_vocabulary_keywords = ["saved"]

    assert window._has_unsaved_changes() is True


def test_close_check_ignores_cosmetic_vocabulary_changes():
    window = _window()
    window._vocabulary_loaded = True
    window._vocab_editor = _Editor(" first, second\n")
    window._saved_vocabulary_keywords = ["first", "second"]

    assert window._has_unsaved_changes() is False


def test_close_check_drops_reverted_current_prompt_dirtiness():
    window = _window()
    window._prompts_loaded = True
    window._prompt_editor = _Editor("saved")
    window._get_prompt_text_for_context = lambda _context: "saved"
    window._dirty_prompt_contexts = {"default"}

    assert window._has_unsaved_changes() is False


@pytest.mark.parametrize("already_closed", [True, False])
def test_clean_or_closed_window_does_not_prompt(monkeypatch, already_closed):
    window = _window()
    window._is_closed = already_closed
    monkeypatch.setattr(
        QMessageBox, "warning", lambda *_args: pytest.fail("must not show a dialog")
    )
    if already_closed:
        window._has_unsaved_changes = lambda: pytest.fail("already closed")

    assert window._confirm_close_if_needed() is True


@pytest.mark.parametrize(
    ("choice", "saved", "allow_close"),
    [
        (QMessageBox.StandardButton.Save, True, True),
        (QMessageBox.StandardButton.Save, False, False),
        (QMessageBox.StandardButton.Discard, False, True),
        (QMessageBox.StandardButton.Cancel, False, False),
    ],
)
def test_close_confirmation_respects_choice_and_save_outcome(
    monkeypatch, choice, saved, allow_close
):
    window = _window()
    window._has_unsaved_changes = lambda: True
    saved_calls = []

    def save():
        saved_calls.append(True)
        if saved:
            window._has_unsaved_changes = lambda: False
        return saved

    window._save_settings = save
    dialogs = []

    def warning(*args):
        dialogs.append(args)
        return choice

    monkeypatch.setattr(QMessageBox, "warning", warning)

    assert window._confirm_close_if_needed() is allow_close
    assert len(saved_calls) == (1 if choice == QMessageBox.StandardButton.Save else 0)
    assert dialogs[0][-1] == QMessageBox.StandardButton.Cancel
    assert window._is_closed is False


def test_save_cannot_close_over_edits_in_an_unread_vocabulary(monkeypatch):
    window = _window()
    window._vocab_editor = _Editor("unsaved")
    window._save_settings = lambda: True
    statuses = []
    window._set_footer_status = lambda *args: statuses.append(args)
    monkeypatch.setattr(
        QMessageBox, "warning", lambda *_args: QMessageBox.StandardButton.Save
    )

    assert window._confirm_close_if_needed() is False
    assert statuses == [
        ("Some changes are still unsaved. Review them before closing.", "warning")
    ]
    assert window._vocab_editor.toPlainText() == "unsaved"


@pytest.mark.parametrize("path", ["reject", "close"])
@pytest.mark.parametrize("discard", [True, False])
def test_native_qt_close_prompts_once_and_cancel_keeps_window_open(
    qt_app, monkeypatch, path, discard
):
    window = SettingsWindow.__new__(SettingsWindow)
    QDialog.__init__(window)
    window._is_closed = False
    window._has_unsaved_changes = lambda: True
    window._update_logs_auto_refresh_state = lambda: None
    window._stop_pynput_listener = lambda: None
    window._recording_hotkey_for = None
    closed = []
    window.closed.connect(lambda: closed.append(True))
    dialogs = []

    def choose(*args):
        dialogs.append(args)
        return (
            QMessageBox.StandardButton.Discard
            if discard
            else QMessageBox.StandardButton.Cancel
        )

    monkeypatch.setattr(QMessageBox, "warning", choose)
    try:
        window.show()
        getattr(window, path)()
        assert len(dialogs) == 1
        assert window.isVisible() is (not discard)
        assert closed == ([True] if discard else [])
    finally:
        window._confirm_close_if_needed = lambda: True
        window.close()
        window.deleteLater()
        qt_app.processEvents()


@pytest.mark.parametrize("path", ["reject", "closeEvent"])
@pytest.mark.parametrize("allow_close", [True, False])
def test_every_close_path_keeps_resources_until_close_is_approved(
    monkeypatch, path, allow_close
):
    window = _window()
    window._confirm_close_if_needed = lambda: allow_close
    calls = []
    window._logs_refresh_timer = SimpleNamespace(stop=lambda: calls.append("timer"))
    window._stop_pynput_listener = lambda: calls.append("listener")
    window._recording_hotkey_for = "toggle"
    window.closed = SimpleNamespace(emit=lambda: calls.append("closed"))
    event = SimpleNamespace(ignore=lambda: calls.append("ignored"))
    monkeypatch.setattr(QDialog, "reject", lambda self: calls.append("qt"))
    monkeypatch.setattr(QDialog, "closeEvent", lambda self, event: calls.append("qt"))

    if path == "reject":
        window.reject()
    else:
        window.closeEvent(event)

    if allow_close:
        assert calls == ["timer", "listener", "closed", "qt"]
        assert window._is_closed is True
        assert window._recording_hotkey_for is None
    else:
        assert calls == (["ignored"] if path == "closeEvent" else [])
        assert window._is_closed is False
        assert window._recording_hotkey_for == "toggle"
