import pytest

pytest.importorskip("PySide6")

import ui.settings_windows as settings_mod
from ui.settings_windows import SettingsWindow


class _FakeCheckbox:
    def __init__(self, checked: bool | None = None):
        self.checked: bool | None = None
        self.set_calls = 0
        if checked is not None:
            self.checked = checked

    def setChecked(self, value: bool) -> None:
        self.checked = value
        self.set_calls += 1

    def isChecked(self) -> bool:
        return bool(self.checked)


class _FakeField:
    def __init__(self, value: str = ""):
        self.value = ""
        self.set_calls = 0
        self.value = value

    def setText(self, value: str) -> None:
        self.value = value
        self.set_calls += 1

    def text(self) -> str:
        return self.value


class _FakeEditor:
    def __init__(self, text: str = ""):
        self.value = text
        self.set_calls: list[str] = []

    def setPlainText(self, value: str) -> None:
        self.value = value
        self.set_calls.append(value)

    def toPlainText(self) -> str:
        return self.value


class _FakeLabel:
    def __init__(self):
        self.text = ""
        self.style = ""
        self.text_calls = 0
        self.style_calls = 0

    def setText(self, value: str) -> None:
        self.text = value
        self.text_calls += 1

    def setStyleSheet(self, value: str) -> None:
        self.style = value
        self.style_calls += 1


class _FakeVisibleWidget:
    def __init__(self):
        self.visible: bool | None = None

    def setVisible(self, value: bool) -> None:
        self.visible = value


class _FakePreShowVisibleWidget:
    """Mimics Qt widgets before first show: not hidden, but isVisible() is False."""

    def __init__(self, *, hidden: bool = False, visible: bool = False):
        self.hidden = hidden
        self.visible = visible
        self.set_calls: list[bool] = []

    def isHidden(self) -> bool:
        return self.hidden

    def isVisible(self) -> bool:
        return self.visible

    def setVisible(self, value: bool) -> None:
        self.hidden = not value
        self.visible = value
        self.set_calls.append(value)


class _FakeSlider:
    def __init__(self, value: int):
        self._value = value
        self.set_calls = 0

    def setValue(self, value: int) -> None:
        self._value = value
        self.set_calls += 1

    def value(self) -> int:
        return self._value


class _FakeCombo:
    def __init__(self, items: list[str], current: str = "default"):
        self._items = list(items)
        self._current_index = 0
        self.set_calls = 0
        self.find_calls = 0
        if current in self._items:
            self._current_index = self._items.index(current)

    def addItems(self, items: list[str]) -> None:
        self._items.extend(items)

    def count(self) -> int:
        return len(self._items)

    def itemText(self, index: int) -> str:
        return self._items[index]

    def findText(self, value: str) -> int:
        self.find_calls += 1
        try:
            return self._items.index(value)
        except ValueError:
            return -1

    def setCurrentIndex(self, index: int) -> None:
        self._current_index = index
        self.set_calls += 1

    def currentText(self) -> str:
        return self._items[self._current_index]


class _FakeSignal:
    def __init__(self):
        self.emitted = False

    def emit(self) -> None:
        self.emitted = True


class _FakeLayout:
    def __init__(self):
        self.widgets: list[object] = []

    def addWidget(self, widget: object) -> None:
        self.widgets.append(widget)


class _FakeTabs:
    def __init__(self, label: str):
        self._label = label

    def tabText(self, _index: int) -> str:
        return self._label


def _make_window() -> SettingsWindow:
    window = SettingsWindow.__new__(SettingsWindow)
    window._mode_combo = None
    window._lang_combo = None
    window._local_backend_combo = None
    window._local_model_combo = None
    window._fp16_combo = _FakeCombo(["default", "true", "false"])
    window._streaming_checkbox = _FakeCheckbox()
    window._refine_checkbox = _FakeCheckbox()
    window._refine_provider_combo = None
    window._refine_model_field = None
    window._overlay_checkbox = _FakeCheckbox()
    window._rtf_checkbox = _FakeCheckbox()
    window._clipboard_restore_checkbox = _FakeCheckbox()
    window._toggle_hotkey_field = _FakeField()
    window._hold_hotkey_field = _FakeField()
    window._api_fields = {}
    window._api_status = {}
    window._on_mode_changed = lambda _mode: None
    return window


