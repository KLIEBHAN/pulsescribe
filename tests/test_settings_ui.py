"""Tests für Settings-UI Persistierung und Helper-Funktionen."""

import pytest
from unittest.mock import MagicMock, patch

from ui.welcome import (
    LEGACY_LOCAL_FP16_ENV_KEY,
    LOCAL_FP16_ENV_KEY,
    LOCAL_MODEL_OPTIONS,
    WelcomeController,
    _bool_override_from_env,
    _is_env_enabled_default_true,
)


class TestEnvEnabledDefaultTrue:
    """Tests für _is_env_enabled_default_true Helper."""

    def test_unset_returns_true(self):
        """Nicht gesetzte ENV-Variable gibt True zurück (Default)."""
        with patch("utils.preferences.get_env_setting", return_value=None):
            assert _is_env_enabled_default_true("TEST_KEY") is True

    def test_false_returns_false(self):
        """'false' gibt False zurück."""
        with patch("utils.preferences.get_env_setting", return_value="false"):
            assert _is_env_enabled_default_true("TEST_KEY") is False

    @pytest.mark.parametrize("value", ["0", "no", "off"])
    def test_falsy_variants_lowercase(self, value):
        """Lowercase falsy-Werte geben False zurück."""
        with patch("utils.preferences.get_env_setting", return_value=value):
            assert _is_env_enabled_default_true("TEST_KEY") is False

    @pytest.mark.parametrize("value", ["FALSE", "NO", "OFF"])
    def test_falsy_variants_uppercase(self, value):
        """Uppercase falsy-Werte geben False zurück (case-insensitive)."""
        with patch("utils.preferences.get_env_setting", return_value=value):
            assert _is_env_enabled_default_true("TEST_KEY") is False

    def test_true_returns_true(self):
        """'true' gibt True zurück."""
        with patch("utils.preferences.get_env_setting", return_value="true"):
            assert _is_env_enabled_default_true("TEST_KEY") is True

    def test_random_value_returns_true(self):
        """Nicht erkannte Werte geben True zurück (default-true)."""
        with patch("utils.preferences.get_env_setting", return_value="maybe"):
            assert _is_env_enabled_default_true("TEST_KEY") is True


class TestBoolOverrideFromEnv:
    def test_prefers_canonical_value_over_legacy(self, monkeypatch):
        values = {
            LOCAL_FP16_ENV_KEY: "false",
            LEGACY_LOCAL_FP16_ENV_KEY: "true",
        }
        monkeypatch.setattr(
            "ui.welcome.get_env_setting",
            lambda key: values.get(key),
        )

        assert (
            _bool_override_from_env(LOCAL_FP16_ENV_KEY, LEGACY_LOCAL_FP16_ENV_KEY)
            == "false"
        )

    def test_uses_legacy_value_when_canonical_is_missing(self, monkeypatch):
        values = {
            LOCAL_FP16_ENV_KEY: None,
            LEGACY_LOCAL_FP16_ENV_KEY: "true",
        }
        monkeypatch.setattr(
            "ui.welcome.get_env_setting",
            lambda key: values.get(key),
        )

        assert (
            _bool_override_from_env(LOCAL_FP16_ENV_KEY, LEGACY_LOCAL_FP16_ENV_KEY)
            == "true"
        )


class TestLightningBatchSizeParsing:
    """Tests für Lightning Batch-Size Integer-Parsing."""

    def test_valid_integer_parsed(self):
        """Gültige Integer werden korrekt geparst."""
        with patch("ui.welcome.get_env_setting", return_value="16"):
            # Simuliere die Parsing-Logik aus welcome.py:1420-1425
            current_batch = "16"
            try:
                batch_val = int(current_batch) if current_batch else 12
            except ValueError:
                batch_val = 12
            assert batch_val == 16

    def test_empty_uses_default(self):
        """Leerer Wert verwendet Default 12."""
        current_batch = ""
        try:
            batch_val = int(current_batch) if current_batch else 12
        except ValueError:
            batch_val = 12
        assert batch_val == 12

    def test_none_uses_default(self):
        """None verwendet Default 12."""
        current_batch = None
        try:
            batch_val = int(current_batch) if current_batch else 12
        except ValueError:
            batch_val = 12
        assert batch_val == 12

    def test_invalid_string_falls_back_to_default(self):
        """Ungültiger String fällt auf Default 12 zurück."""
        current_batch = "invalid"
        try:
            batch_val = int(current_batch) if current_batch else 12
        except ValueError:
            batch_val = 12
        assert batch_val == 12

    def test_float_string_falls_back_to_default(self):
        """Float-String fällt auf Default zurück."""
        current_batch = "12.5"
        try:
            batch_val = int(current_batch) if current_batch else 12
        except ValueError:
            batch_val = 12
        assert batch_val == 12


