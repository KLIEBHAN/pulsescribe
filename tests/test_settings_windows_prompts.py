import pytest

pytest.importorskip("PySide6")

from ui.settings_windows import SettingsWindow


class _FakeEditor:
    def __init__(self, text: str = ""):
        self._text = text
        self.accessible_description = ""

    def toPlainText(self) -> str:
        return self._text

    def setPlainText(self, text: str) -> None:
        self._text = text

    def setAccessibleDescription(self, text: str) -> None:
        self.accessible_description = text


class _FakeCombo:
    def __init__(self, text: str, data: str | None = None):
        self._text = text
        self._data = data

    def currentText(self) -> str:
        return self._text

    def currentData(self) -> str | None:
        return self._data


class _FakeLabel:
    def __init__(self, text: str = ""):
        self._text = text
        self._style = ""

    def text(self) -> str:
        return self._text

    def setText(self, text: str) -> None:
        self._text = text

    def styleSheet(self) -> str:
        return self._style

    def setStyleSheet(self, style: str) -> None:
        self._style = style


class _FakeButton:
    def __init__(self, enabled: bool = True):
        self.enabled = enabled

    def setEnabled(self, enabled: bool) -> None:
        self.enabled = enabled


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


def test_get_prompt_text_for_app_mappings_reuses_cached_formatting(monkeypatch):
    from utils import custom_prompts

    window = _make_window()
    window._prompts_loaded_data = {
        "voice_commands": {"instruction": "default vc"},
        "prompts": {"default": {"prompt": "disk default"}},
        "app_contexts": {"Mail": "email", "Slack": "chat"},
    }

    format_calls: list[dict[str, str]] = []
    original_formatter = custom_prompts.format_app_mappings

    def _tracked_format(mappings: dict[str, str]) -> str:
        format_calls.append(dict(mappings))
        return original_formatter(mappings)

    monkeypatch.setattr(custom_prompts, "format_app_mappings", _tracked_format)

    first = window._get_prompt_text_for_context("app_mappings")
    window._cache_prompt_text("app_mappings", first)
    second = window._get_prompt_text_for_context("app_mappings")

    assert first == second
    assert len(format_calls) == 1


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


def test_selected_prompt_context_normalizes_friendly_labels() -> None:
    window = _make_window()
    window._prompt_context_combo = _FakeCombo("Voice Commands", "voice_commands")

    assert window._selected_prompt_context() == "voice_commands"


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


def test_refresh_prompt_editor_feedback_updates_state_label_buttons_and_accessibility():
    window = _make_window()
    window._prompt_editor = _FakeEditor("edited email")
    window._current_prompt_context = "email"
    window._prompt_context_combo = None
    window._prompt_context_state_label = _FakeLabel()
    window._prompt_save_btn = _FakeButton(enabled=False)
    window._prompt_reset_btn = _FakeButton(enabled=False)
    window._get_saved_prompt_text_for_context = lambda _context: "saved email"
    window._get_prompt_default_text_for_context = lambda _context: "default email"

    window._refresh_prompt_editor_feedback()

    assert window._prompt_context_state_label.text() == "Unsaved changes to Email prompt."
    assert window._prompt_save_btn.enabled is True
    assert window._prompt_reset_btn.enabled is True
    assert "Unsaved changes to Email prompt." in window._prompt_editor.accessible_description


def test_refresh_vocabulary_action_buttons_disables_save_when_editor_is_unchanged():
    window = _make_window()
    window._vocab_editor = _FakeEditor("Alpha\nBeta")
    window._saved_vocabulary_keywords = ["Alpha", "Beta"]
    window._vocabulary_loaded = True
    window._last_vocabulary_signature = (1, 2)
    window._vocab_save_btn = _FakeButton(enabled=True)

    window._refresh_vocabulary_action_buttons()

    assert window._vocab_save_btn.enabled is False


def test_refresh_vocabulary_action_buttons_enables_save_for_unsaved_changes():
    window = _make_window()
    window._vocab_editor = _FakeEditor("Alpha\nGamma")
    window._saved_vocabulary_keywords = ["Alpha", "Beta"]
    window._vocabulary_loaded = True
    window._last_vocabulary_signature = (1, 2)
    window._vocab_save_btn = _FakeButton(enabled=False)

    window._refresh_vocabulary_action_buttons()

    assert window._vocab_save_btn.enabled is True


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