def _make_save_window(fp16_value: str) -> SettingsWindow:
    window = SettingsWindow.__new__(SettingsWindow)
    window._mode_combo = None
    window._lang_combo = None
    window._local_backend_combo = None
    window._local_model_combo = None
    window._streaming_checkbox = None
    window._refine_checkbox = None
    window._refine_provider_combo = None
    window._refine_model_field = None
    window._overlay_checkbox = None
    window._rtf_checkbox = None
    window._clipboard_restore_checkbox = None
    window._api_fields = {}
    window._api_status = {}
    window._fp16_combo = _FakeCombo(["default", "true", "false"], current=fp16_value)
    window._validate_hotkeys_for_save = lambda: ("ctrl+alt+r", "")
    window._save_all_prompts = lambda: None
    window._write_reload_signal = lambda: None
    window._show_save_feedback = lambda: None
    window._on_settings_changed_callback = None
    window.settings_changed = _FakeSignal()
    return window


def test_load_settings_parses_common_bool_variants(monkeypatch):
    values = {
        "PULSESCRIBE_STREAMING": "FALSE",
        "PULSESCRIBE_REFINE": "YES",
        "PULSESCRIBE_OVERLAY": "0",
        "PULSESCRIBE_SHOW_RTF": "ON",
        "PULSESCRIBE_CLIPBOARD_RESTORE": "off",
    }
    monkeypatch.setattr(settings_mod, "read_env_file", lambda: dict(values))

    window = _make_window()
    window._load_settings()

    assert window._streaming_checkbox.checked is False
    assert window._refine_checkbox.checked is True
    assert window._overlay_checkbox.checked is False
    assert window._rtf_checkbox.checked is True
    assert window._clipboard_restore_checkbox.checked is False


def test_load_settings_uses_defaults_for_missing_or_invalid_bool_values(monkeypatch):
    values = {
        "PULSESCRIBE_STREAMING": "invalid",
        "PULSESCRIBE_REFINE": None,
        "PULSESCRIBE_OVERLAY": "",
        "PULSESCRIBE_SHOW_RTF": "maybe",
        "PULSESCRIBE_CLIPBOARD_RESTORE": None,
    }
    monkeypatch.setattr(settings_mod, "read_env_file", lambda: dict(values))

    window = _make_window()
    window._load_settings()

    assert window._streaming_checkbox.checked is True
    assert window._refine_checkbox.checked is False
    assert window._overlay_checkbox.checked is True
    assert window._rtf_checkbox.checked is False
    assert window._clipboard_restore_checkbox.checked is False


def test_load_settings_uses_default_hotkeys_when_none_are_configured(monkeypatch):
    monkeypatch.setattr(settings_mod, "read_env_file", lambda: {})

    window = _make_window()
    window._load_settings()

    assert (
        window._toggle_hotkey_field.value
        == settings_mod.DEFAULT_WINDOWS_TOGGLE_HOTKEY
    )
    assert window._hold_hotkey_field.value == settings_mod.DEFAULT_WINDOWS_HOLD_HOTKEY


def test_load_settings_populates_api_fields_from_process_env(monkeypatch):
    monkeypatch.setattr(settings_mod, "read_env_file", lambda: {})
    monkeypatch.setenv("DEEPGRAM_API_KEY", "dg-live-key")

    window = _make_window()
    field = _FakeField()
    status = _FakeLabel()
    window._api_fields = {"DEEPGRAM_API_KEY": field}
    window._api_status = {"DEEPGRAM_API_KEY": status}

    window._load_settings()

    assert field.value == "dg-live-key"
    assert status.text == "Configured"


