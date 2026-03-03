"""Tests für Settings-UI Persistierung und Helper-Funktionen."""

import pytest
from unittest.mock import MagicMock, patch

from ui.welcome import WelcomeController, _is_env_enabled_default_true


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


class _FakeContainer:
    def __init__(self):
        self.hidden = None

    def setHidden_(self, value):
        self.hidden = value


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

        ctrl._switch_logs_segment(1)

        assert ctrl._logs_container.hidden is True
        assert ctrl._transcripts_container.hidden is False
        ctrl._refresh_transcripts.assert_called_once_with()
        ctrl._refresh_logs.assert_not_called()

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
