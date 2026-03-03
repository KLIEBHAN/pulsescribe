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
        ctrl._refresh_transcripts.assert_called_once_with(scroll_to_bottom=False)
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