def test_load_settings_skips_no_op_widget_mutations(monkeypatch):
    values = {
        "PULSESCRIBE_MODE": "deepgram",
        "PULSESCRIBE_LANGUAGE": "auto",
        "PULSESCRIBE_STREAMING": "true",
        "PULSESCRIBE_OVERLAY": "true",
        "PULSESCRIBE_SHOW_RTF": "false",
        "PULSESCRIBE_CLIPBOARD_RESTORE": "false",
        "DEEPGRAM_API_KEY": "dg-live-key",
    }
    monkeypatch.setattr(settings_mod, "read_env_file", lambda: dict(values))

    mode_changes: list[str] = []
    window = _make_window()
    window._mode_combo = _FakeCombo(settings_mod.MODE_OPTIONS, current="deepgram")
    window._lang_combo = _FakeCombo(settings_mod.LANGUAGE_OPTIONS, current="auto")
    window._streaming_checkbox = _FakeCheckbox(True)
    window._overlay_checkbox = _FakeCheckbox(True)
    window._rtf_checkbox = _FakeCheckbox(False)
    window._clipboard_restore_checkbox = _FakeCheckbox(False)
    window._toggle_hotkey_field = _FakeField(
        settings_mod.DEFAULT_WINDOWS_TOGGLE_HOTKEY
    )
    window._hold_hotkey_field = _FakeField(settings_mod.DEFAULT_WINDOWS_HOLD_HOTKEY)
    window._api_fields = {"DEEPGRAM_API_KEY": _FakeField("dg-live-key")}
    window._api_status = {"DEEPGRAM_API_KEY": _FakeLabel()}
    window._api_status["DEEPGRAM_API_KEY"].text = "Configured"
    window._api_status["DEEPGRAM_API_KEY"].style = f"color: {settings_mod.COLORS['success']};"
    window._on_mode_changed = lambda mode: mode_changes.append(mode)

    window._load_settings()

    assert window._mode_combo.set_calls == 0
    assert window._lang_combo.set_calls == 0
    assert window._streaming_checkbox.set_calls == 0
    assert window._overlay_checkbox.set_calls == 0
    assert window._rtf_checkbox.set_calls == 0
    assert window._clipboard_restore_checkbox.set_calls == 0
    assert window._toggle_hotkey_field.set_calls == 0
    assert window._hold_hotkey_field.set_calls == 0
    assert window._api_fields["DEEPGRAM_API_KEY"].set_calls == 0
    assert window._api_status["DEEPGRAM_API_KEY"].text_calls == 0
    assert window._api_status["DEEPGRAM_API_KEY"].style_calls == 0
    assert mode_changes == ["deepgram"]


def test_load_settings_uses_applied_combo_mode_when_env_mode_is_invalid(monkeypatch):
    monkeypatch.setattr(
        settings_mod,
        "read_env_file",
        lambda: {"PULSESCRIBE_MODE": "bogus"},
    )

    mode_changes: list[str] = []
    window = _make_window()
    window._mode_combo = _FakeCombo(settings_mod.MODE_OPTIONS, current="deepgram")
    window._on_mode_changed = lambda mode: mode_changes.append(mode)

    window._load_settings()

    assert window._mode_combo.currentText() == "deepgram"
    assert mode_changes == ["deepgram"]


def test_load_settings_uses_cached_combo_indices_for_bulk_updates(monkeypatch):
    monkeypatch.setattr(
        settings_mod,
        "read_env_file",
        lambda: {
            "PULSESCRIBE_MODE": "local",
            "PULSESCRIBE_LANGUAGE": "de",
            "PULSESCRIBE_LOCAL_BACKEND": "lightning",
            "PULSESCRIBE_LOCAL_MODEL": "large-v3",
            settings_mod.LOCAL_FP16_ENV_KEY: "true",
        },
    )

    window = _make_window()
    window._mode_combo = _FakeCombo(settings_mod.MODE_OPTIONS, current="deepgram")
    window._lang_combo = _FakeCombo(settings_mod.LANGUAGE_OPTIONS, current="auto")
    window._local_backend_combo = _FakeCombo(
        settings_mod.LOCAL_BACKEND_OPTIONS,
        current="auto",
    )
    window._local_model_combo = _FakeCombo(
        settings_mod.LOCAL_MODEL_OPTIONS,
        current="default",
    )
    window._on_mode_changed = lambda _mode: None

    window._load_settings()

    assert window._mode_combo.currentText() == "local"
    assert window._lang_combo.currentText() == "de"
    assert window._local_backend_combo.currentText() == "lightning"
    assert window._local_model_combo.currentText() == "large-v3"
    assert window._fp16_combo.currentText() == "true"
    assert window._mode_combo.find_calls == 0
    assert window._lang_combo.find_calls == 0
    assert window._local_backend_combo.find_calls == 0
    assert window._local_model_combo.find_calls == 0
    assert window._fp16_combo.find_calls == 0