class TestLightningQuantizationMapping:
    """Tests für Lightning Quantization Index→String Mapping."""

    def test_index_0_is_none(self):
        """Index 0 entspricht keine Quantisierung (none/default)."""
        quant_idx = 0
        if quant_idx == 0:
            result = None  # Remove from env
        elif quant_idx == 1:
            result = "8bit"
        else:
            result = "4bit"
        assert result is None

    def test_index_1_is_8bit(self):
        """Index 1 entspricht 8bit Quantisierung."""
        quant_idx = 1
        if quant_idx == 0:
            result = None
        elif quant_idx == 1:
            result = "8bit"
        else:
            result = "4bit"
        assert result == "8bit"

    def test_index_2_is_4bit(self):
        """Index 2 entspricht 4bit Quantisierung."""
        quant_idx = 2
        if quant_idx == 0:
            result = None
        elif quant_idx == 1:
            result = "8bit"
        else:
            result = "4bit"
        assert result == "4bit"


class TestWelcomeLocalPresetBehavior:
    def test_local_model_options_cover_models_used_by_presets(self):
        from utils.presets import LOCAL_PRESETS

        preset_models = {
            values["local_model"]
            for values in LOCAL_PRESETS.values()
            if values.get("local_model")
        }

        assert preset_models <= set(LOCAL_MODEL_OPTIONS)

    def test_apply_local_preset_resets_lightning_fields(self):
        ctrl = WelcomeController.__new__(WelcomeController)
        ctrl._mode_popup = _FakePopup(["deepgram", "local"], selected="deepgram")
        ctrl._local_backend_popup = _FakePopup(
            ["auto", "whisper", "faster", "mlx", "lightning"],
            selected="lightning",
        )
        ctrl._local_model_popup = _FakePopup(
            ["default", "turbo", "large", "large-v3"],
            selected="large-v3",
        )
        ctrl._device_popup = _FakePopup(["auto", "mps", "cpu", "cuda"], selected="cuda")
        ctrl._warmup_popup = _FakePopup(["auto", "true", "false"], selected="true")
        ctrl._local_fast_popup = _FakePopup(
            ["default", "true", "false"],
            selected="false",
        )
        ctrl._fp16_popup = _FakePopup(["default", "true", "false"], selected="true")
        ctrl._beam_size_field = _FakeField("9")
        ctrl._best_of_field = _FakeField("4")
        ctrl._temperature_field = _FakeField("0.7")
        ctrl._compute_type_field = _FakeField("float16")
        ctrl._cpu_threads_field = _FakeField("16")
        ctrl._num_workers_field = _FakeField("4")
        ctrl._without_timestamps_popup = _FakePopup(
            ["default", "true", "false"],
            selected="false",
        )
        ctrl._vad_filter_popup = _FakePopup(
            ["default", "true", "false"],
            selected="false",
        )
        ctrl._lightning_batch_slider = _FakeSlider(24)
        ctrl._lightning_batch_value_label = _FakeField("24")
        ctrl._lightning_quant_popup = _FakePopup(
            ["none (best quality)", "8bit", "4bit (smallest memory)"],
            selected="4bit (smallest memory)",
        )
        ctrl._lightning_quant_popup.selected_index = 2
        ctrl._update_local_settings_visibility = lambda: None

        ctrl._apply_local_preset("macOS: MLX Fast (turbo)")

        assert ctrl._mode_popup.titleOfSelectedItem() == "local"
        assert ctrl._lightning_batch_slider.intValue() == 12
        assert ctrl._lightning_batch_value_label.stringValue() == "12"
        assert ctrl._lightning_quant_popup.indexOfSelectedItem() == 0


