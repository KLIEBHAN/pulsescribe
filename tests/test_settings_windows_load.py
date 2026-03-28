import pytest

pytest.importorskip("PySide6")

import ui.settings_windows as settings_mod
from ui.settings_windows import SettingsWindow


class _FakeCheckbox:
    def __init__(self):
        self.checked: bool | None = None

    def setChecked(self, value: bool) -> None:
        self.checked = value


class _FakeField:
    def __init__(self):
        self.value = ""

    def setText(self, value: str) -> None:
        self.value = value

    def text(self) -> str:
        return self.value


class _FakeLabel:
    def __init__(self):
        self.text = ""
        self.style = ""

    def setText(self, value: str) -> None:
        self.text = value

    def setStyleSheet(self, value: str) -> None:
        self.style = value


class _FakeVisibleWidget:
    def __init__(self):
        self.visible: bool | None = None

    def setVisible(self, value: bool) -> None:
        self.visible = value


class _FakeSlider:
    def __init__(self, value: int):
        self._value = value

    def setValue(self, value: int) -> None:
        self._value = value

    def value(self) -> int:
        return self._value


class _FakeCombo:
    def __init__(self, items: list[str], current: str = "default"):
        self._items = list(items)
        self._current_index = 0
        if current in self._items:
            self._current_index = self._items.index(current)

    def findText(self, value: str) -> int:
        try:
            return self._items.index(value)
        except ValueError:
            return -1

    def setCurrentIndex(self, index: int) -> None:
        self._current_index = index

    def currentText(self) -> str:
        return self._items[self._current_index]


class _FakeSignal:
    def __init__(self):
        self.emitted = False

    def emit(self) -> None:
        self.emitted = True


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
    assert status.text == "✓"


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
    save_calls: list[tuple[str, str]] = []
    remove_calls: list[str] = []

    monkeypatch.setattr(
        settings_mod,
        "save_env_setting",
        lambda key, value: save_calls.append((key, value)),
    )
    monkeypatch.setattr(
        settings_mod,
        "remove_env_setting",
        lambda key: remove_calls.append(key),
    )
    monkeypatch.setattr(settings_mod, "set_api_key", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(settings_mod, "is_onboarding_complete", lambda: True)

    window = _make_save_window("true")
    window._save_settings()

    assert (settings_mod.LOCAL_FP16_ENV_KEY, "true") in save_calls
    assert settings_mod.LEGACY_LOCAL_FP16_ENV_KEY in remove_calls
    assert not any(
        key == settings_mod.LEGACY_LOCAL_FP16_ENV_KEY for key, _ in save_calls
    )


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


def test_build_setup_status_requires_provider_key_for_cloud_mode():
    headline, detail, color = settings_mod._build_setup_status(
        "deepgram",
        toggle_hotkey="ctrl+alt+r",
        hold_hotkey="ctrl+win",
        api_keys={"DEEPGRAM_API_KEY": ""},
    )

    assert headline == "Setup Incomplete"
    assert "Deepgram API key" in detail
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

    assert "Hold ctrl+win" in text
    assert "Alternative: press ctrl+alt+r" in text


def test_on_mode_changed_hides_local_advanced_cards_for_cloud_modes():
    window = SettingsWindow.__new__(SettingsWindow)
    window._local_backend_container = _FakeVisibleWidget()
    window._local_model_container = _FakeVisibleWidget()
    window._streaming_container = _FakeVisibleWidget()
    window._advanced_local_settings_card = _FakeVisibleWidget()
    window._advanced_faster_settings_card = _FakeVisibleWidget()
    window._advanced_lightning_settings_card = _FakeVisibleWidget()
    window._refresh_setup_overview = lambda: None

    window._on_mode_changed("deepgram")

    assert window._local_backend_container.visible is False
    assert window._local_model_container.visible is False
    assert window._streaming_container.visible is True
    assert window._advanced_local_settings_card.visible is False
    assert window._advanced_faster_settings_card.visible is False
    assert window._advanced_lightning_settings_card.visible is False

    window._on_mode_changed("local")

    assert window._local_backend_container.visible is True
    assert window._local_model_container.visible is True
    assert window._streaming_container.visible is False
    assert window._advanced_local_settings_card.visible is True
    assert window._advanced_faster_settings_card.visible is True
    assert window._advanced_lightning_settings_card.visible is True