def test_refresh_setup_overview_uses_process_env_api_keys(monkeypatch):
    monkeypatch.setenv("DEEPGRAM_API_KEY", "dg-live-key")

    window = SettingsWindow.__new__(SettingsWindow)
    window._setup_status_label = _FakeLabel()
    window._setup_status_detail_label = _FakeLabel()
    window._setup_howto_label = _FakeLabel()
    window._mode_combo = _FakeCombo(["deepgram"], current="deepgram")
    window._toggle_hotkey_field = _FakeField()
    window._toggle_hotkey_field.setText("ctrl+alt+r")
    window._hold_hotkey_field = _FakeField()
    window._api_fields = {"DEEPGRAM_API_KEY": None}
    window._process_env_api_keys = None

    window._refresh_setup_overview()

    assert window._setup_status_label.text == "Ready to Dictate"
    assert "Deepgram" in window._setup_status_detail_label.text


def test_refresh_provider_key_statuses_marks_current_mode_key_as_required():
    window = SettingsWindow.__new__(SettingsWindow)
    window._mode_combo = _FakeCombo(settings_mod.MODE_OPTIONS, current="deepgram")
    window._api_fields = {
        "DEEPGRAM_API_KEY": _FakeField(""),
        "GROQ_API_KEY": _FakeField("grq-live"),
    }
    window._api_status = {
        "DEEPGRAM_API_KEY": _FakeLabel(),
        "GROQ_API_KEY": _FakeLabel(),
    }
    window._provider_guidance_label = _FakeLabel()
    window._process_env_api_keys = {}
    window._last_provider_key_status_snapshot = None

    window._refresh_provider_key_statuses()

    assert window._provider_guidance_label.text.startswith("Deepgram is selected")
    assert window._api_status["DEEPGRAM_API_KEY"].text == "Required"
    assert window._api_status["GROQ_API_KEY"].text == "Configured"


def test_ensure_tab_built_builds_lazy_tab_only_once():
    window = SettingsWindow.__new__(SettingsWindow)
    built: list[str] = []
    layout = _FakeLayout()

    window._tab_builders = {"Logs": lambda: built.append("logs") or "logs-widget"}
    window._lazy_tab_layouts = {"Logs": layout}
    window._built_tabs = set()

    assert window._ensure_tab_built("Logs") is True
    assert window._ensure_tab_built("Logs") is False
    assert built == ["logs"]
    assert layout.widgets == ["logs-widget"]


def test_on_tab_changed_builds_lazy_prompts_tab_before_loading():
    window = SettingsWindow.__new__(SettingsWindow)
    built: list[str] = []
    prompt_calls: list[str] = []

    window._tabs = _FakeTabs("Prompts")
    window._ensure_tab_built = lambda label: built.append(label) or True
    window._ensure_prompts_loaded = lambda: prompt_calls.append("prompts")
    window._load_vocabulary = lambda **_kwargs: (_ for _ in ()).throw(
        AssertionError("vocabulary should not load")
    )
    window._refresh_logs = lambda: (_ for _ in ()).throw(
        AssertionError("logs should not refresh")
    )
    window._refresh_transcripts = lambda: (_ for _ in ()).throw(
        AssertionError("transcripts should not refresh")
    )
    window._update_logs_auto_refresh_state = lambda: None
    window._logs_stack = None

    window._on_tab_changed(0)

    assert built == ["Prompts"]
    assert prompt_calls == ["prompts"]


def test_load_vocabulary_skips_reload_when_signature_unchanged(monkeypatch):
    from utils import vocabulary as vocabulary_mod

    window = SettingsWindow.__new__(SettingsWindow)
    window._vocab_editor = _FakeEditor("draft keyword")
    window._vocab_status = _FakeLabel()
    window._vocabulary_loaded = True
    window._last_vocabulary_signature = (7, 9)

    monkeypatch.setattr(settings_mod, "get_file_signature", lambda _path: (7, 9))
    monkeypatch.setattr(
        vocabulary_mod,
        "load_vocabulary_state",
        lambda: (_ for _ in ()).throw(AssertionError("disk load should not happen")),
    )

    window._load_vocabulary()

    assert window._vocab_editor.value == "draft keyword"
    assert window._vocab_editor.set_calls == []