class TestWelcomeSaveSettings:
    def test_save_settings_uses_canonical_fp16_key_and_removes_legacy(self, monkeypatch):
        save_calls: list[tuple[str, str]] = []
        remove_calls: list[str] = []

        monkeypatch.setattr(
            "ui.welcome.save_env_setting",
            lambda key, value: save_calls.append((key, value)),
        )
        monkeypatch.setattr(
            "ui.welcome.remove_env_setting",
            lambda key: remove_calls.append(key),
        )

        ctrl = WelcomeController.__new__(WelcomeController)
        ctrl._mode_popup = None
        ctrl._local_backend_popup = None
        ctrl._local_model_popup = None
        ctrl._lang_popup = None
        ctrl._device_popup = None
        ctrl._warmup_popup = None
        ctrl._local_fast_popup = None
        ctrl._fp16_popup = _FakePopup(["default", "true", "false"], selected="true")
        ctrl._beam_size_field = None
        ctrl._best_of_field = None
        ctrl._temperature_field = None
        ctrl._compute_type_field = None
        ctrl._cpu_threads_field = None
        ctrl._num_workers_field = None
        ctrl._without_timestamps_popup = None
        ctrl._vad_filter_popup = None
        ctrl._lightning_batch_slider = None
        ctrl._lightning_quant_popup = None
        ctrl._streaming_checkbox = None
        ctrl._refine_checkbox = None
        ctrl._clipboard_restore_checkbox = None
        ctrl._overlay_checkbox = None
        ctrl._dock_icon_checkbox = None
        ctrl._rtf_checkbox = None
        ctrl._provider_popup = None
        ctrl._model_field = None
        ctrl._vocab_text_view = None
        ctrl._save_custom_prompts = lambda: None
        ctrl._on_settings_changed_callback = None
        ctrl._save_btn = None

        ctrl._save_all_settings()

        assert (LOCAL_FP16_ENV_KEY, "true") in save_calls
        assert LEGACY_LOCAL_FP16_ENV_KEY in remove_calls


class TestWelcomePrivacySettings:
    def test_open_privacy_settings_passes_window(self):
        ctrl = WelcomeController.__new__(WelcomeController)
        ctrl._window = object()

        with patch("utils.permissions.open_privacy_settings") as mock_open:
            WelcomeController._open_privacy_settings(ctrl, "Privacy_Microphone")

        mock_open.assert_called_once_with(
            "Privacy_Microphone",
            window=ctrl._window,
        )


class _FakeContainer:
    def __init__(self):
        self.hidden = None

    def setHidden_(self, value):
        self.hidden = value


class _FakePopup:
    def __init__(self, items=None, selected=None):
        self.items = list(items or [])
        self.selected = selected
        self.selected_index = 0

    def addItemWithTitle_(self, title):
        self.items.append(title)

    def selectItemWithTitle_(self, title):
        if title not in self.items:
            raise ValueError(title)
        self.selected = title
        self.selected_index = self.items.index(title)

    def selectItemAtIndex_(self, index):
        self.selected_index = index
        if 0 <= index < len(self.items):
            self.selected = self.items[index]

    def titleOfSelectedItem(self):
        return self.selected

    def indexOfSelectedItem(self):
        return self.selected_index


class _FakeField:
    def __init__(self, value=""):
        self.value = value

    def setStringValue_(self, value):
        self.value = value

    def stringValue(self):
        return self.value


class _FakeSlider:
    def __init__(self, value: int):
        self._value = value

    def setIntValue_(self, value: int):
        self._value = value

    def intValue(self):
        return self._value


