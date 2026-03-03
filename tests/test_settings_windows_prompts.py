import pytest

pytest.importorskip("PySide6")

from ui.settings_windows import SettingsWindow


class _FakeEditor:
    def __init__(self, text: str = ""):
        self._text = text

    def toPlainText(self) -> str:
        return self._text

    def setPlainText(self, text: str) -> None:
        self._text = text


def _make_window() -> SettingsWindow:
    window = SettingsWindow.__new__(SettingsWindow)
    window._prompt_editor = _FakeEditor()
    window._prompt_status = None
    window._prompts_cache = {}
    window._current_prompt_context = "default"
    return window


def test_prompt_context_switch_restores_unsaved_cached_text(monkeypatch):
    from utils import custom_prompts

    window = _make_window()
    window._prompt_editor.setPlainText("draft default")

    monkeypatch.setattr(
        custom_prompts,
        "load_custom_prompts",
        lambda: {
            "prompts": {
                "default": {"prompt": "disk default"},
                "email": {"prompt": "disk email"},
            }
        },
    )

    window._on_prompt_context_changed("email")
    assert window._prompt_editor.toPlainText() == "disk email"

    window._prompt_editor.setPlainText("draft email")
    window._on_prompt_context_changed("default")
    assert window._prompt_editor.toPlainText() == "draft default"


def test_load_prompt_for_context_prefers_cache_over_disk(monkeypatch):
    from utils import custom_prompts

    window = _make_window()
    window._prompts_cache["default"] = "cached default"

    def _fail_load():
        raise AssertionError("disk load should not happen")

    monkeypatch.setattr(
        custom_prompts,
        "load_custom_prompts",
        _fail_load,
    )

    window._load_prompt_for_context("default")
    assert window._prompt_editor.toPlainText() == "cached default"