def test_load_vocabulary_force_reloads_even_when_signature_unchanged(monkeypatch):
    from utils import vocabulary as vocabulary_mod

    window = SettingsWindow.__new__(SettingsWindow)
    window._vocab_editor = _FakeEditor("draft keyword")
    window._vocab_status = _FakeLabel()
    window._vocabulary_loaded = True
    window._last_vocabulary_signature = (7, 9)

    monkeypatch.setattr(settings_mod, "get_file_signature", lambda _path: (7, 9))
    monkeypatch.setattr(
        vocabulary_mod,
        "load_vocabulary_state",
        lambda: ({"keywords": ["alpha"]}, [], (1, 2, 3)),
    )

    window._load_vocabulary(force=True)

    assert window._vocab_editor.set_calls == ["alpha"]
    assert window._vocab_status.text == "1 keywords loaded"


def test_load_vocabulary_surfaces_validation_warnings(monkeypatch):
    from utils import vocabulary as vocabulary_mod

    window = SettingsWindow.__new__(SettingsWindow)
    window._vocab_editor = _FakeEditor("")
    window._vocab_status = _FakeLabel()
    window._vocabulary_loaded = False
    window._last_vocabulary_signature = None

    monkeypatch.setattr(settings_mod, "get_file_signature", lambda _path: (11, 22))
    monkeypatch.setattr(
        vocabulary_mod,
        "load_vocabulary_state",
        lambda: (
            {"keywords": ["alpha", "beta"]},
            ["2 doppelte Keywords gefunden."],
            (11, 22, 33),
        ),
    )

    window._load_vocabulary()

    assert window._vocab_editor.value == "alpha\nbeta"
    assert window._vocab_status.text == "⚠ 2 doppelte Keywords gefunden."


def test_save_vocabulary_surfaces_saved_warning_summary(monkeypatch):
    from utils import vocabulary as vocabulary_mod

    saved_keywords: list[list[str]] = []

    window = SettingsWindow.__new__(SettingsWindow)
    window._vocab_editor = _FakeEditor("alpha\nbeta\n")
    window._vocab_status = _FakeLabel()
    window._vocabulary_loaded = False
    window._last_vocabulary_signature = None

    monkeypatch.setattr(
        vocabulary_mod,
        "save_vocabulary_state",
        lambda keywords: (
            saved_keywords.append(list(keywords)),
            {"keywords": ["alpha", "beta"]},
            ["2 doppelte Keywords gefunden."],
            (33, 44, 55),
        )[1:],
    )

    window._save_vocabulary()

    assert saved_keywords == [["alpha", "beta"]]
    assert window._vocab_status.text == (
        "✓ Saved (2 keywords) - ⚠ 2 doppelte Keywords gefunden."
    )
    assert window._last_vocabulary_signature == (33, 44)


def test_load_settings_prefers_canonical_fp16_key(monkeypatch):
    values = {
        settings_mod.LOCAL_FP16_ENV_KEY: "false",
        settings_mod.LEGACY_LOCAL_FP16_ENV_KEY: "true",
    }
    monkeypatch.setattr(settings_mod, "read_env_file", lambda: dict(values))

    window = _make_window()
    window._load_settings()

    assert window._fp16_combo.currentText() == "false"


def test_load_settings_falls_back_to_legacy_fp16_key(monkeypatch):
    values = {
        settings_mod.LOCAL_FP16_ENV_KEY: None,
        settings_mod.LEGACY_LOCAL_FP16_ENV_KEY: "true",
    }
    monkeypatch.setattr(settings_mod, "read_env_file", lambda: dict(values))

    window = _make_window()
    window._load_settings()

    assert window._fp16_combo.currentText() == "true"


def test_save_settings_uses_canonical_fp16_key_and_removes_legacy(monkeypatch):
    recorded_updates: dict[str, str | None] = {}

    monkeypatch.setattr(
        settings_mod,
        "update_env_settings",
        lambda updates: recorded_updates.update(updates),
    )
    monkeypatch.setattr(settings_mod, "is_onboarding_complete", lambda: True)

    window = _make_save_window("true")
    window._save_settings()

    assert recorded_updates[settings_mod.LOCAL_FP16_ENV_KEY] == "true"
    assert recorded_updates[settings_mod.LEGACY_LOCAL_FP16_ENV_KEY] is None