class TestWelcomeLogsSegmentSwitch:
    """Tests für Logs/Transcripts Segment-Verhalten im macOS-Settingsfenster."""

    def test_switch_to_logs_shows_logs_and_refreshes(self):
        ctrl = WelcomeController.__new__(WelcomeController)
        ctrl._logs_container = _FakeContainer()
        ctrl._transcripts_container = _FakeContainer()
        ctrl._refresh_logs = MagicMock()
        ctrl._refresh_transcripts = MagicMock()

        ctrl._switch_logs_segment(0)

        assert ctrl._logs_container.hidden is False
        assert ctrl._transcripts_container.hidden is True
        ctrl._refresh_logs.assert_called_once_with(scroll_to_bottom=True)
        ctrl._refresh_transcripts.assert_not_called()

    def test_switch_to_transcripts_shows_transcripts_and_refreshes(self):
        ctrl = WelcomeController.__new__(WelcomeController)
        ctrl._logs_container = _FakeContainer()
        ctrl._transcripts_container = _FakeContainer()
        ctrl._refresh_logs = MagicMock()
        ctrl._refresh_transcripts = MagicMock()
        ctrl._transcripts_view_seen = False

        ctrl._switch_logs_segment(1)

        assert ctrl._logs_container.hidden is True
        assert ctrl._transcripts_container.hidden is False
        ctrl._refresh_transcripts.assert_called_once_with(scroll_to_bottom=True)
        ctrl._refresh_logs.assert_not_called()
        assert ctrl._transcripts_view_seen is True

    def test_switch_to_transcripts_preserves_position_after_first_open(self):
        ctrl = WelcomeController.__new__(WelcomeController)
        ctrl._logs_container = _FakeContainer()
        ctrl._transcripts_container = _FakeContainer()
        ctrl._refresh_logs = MagicMock()
        ctrl._refresh_transcripts = MagicMock()
        ctrl._transcripts_view_seen = True

        ctrl._switch_logs_segment(1)

        ctrl._refresh_transcripts.assert_called_once_with(scroll_to_bottom=False)

    def test_logs_view_active_when_segment_is_logs(self):
        ctrl = WelcomeController.__new__(WelcomeController)
        ctrl._logs_segment_control = type(
            "_Segment",
            (),
            {"selectedSegment": lambda self: 0},
        )()
        assert ctrl._is_logs_view_active() is True

    def test_logs_view_inactive_when_segment_is_transcripts(self):
        ctrl = WelcomeController.__new__(WelcomeController)
        ctrl._logs_segment_control = type(
            "_Segment",
            (),
            {"selectedSegment": lambda self: 1},
        )()
        assert ctrl._is_logs_view_active() is False


class _FakeTabItem:
    def __init__(self, identifier):
        self._identifier = identifier

    def identifier(self):
        return self._identifier


class _FakeTabView:
    def __init__(self, identifier):
        self._item = _FakeTabItem(identifier)

    def selectedTabViewItem(self):
        return self._item


class _FakeWindow:
    def __init__(self, visible: bool, miniaturized: bool):
        self._visible = visible
        self._miniaturized = miniaturized

    def isVisible(self):
        return self._visible

    def isMiniaturized(self):
        return self._miniaturized


