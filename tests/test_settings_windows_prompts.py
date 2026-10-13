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



def test_cache_prompt_text_treats_comment_only_app_mapping_edits_as_clean() -> None:
    window = _make_window()
    baseline = "# App → Context Mappings (one per line: AppName = context)\nMail = email"

    window._cache_prompt_text(
        "app_mappings",
        baseline + "\n# personal note",
        baseline_text=baseline,
    )

    assert window._dirty_prompt_contexts == set()


@pytest.mark.parametrize(
    "draft, saved, default, dirty, reset, expected_text",
    [
        (
            "Mail = email\n# note", "Mail = email", "Mail = email", False, False,
            "Using the built-in App Mappings.",
        ),
        (
            "Mail = chat", "Mail = email", "Mail = email", True, True,
            "Unsaved custom App Mappings. Save to keep this override.",
        ),
        (
            "Mail = email", "Mail = chat", "Mail = email", True, False,
            "Built-in App Mappings restored here. Save to remove the custom version.",
        ),
        (
            "Mail = chat", "Mail = chat", "Mail = email", False, True,
            "Using a saved custom App Mappings.",
        ),
        (
            "Mail = code", "Mail = chat", "Mail = email", True, True,
            "Unsaved changes to App Mappings.",
        ),
        (
            "Broken Line", "# saved", "# default", False, False,
            "Using the built-in App Mappings. Only 'App = context' lines are saved.",
        ),
    ],
)
def test_mapping_edit_shares_one_analysis_for_cache_and_ui(
    monkeypatch, draft, saved, default, dirty, reset, expected_text
):
    import ui.prompt_editor_feedback as feedback_mod
    from utils import custom_prompts

    class CountingEditor(_FakeEditor):
        reads = 0

        def toPlainText(self):
            self.reads += 1
            return super().toPlainText()

    window = _make_window()
    window._current_prompt_context = "app_mappings"
    window._prompt_editor = CountingEditor(draft)
    window._prompt_context_state_label = _FakeLabel()
    window._prompt_save_btn = _FakeButton()
    window._prompt_reset_btn = _FakeButton()
    window._get_prompt_text_for_context = lambda _context: saved
    window._get_prompt_default_text_for_context = lambda _context: default
    window._dirty_prompt_contexts.add("app_mappings")
    parsed = []
    original_parse = custom_prompts.parse_app_mappings

    def parse(text):
        parsed.append(text)
        return original_parse(text)

    monkeypatch.setattr(custom_prompts, "parse_app_mappings", parse)
    monkeypatch.setattr(feedback_mod, "parse_app_mappings", parse)

    window._on_prompt_editor_text_changed()

    assert window._prompt_editor.reads == 1
    assert len(parsed) == len(set([draft, saved, default]))
    assert set(parsed) == {draft, saved, default}
    assert window._prompts_cache == {"app_mappings": draft}
    assert ("app_mappings" in window._dirty_prompt_contexts) is dirty
    assert window._prompt_save_btn.enabled is dirty
    assert window._prompt_reset_btn.enabled is reset
    assert window._prompt_context_state_label.text() == expected_text
    assert expected_text in window._prompt_editor.accessible_description


def test_comment_only_mapping_edit_does_not_persist(monkeypatch):
    from utils import custom_prompts

    window = _make_window()
    window._current_prompt_context = "app_mappings"
    window._prompts_loaded_data = {"app_contexts": {"Mail": "email"}}
    baseline = window._get_prompt_text_for_context("app_mappings")
    window._prompt_editor.setPlainText(baseline + "\n# note")
    monkeypatch.setattr(
        custom_prompts,
        "save_custom_prompts_state",
        lambda _data: pytest.fail("A semantic no-op must not rewrite prompts.toml"),
    )

    window._on_prompt_editor_text_changed()
    window._save_current_prompt()

    assert window._dirty_prompt_contexts == set()
    assert window._prompts_cache["app_mappings"] == baseline + "\n# note"


@pytest.mark.parametrize("initially_dirty", [False, True])
@pytest.mark.parametrize(
    "context, draft, saved, default, dirty, reset",
    [
        ("email", "saved email", "saved email", "default email", False, True),
        ("email", "default email", "saved email", "default email", True, False),
        ("voice_commands", "new commands", "old commands", "built-in", True, True),
        ("default", "", "custom prompt", "built-in", True, True),
    ],
)
def test_prompt_edit_keeps_dirty_state_and_buttons_consistent(
    context, draft, saved, default, dirty, reset, initially_dirty
):
    window = _make_window()
    window._current_prompt_context = context
    window._prompt_editor.setPlainText(draft)
    window._prompt_save_btn = _FakeButton()
    window._prompt_reset_btn = _FakeButton()
    window._get_prompt_text_for_context = lambda _context: saved
    window._get_prompt_default_text_for_context = lambda _context: default
    if initially_dirty:
        window._dirty_prompt_contexts.add(context)

    window._on_prompt_editor_text_changed()

    assert window._prompts_cache[context] == draft
    assert (context in window._dirty_prompt_contexts) is dirty
    assert window._prompt_save_btn.enabled is dirty
    assert window._prompt_reset_btn.enabled is reset