def test_save_settings_invalid_hotkeys_show_footer_hint(monkeypatch):
    monkeypatch.setattr(settings_mod, "is_onboarding_complete", lambda: True)

    window = _make_save_window("default")
    window._validate_hotkeys_for_save = lambda: None
    window._footer_status_label = _FakeLabel()

    window._save_settings()

    assert "hotkey issues" in window._footer_status_label.text.lower()


def test_save_settings_batches_api_key_updates(monkeypatch):
    recorded_updates: dict[str, str | None] = {}

    monkeypatch.setattr(
        settings_mod,
        "update_env_settings",
        lambda updates: recorded_updates.update(updates),
    )
    monkeypatch.setattr(settings_mod, "is_onboarding_complete", lambda: True)

    window = _make_save_window("default")
    window._api_fields = {"DEEPGRAM_API_KEY": _FakeField(), "GROQ_API_KEY": _FakeField()}
    window._api_fields["DEEPGRAM_API_KEY"].setText("dg-key")
    window._api_fields["GROQ_API_KEY"].setText("")
    window._api_status = {
        "DEEPGRAM_API_KEY": _FakeLabel(),
        "GROQ_API_KEY": _FakeLabel(),
    }

    window._save_settings()

    assert recorded_updates["DEEPGRAM_API_KEY"] == "dg-key"
    assert recorded_updates["GROQ_API_KEY"] is None
    assert window._api_status["DEEPGRAM_API_KEY"].text == "Configured"
    assert window._api_status["GROQ_API_KEY"].text == "Optional"


def test_apply_local_preset_resets_stale_advanced_values():
    window = SettingsWindow.__new__(SettingsWindow)
    mode_changes: list[str] = []

    window._mode_combo = _FakeCombo(["deepgram", "local"], current="deepgram")
    window._local_backend_combo = _FakeCombo(settings_mod.LOCAL_BACKEND_OPTIONS, current="whisper")
    window._local_model_combo = _FakeCombo(settings_mod.LOCAL_MODEL_OPTIONS, current="large")
    window._device_combo = _FakeCombo(settings_mod.DEVICE_OPTIONS, current="cuda")
    window._compute_type_combo = _FakeCombo(["default", "float16", "int8"], current="float16")
    window._vad_filter_combo = _FakeCombo(settings_mod.BOOL_OVERRIDE_OPTIONS, current="false")
    window._without_timestamps_combo = _FakeCombo(settings_mod.BOOL_OVERRIDE_OPTIONS, current="false")
    window._fp16_combo = _FakeCombo(settings_mod.BOOL_OVERRIDE_OPTIONS, current="true")
    window._lightning_quant_combo = _FakeCombo(settings_mod.LIGHTNING_QUANT_OPTIONS, current="8bit")
    window._beam_size_field = _FakeField()
    window._beam_size_field.setText("9")
    window._temperature_field = _FakeField()
    window._temperature_field.setText("0.7")
    window._best_of_field = _FakeField()
    window._best_of_field.setText("4")
    window._cpu_threads_field = _FakeField()
    window._cpu_threads_field.setText("16")
    window._num_workers_field = _FakeField()
    window._num_workers_field.setText("4")
    window._lightning_batch_slider = _FakeSlider(24)
    window._preset_status = _FakeLabel()
    window._on_mode_changed = lambda mode: mode_changes.append(mode)

    SettingsWindow._apply_local_preset(window, "cpu_fast")

    assert window._mode_combo.currentText() == "local"
    assert mode_changes == ["local"]
    assert window._local_backend_combo.currentText() == "faster"
    assert window._local_model_combo.currentText() == "turbo"
    assert window._device_combo.currentText() == "cpu"
    assert window._compute_type_combo.currentText() == "int8"
    assert window._beam_size_field.value == ""
    assert window._temperature_field.value == ""
    assert window._best_of_field.value == ""
    assert window._cpu_threads_field.value == "0"
    assert window._num_workers_field.value == "1"
    assert window._vad_filter_combo.currentText() == "true"
    assert window._without_timestamps_combo.currentText() == "true"
    assert window._fp16_combo.currentText() == "default"
    assert window._lightning_batch_slider.value() == 12
    assert window._lightning_quant_combo.currentText() == "none"
    assert window._mode_combo.find_calls == 0
    assert window._local_backend_combo.find_calls == 0
    assert window._local_model_combo.find_calls == 0
    assert window._device_combo.find_calls == 0
    assert window._compute_type_combo.find_calls == 0
    assert window._vad_filter_combo.find_calls == 0
    assert window._without_timestamps_combo.find_calls == 0
    assert window._fp16_combo.find_calls == 0
    assert window._lightning_quant_combo.find_calls == 0