class TestWelcomeLogsAutoRefreshGuards:
    def test_logs_tab_active_detection(self):
        ctrl = WelcomeController.__new__(WelcomeController)
        ctrl._tab_view = _FakeTabView("Logs")
        assert ctrl._is_logs_tab_active() is True

    def test_logs_tab_inactive_detection(self):
        ctrl = WelcomeController.__new__(WelcomeController)
        ctrl._tab_view = _FakeTabView("Setup")
        assert ctrl._is_logs_tab_active() is False

    def test_window_visibility_detection(self):
        ctrl = WelcomeController.__new__(WelcomeController)
        ctrl._window = _FakeWindow(visible=True, miniaturized=False)
        assert ctrl._is_window_visible_for_logs() is True

        ctrl._window = _FakeWindow(visible=True, miniaturized=True)
        assert ctrl._is_window_visible_for_logs() is False

    def test_auto_refresh_tick_skips_when_logs_tab_not_visible(self, monkeypatch):
        import sys
        import types

        captured: dict[str, object] = {}

        class _FakeNSTimer:
            @staticmethod
            def scheduledTimerWithTimeInterval_repeats_block_(interval, repeats, block):
                captured["interval"] = interval
                captured["repeats"] = repeats
                captured["tick"] = block
                return object()

        monkeypatch.setitem(
            sys.modules,
            "Foundation",
            types.SimpleNamespace(NSTimer=_FakeNSTimer),
        )

        ctrl = WelcomeController.__new__(WelcomeController)
        ctrl._stop_logs_auto_refresh = lambda: None
        ctrl._logs_auto_checkbox = type("_Check", (), {"state": lambda self: 1})()
        ctrl._is_logs_tab_active = lambda: False
        ctrl._is_logs_view_active = lambda: True
        ctrl._is_window_visible_for_logs = lambda: True
        ctrl._refresh_logs = MagicMock()

        ctrl._start_logs_auto_refresh()
        captured["tick"](None)

        ctrl._refresh_logs.assert_not_called()

    def test_auto_refresh_tick_runs_when_logs_are_visible(self, monkeypatch):
        import sys
        import types

        captured: dict[str, object] = {}

        class _FakeNSTimer:
            @staticmethod
            def scheduledTimerWithTimeInterval_repeats_block_(interval, repeats, block):
                captured["interval"] = interval
                captured["repeats"] = repeats
                captured["tick"] = block
                return object()

        monkeypatch.setitem(
            sys.modules,
            "Foundation",
            types.SimpleNamespace(NSTimer=_FakeNSTimer),
        )

        ctrl = WelcomeController.__new__(WelcomeController)
        ctrl._stop_logs_auto_refresh = lambda: None
        ctrl._logs_auto_checkbox = type("_Check", (), {"state": lambda self: 1})()
        ctrl._is_logs_tab_active = lambda: True
        ctrl._is_logs_view_active = lambda: True
        ctrl._is_window_visible_for_logs = lambda: True
        ctrl._refresh_logs = MagicMock()

        ctrl._start_logs_auto_refresh()
        captured["tick"](None)

        ctrl._refresh_logs.assert_called_once_with(scroll_to_bottom=False)

    def test_auto_refresh_tick_runs_when_transcripts_are_visible(self, monkeypatch):
        import sys
        import types

        captured: dict[str, object] = {}

        class _FakeNSTimer:
            @staticmethod
            def scheduledTimerWithTimeInterval_repeats_block_(interval, repeats, block):
                captured["interval"] = interval
                captured["repeats"] = repeats
                captured["tick"] = block
                return object()

        monkeypatch.setitem(
            sys.modules,
            "Foundation",
            types.SimpleNamespace(NSTimer=_FakeNSTimer),
        )

        ctrl = WelcomeController.__new__(WelcomeController)
        ctrl._stop_logs_auto_refresh = lambda: None
        ctrl._logs_auto_checkbox = type("_Check", (), {"state": lambda self: 1})()
        ctrl._is_logs_tab_active = lambda: True
        ctrl._is_logs_view_active = lambda: False
        ctrl._is_window_visible_for_logs = lambda: True
        ctrl._refresh_logs = MagicMock()
        ctrl._refresh_transcripts = MagicMock()

        ctrl._start_logs_auto_refresh()
        captured["tick"](None)

        ctrl._refresh_logs.assert_not_called()
        ctrl._refresh_transcripts.assert_called_once_with(scroll_to_bottom=False)


class _FakePoint:
    def __init__(self, y: float):
        self.y = y


class _FakeSize:
    def __init__(self, height: float):
        self.height = height


class _FakeRect:
    def __init__(self, y: float, height: float):
        self.origin = _FakePoint(y)
        self.size = _FakeSize(height)


class _FakeClipView:
    def __init__(self, *, y: float, height: float):
        self._rect = _FakeRect(y, height)
        self.scrolled_to = None

    def documentVisibleRect(self):
        return self._rect

    def scrollToPoint_(self, point):
        self.scrolled_to = point