@pytest.mark.parametrize(
    "context, draft, dirty, reset, expected_text",
    [
        (
            "email", "saved email", False, True,
            "Using a saved custom Email prompt.",
        ),
        (
            "email", "built-in email", True, False,
            "Built-in Email prompt restored here. Save to remove the custom version.",
        ),
        (
            "email", "another email", True, True,
            "Unsaved changes to Email prompt.",
        ),
        (
            "app_mappings", '"Mail" = email # note', False, True,
            "Using a saved custom App Mappings.",
        ),
        (
            "app_mappings", "Mail = chat\n# note", True, False,
            "Built-in App Mappings restored here. Save to remove the custom version.",
        ),
        (
            "app_mappings", "Slack = chat", True, True,
            "Unsaved changes to App Mappings.",
        ),
    ],
)
def test_edit_and_refresh_agree_when_clean_cache_differs_from_loaded_prompts(
    context, draft, dirty, reset, expected_text
):
    window = _make_window()
    window._current_prompt_context = context
    window._prompts_loaded_data = {
        "prompts": {"email": {"prompt": "saved email"}},
        "app_contexts": {"Mail": "email"},
    }
    window._prompts_defaults_data = {
        "prompts": {"email": {"prompt": "built-in email"}},
        "app_contexts": {"Mail": "chat"},
    }
    window._prompts_cache[context] = window._get_prompt_default_text_for_context(context)
    window._prompt_editor.setPlainText(draft)
    window._prompt_context_state_label = _FakeLabel()
    window._prompt_save_btn = _FakeButton()
    window._prompt_reset_btn = _FakeButton()
    assert window._get_saved_prompt_text_for_context(context) != (
        window._get_prompt_text_for_context(context)
    )

    window._on_prompt_editor_text_changed()

    assert window._prompts_cache[context] == draft
    assert (context in window._dirty_prompt_contexts) is dirty
    assert window._prompt_context_state_label.text() == expected_text
    assert window._prompt_save_btn.enabled is dirty
    assert window._prompt_reset_btn.enabled is reset
    guidance = window._prompt_editor.accessible_description

    window._refresh_prompt_editor_feedback()

    assert (context in window._dirty_prompt_contexts) is dirty
    assert window._prompt_context_state_label.text() == expected_text
    assert window._prompt_save_btn.enabled is dirty
    assert window._prompt_reset_btn.enabled is reset
    assert window._prompt_editor.accessible_description == guidance


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


@pytest.mark.parametrize(
    ("loaded", "raw_text", "write_ok", "expected_saves", "expected_result"),
    [
        (False, "Alpha\nGamma", True, 0, True),  # never overwrite an unread file
        (True, "Alpha\nBeta", True, 0, True),
        (True, "Alpha\nGamma", True, 1, True),
        (True, "Alpha\nGamma", False, 1, False),  # failed write is reported
    ],
)
def test_save_vocabulary_if_changed_matches_macos_save_and_apply(
    loaded, raw_text, write_ok, expected_saves, expected_result
):
    window = _make_window()
    window._vocab_editor = _FakeEditor(raw_text)
    window._saved_vocabulary_keywords = ["Alpha", "Beta"]
    window._vocabulary_loaded = loaded
    saves: list[bool] = []
    window._save_vocabulary = lambda: saves.append(True) or write_ok

    result = window._save_vocabulary_if_changed()

    assert len(saves) == expected_saves
    assert result is expected_result


def test_save_vocabulary_returns_false_when_write_fails(monkeypatch):
    import utils.vocabulary as vocabulary

    def fail(_keywords):
        raise OSError("disk full")

    monkeypatch.setattr(vocabulary, "save_vocabulary_state", fail)
    window = _make_window()
    window._vocab_editor = _FakeEditor("Alpha\nGamma")
    window._vocab_status = _FakeLabel()
    window._saved_vocabulary_keywords = ["Alpha", "Beta"]
    window._vocabulary_loaded = True
    window._last_vocabulary_signature = (1, 2)

    assert window._save_vocabulary() is False
    assert "Could not save the vocabulary" in window._vocab_status.text()


@pytest.mark.parametrize("raw_text", ["", "Alpha\nBeta", "Alpha\nalpha\nGamma"])
def test_vocabulary_edit_reads_and_analyzes_input_once(monkeypatch, raw_text):
    import ui.settings_windows as settings_mod
    from utils.vocabulary import analyze_vocabulary_text

    class CountingEditor(_FakeEditor):
        reads = 0

        def toPlainText(self):
            self.reads += 1
            return super().toPlainText()

    window = _make_window()
    window._vocab_editor = CountingEditor(raw_text)
    window._vocab_status = _FakeLabel()
    window._saved_vocabulary_keywords = ["Alpha", "Beta"]
    window._vocabulary_loaded = True
    window._vocab_save_btn = _FakeButton()
    analyzed = []

    def analyze(text):
        analyzed.append(text)
        return analyze_vocabulary_text(text)

    monkeypatch.setattr(settings_mod, "analyze_vocabulary_text", analyze)

    window._refresh_vocabulary_editor_feedback()

    assert window._vocab_editor.reads == 1
    assert analyzed == [raw_text]
    assert window._vocab_save_btn.enabled == (raw_text != "Alpha\nBeta")


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