def test_apply_local_preset_skips_refresh_for_idempotent_values():
    window = SettingsWindow.__new__(SettingsWindow)
    mode_changes: list[str] = []
    refresh_calls: list[str] = []

    window._mode_combo = _FakeCombo(["deepgram", "local"], current="local")
    window._local_backend_combo = _FakeCombo(
        settings_mod.LOCAL_BACKEND_OPTIONS, current="faster"
    )
    window._local_model_combo = _FakeCombo(
        settings_mod.LOCAL_MODEL_OPTIONS, current="turbo"
    )
    window._device_combo = _FakeCombo(settings_mod.DEVICE_OPTIONS, current="cpu")
    window._compute_type_combo = _FakeCombo(
        ["default", "float16", "int8"], current="int8"
    )
    window._vad_filter_combo = _FakeCombo(
        settings_mod.BOOL_OVERRIDE_OPTIONS, current="true"
    )
    window._without_timestamps_combo = _FakeCombo(
        settings_mod.BOOL_OVERRIDE_OPTIONS, current="true"
    )
    window._fp16_combo = _FakeCombo(settings_mod.BOOL_OVERRIDE_OPTIONS, current="default")
    window._lightning_quant_combo = _FakeCombo(
        settings_mod.LIGHTNING_QUANT_OPTIONS, current="none"
    )
    window._beam_size_field = _FakeField("")
    window._temperature_field = _FakeField("")
    window._best_of_field = _FakeField("")
    window._cpu_threads_field = _FakeField("0")
    window._num_workers_field = _FakeField("1")
    window._lightning_batch_slider = _FakeSlider(12)
    window._preset_status = _FakeLabel()
    window._preset_status.text = "✓ 'cpu_fast' preset applied — click 'Save & Apply' to persist."
    window._preset_status.style = f"color: {settings_mod.COLORS['success']};"
    window._on_mode_changed = lambda mode: mode_changes.append(mode)
    window._refresh_setup_overview = lambda: refresh_calls.append("refresh")

    SettingsWindow._apply_local_preset(window, "cpu_fast")

    assert mode_changes == []
    assert refresh_calls == []
    assert window._mode_combo.set_calls == 0
    assert window._local_backend_combo.set_calls == 0
    assert window._local_model_combo.set_calls == 0
    assert window._device_combo.set_calls == 0
    assert window._compute_type_combo.set_calls == 0
    assert window._vad_filter_combo.set_calls == 0
    assert window._without_timestamps_combo.set_calls == 0
    assert window._fp16_combo.set_calls == 0
    assert window._lightning_quant_combo.set_calls == 0
    assert window._beam_size_field.set_calls == 0
    assert window._temperature_field.set_calls == 0
    assert window._best_of_field.set_calls == 0
    assert window._cpu_threads_field.set_calls == 0
    assert window._num_workers_field.set_calls == 0
    assert window._lightning_batch_slider.set_calls == 0
    assert window._preset_status.text_calls == 0
    assert window._preset_status.style_calls == 0


def test_build_setup_status_requires_provider_key_for_cloud_mode():
    headline, detail, color = settings_mod._build_setup_status(
        "deepgram",
        toggle_hotkey="ctrl+alt+r",
        hold_hotkey="ctrl+win",
        api_keys={"DEEPGRAM_API_KEY": ""},
    )

    assert headline == "Setup Incomplete"
    assert "Deepgram is selected" in detail
    assert "API key" in detail
    assert color == "warning"


def test_build_setup_status_allows_local_mode_without_api_key():
    headline, detail, color = settings_mod._build_setup_status(
        "local",
        toggle_hotkey="ctrl+alt+r",
        hold_hotkey="ctrl+win",
        api_keys={},
    )

    assert headline == "Ready for Local Dictation"
    assert "this device" in detail
    assert color == "success"