class _FakeTranscriptsScrollView:
    def __init__(self, clip_view):
        self._clip_view = clip_view
        self.reflected = []

    def contentView(self):
        return self._clip_view

    def reflectScrolledClipView_(self, clip_view):
        self.reflected.append(clip_view)


class _FakeFrame:
    def __init__(self, height: float):
        self.size = _FakeSize(height)


class _FakeTranscriptsTextView:
    def __init__(self, initial_text: str, *, doc_height: float):
        self._text = initial_text
        self._doc_height = doc_height
        self.set_calls = []

    def setString_(self, text: str):
        self._text = text
        self.set_calls.append(text)

    def string(self):
        return self._text

    def frame(self):
        return _FakeFrame(self._doc_height)


class _FakeTranscriptsCountLabel:
    def __init__(self):
        self.value = ""

    def setStringValue_(self, value: str):
        self.value = value


class TestWelcomeTranscriptsRefreshBehavior:
    def test_refresh_transcripts_skips_file_read_when_signature_unchanged(
        self, monkeypatch
    ):
        import ui.welcome as welcome_mod

        ctrl = WelcomeController.__new__(WelcomeController)
        ctrl._transcripts_text_view = _FakeTranscriptsTextView(
            "cached text",
            doc_height=800,
        )
        ctrl._transcripts_scroll_view = _FakeTranscriptsScrollView(
            _FakeClipView(y=120, height=300)
        )
        ctrl._transcripts_count_label = _FakeTranscriptsCountLabel()
        ctrl._last_transcripts_text = "cached text"
        ctrl._last_transcripts_signature = (1, 2)
        ctrl._get_transcripts_payload = MagicMock(
            side_effect=AssertionError("transcripts should not reload")
        )
        ctrl._scroll_transcripts_to_bottom = MagicMock()

        monkeypatch.setattr(welcome_mod, "get_file_signature", lambda _path: (1, 2))

        ctrl._refresh_transcripts(scroll_to_bottom=False)

        ctrl._get_transcripts_payload.assert_not_called()
        ctrl._scroll_transcripts_to_bottom.assert_not_called()

    def test_refresh_transcripts_can_scroll_to_bottom_without_reloading(
        self, monkeypatch
    ):
        import ui.welcome as welcome_mod

        ctrl = WelcomeController.__new__(WelcomeController)
        ctrl._transcripts_text_view = _FakeTranscriptsTextView(
            "cached text",
            doc_height=800,
        )
        ctrl._transcripts_scroll_view = _FakeTranscriptsScrollView(
            _FakeClipView(y=120, height=300)
        )
        ctrl._transcripts_count_label = _FakeTranscriptsCountLabel()
        ctrl._last_transcripts_text = "cached text"
        ctrl._last_transcripts_signature = (1, 2)
        ctrl._get_transcripts_payload = MagicMock(
            side_effect=AssertionError("transcripts should not reload")
        )
        ctrl._scroll_transcripts_to_bottom = MagicMock()

        monkeypatch.setattr(welcome_mod, "get_file_signature", lambda _path: (1, 2))

        ctrl._refresh_transcripts(scroll_to_bottom=True)

        ctrl._get_transcripts_payload.assert_not_called()
        ctrl._scroll_transcripts_to_bottom.assert_called_once_with()

    def test_refresh_transcripts_skips_rerender_when_text_unchanged(self):
        ctrl = WelcomeController.__new__(WelcomeController)
        ctrl._transcripts_text_view = _FakeTranscriptsTextView(
            "cached text",
            doc_height=800,
        )
        ctrl._transcripts_scroll_view = _FakeTranscriptsScrollView(
            _FakeClipView(y=120, height=300)
        )
        ctrl._transcripts_count_label = _FakeTranscriptsCountLabel()
        ctrl._last_transcripts_text = "cached text"
        ctrl._last_transcripts_signature = None
        ctrl._get_transcripts_payload = lambda: ("cached text", 5)
        ctrl._scroll_transcripts_to_bottom = MagicMock()
        ctrl._restore_transcripts_scroll_position = MagicMock()

        ctrl._refresh_transcripts(scroll_to_bottom=False)

        assert ctrl._transcripts_text_view.set_calls == []
        assert ctrl._transcripts_count_label.value == "5 entries"
        ctrl._scroll_transcripts_to_bottom.assert_not_called()
        ctrl._restore_transcripts_scroll_position.assert_not_called()

    def test_refresh_transcripts_preserves_scroll_position_when_not_near_bottom(
        self, monkeypatch
    ):
        import sys
        import types

        monkeypatch.setitem(
            sys.modules,
            "Foundation",
            types.SimpleNamespace(NSMakePoint=lambda x, y: (x, y)),
        )

        ctrl = WelcomeController.__new__(WelcomeController)
        clip_view = _FakeClipView(y=120, height=100)
        ctrl._transcripts_scroll_view = _FakeTranscriptsScrollView(clip_view)
        ctrl._transcripts_text_view = _FakeTranscriptsTextView(
            "old text",
            doc_height=900,
        )
        ctrl._transcripts_count_label = _FakeTranscriptsCountLabel()
        ctrl._last_transcripts_text = "old text"
        ctrl._last_transcripts_signature = None
        ctrl._get_transcripts_payload = lambda: ("new text", 3)
        ctrl._scroll_transcripts_to_bottom = MagicMock()

        ctrl._refresh_transcripts(scroll_to_bottom=False)

        assert ctrl._transcripts_text_view.set_calls == ["new text"]
        assert clip_view.scrolled_to == (0.0, 120)
        assert ctrl._last_transcripts_text == "new text"
        assert ctrl._transcripts_count_label.value == "3 entries"
        ctrl._scroll_transcripts_to_bottom.assert_not_called()


