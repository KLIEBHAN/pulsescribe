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
    window._prompts_loaded = True
    window._prompts_loaded_data = None
    window._dirty_prompt_contexts = set()
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


def test_save_all_prompts_skips_unloaded_editor(monkeypatch):
    from utils import custom_prompts

    window = _make_window()
    window._prompts_loaded = False

    monkeypatch.setattr(
        custom_prompts,
        "load_custom_prompts",
        lambda: (_ for _ in ()).throw(AssertionError("disk load should not happen")),
    )

    window._save_all_prompts()

    assert window._prompts_cache == {}


def test_save_all_prompts_skips_rewrite_when_prompt_text_is_unchanged(monkeypatch):
    from utils import custom_prompts

    load_calls = {"count": 0}
    save_calls: list[dict] = []

    def fake_load():
        load_calls["count"] += 1
        return {
            "voice_commands": {"instruction": "voice default"},
            "prompts": {"default": {"prompt": "disk default"}},
            "app_contexts": {"Mail": "email"},
        }

    monkeypatch.setattr(custom_prompts, "load_custom_prompts", fake_load)
    monkeypatch.setattr(
        custom_prompts,
        "save_custom_prompts_state",
        lambda data: save_calls.append(data),
    )

    window = _make_window()
    window._load_prompt_for_context("default")
    window._save_all_prompts()

    assert load_calls["count"] == 1
    assert save_calls == []
    assert window._dirty_prompt_contexts == set()


def test_save_current_prompt_reuses_returned_saved_state(monkeypatch):
    from utils import custom_prompts

    save_calls: list[dict] = []
    saved_state = {
        "voice_commands": {"instruction": "default vc"},
        "prompts": {
            "default": {"prompt": "edited default"},
            "email": {"prompt": "custom email"},
        },
        "app_contexts": {"Mail": "email"},
    }

    monkeypatch.setattr(
        custom_prompts,
        "save_custom_prompts_state",
        lambda data: save_calls.append(data) or saved_state,
    )

    window = _make_window()
    window._prompt_editor = _FakeEditor("edited default")
    window._prompt_context_combo = None
    window._prompts_loaded_data = {
        "voice_commands": {"instruction": "default vc"},
        "prompts": {
            "default": {"prompt": "disk default"},
            "email": {"prompt": "custom email"},
        },
        "app_contexts": {"Mail": "email"},
    }

    window._save_current_prompt()

    assert len(save_calls) == 1
    assert save_calls[0]["prompts"] == {
        "default": {"prompt": "edited default"},
        "email": {"prompt": "custom email"},
    }
    assert window._prompts_loaded_data == saved_state
    assert window._dirty_prompt_contexts == set()


def test_save_all_prompts_saves_only_dirty_contexts_and_keeps_existing_overrides(
    monkeypatch,
):
    from utils import custom_prompts

    save_calls: list[dict] = []
    saved_state = {
        "voice_commands": {"instruction": "default vc"},
        "prompts": {
            "default": {"prompt": "edited default"},
            "email": {"prompt": "custom email"},
        },
        "app_contexts": {"Mail": "email"},
    }

    monkeypatch.setattr(
        custom_prompts,
        "save_custom_prompts_state",
        lambda data: save_calls.append(data) or saved_state,
    )

    window = _make_window()
    window._prompt_editor = _FakeEditor("edited default")
    window._prompts_loaded_data = {
        "voice_commands": {"instruction": "default vc"},
        "prompts": {
            "default": {"prompt": "disk default"},
            "email": {"prompt": "custom email"},
        },
        "app_contexts": {"Mail": "email"},
    }

    window._save_all_prompts()

    assert len(save_calls) == 1
    assert save_calls[0]["prompts"] == {
        "default": {"prompt": "edited default"},
        "email": {"prompt": "custom email"},
    }
    assert window._prompts_loaded_data == saved_state
    assert window._dirty_prompt_contexts == set()