def test_build_setup_how_to_text_prefers_hold_flow_when_available():
    text = settings_mod._build_setup_how_to_text("ctrl+alt+r", "ctrl+win")

    assert "Hold Ctrl+Win" in text
    assert "Alternative: press Ctrl+Alt+R" in text


def test_build_provider_guidance_text_for_missing_required_key():
    text = settings_mod._build_provider_guidance_text(
        "deepgram",
        required_key_present=False,
    )

    assert "Deepgram" in text
    assert "Add its API key" in text


def test_build_api_key_status_marks_required_and_optional_states():
    assert settings_mod._build_api_key_status(
        "DEEPGRAM_API_KEY",
        mode="deepgram",
        configured=False,
    ) == ("Required", "warning")
    assert settings_mod._build_api_key_status(
        "GROQ_API_KEY",
        mode="deepgram",
        configured=False,
    ) == ("Optional", "text_secondary")
    assert settings_mod._build_api_key_status(
        "OPENAI_API_KEY",
        mode="local",
        configured=False,
    ) == ("Not needed", "text_secondary")


def test_on_mode_changed_hides_local_advanced_cards_for_cloud_modes():
    window = SettingsWindow.__new__(SettingsWindow)
    window._local_backend_container = _FakeVisibleWidget()
    window._local_model_container = _FakeVisibleWidget()
    window._streaming_container = _FakeVisibleWidget()
    window._advanced_empty_state_card = _FakeVisibleWidget()
    window._advanced_empty_state_label = _FakeLabel()
    window._advanced_guidance_label = _FakeLabel()
    window._advanced_local_settings_card = _FakeVisibleWidget()
    window._advanced_faster_settings_card = _FakeVisibleWidget()
    window._advanced_lightning_settings_card = _FakeVisibleWidget()
    window._local_backend_combo = _FakeCombo(settings_mod.LOCAL_BACKEND_OPTIONS, current="auto")
    window._refresh_provider_key_statuses = lambda: None
    window._refresh_setup_overview = lambda: None

    window._on_mode_changed("deepgram")

    assert window._local_backend_container.visible is False
    assert window._local_model_container.visible is False
    assert window._streaming_container.visible is True
    assert window._advanced_empty_state_card.visible is True
    assert window._advanced_local_settings_card.visible is False
    assert window._advanced_faster_settings_card.visible is False
    assert window._advanced_lightning_settings_card.visible is False

    window._local_backend_combo = _FakeCombo(settings_mod.LOCAL_BACKEND_OPTIONS, current="faster")
    window._on_mode_changed("local")

    assert window._local_backend_container.visible is True
    assert window._local_model_container.visible is True
    assert window._streaming_container.visible is False
    assert window._advanced_empty_state_card.visible is False
    assert window._advanced_local_settings_card.visible is True
    assert window._advanced_faster_settings_card.visible is True
    assert window._advanced_lightning_settings_card.visible is False


def test_on_mode_changed_uses_hidden_state_before_first_show():
    window = SettingsWindow.__new__(SettingsWindow)
    window._local_backend_container = _FakePreShowVisibleWidget()
    window._local_model_container = _FakePreShowVisibleWidget()
    window._streaming_container = _FakePreShowVisibleWidget()
    window._advanced_empty_state_card = _FakePreShowVisibleWidget()
    window._advanced_empty_state_label = _FakeLabel()
    window._advanced_guidance_label = _FakeLabel()
    window._advanced_local_settings_card = _FakePreShowVisibleWidget()
    window._advanced_faster_settings_card = _FakePreShowVisibleWidget()
    window._advanced_lightning_settings_card = _FakePreShowVisibleWidget()
    window._local_backend_combo = _FakeCombo(settings_mod.LOCAL_BACKEND_OPTIONS, current="auto")
    window._refresh_provider_key_statuses = lambda: None
    window._refresh_setup_overview = lambda: None

    window._on_mode_changed("deepgram")

    assert window._local_backend_container.hidden is True
    assert window._local_model_container.hidden is True
    assert window._advanced_empty_state_card.hidden is False
    assert window._advanced_local_settings_card.hidden is True
    assert window._advanced_faster_settings_card.hidden is True
    assert window._advanced_lightning_settings_card.hidden is True
    assert window._streaming_container.hidden is False