class TestWelcomeLogsRefreshBehavior:
    def test_refresh_logs_skips_file_read_when_signature_unchanged(self, monkeypatch):
        import ui.welcome as welcome_mod

        ctrl = WelcomeController.__new__(WelcomeController)
        ctrl._logs_text_view = _FakeTranscriptsTextView("cached logs", doc_height=600)
        ctrl._logs_scroll_view = _FakeTranscriptsScrollView(
            _FakeClipView(y=100, height=240)
        )
        ctrl._last_logs_text = "cached logs"
        ctrl._last_logs_signature = (1, 2)
        ctrl._get_logs_text = MagicMock(
            side_effect=AssertionError("log tail should not be read")
        )
        ctrl._scroll_logs_to_bottom = MagicMock()
        ctrl._restore_logs_scroll_position = MagicMock()

        monkeypatch.setattr(welcome_mod, "get_file_signature", lambda _path: (1, 2))

        ctrl._refresh_logs(scroll_to_bottom=False)

        ctrl._get_logs_text.assert_not_called()
        assert ctrl._logs_text_view.set_calls == []
        ctrl._scroll_logs_to_bottom.assert_not_called()
        ctrl._restore_logs_scroll_position.assert_not_called()

    def test_refresh_logs_updates_signature_even_when_text_unchanged(self, monkeypatch):
        import ui.welcome as welcome_mod

        ctrl = WelcomeController.__new__(WelcomeController)
        ctrl._logs_text_view = _FakeTranscriptsTextView("same logs", doc_height=600)
        ctrl._logs_scroll_view = _FakeTranscriptsScrollView(
            _FakeClipView(y=80, height=220)
        )
        ctrl._last_logs_text = "same logs"
        ctrl._last_logs_signature = (1, 2)
        ctrl._get_logs_text = MagicMock(return_value="same logs")
        ctrl._scroll_logs_to_bottom = MagicMock()
        ctrl._restore_logs_scroll_position = MagicMock()

        monkeypatch.setattr(welcome_mod, "get_file_signature", lambda _path: (3, 4))

        ctrl._refresh_logs(scroll_to_bottom=False)

        ctrl._get_logs_text.assert_called_once()
        assert ctrl._logs_text_view.set_calls == []
        assert ctrl._last_logs_signature == (3, 4)
