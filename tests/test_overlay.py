import sys
import types

from ui.overlay import (
    OVERLAY_INTERIM_MAX_CHARS,
    WAVE_ANIMATION_FPS,
    WAVE_ANIMATION_FPS_ACTIVE,
    WAVE_ANIMATION_FPS_FEEDBACK,
    WAVE_HEIGHT_UPDATE_EPSILON,
    OverlayController,
    SoundWaveView,
    _format_recording_interim_text,
)


class _FakeTextField:
    def __init__(self) -> None:
        self.font_calls = 0
        self.color_calls = 0
        self.text_calls = 0
        self.font = None
        self.color = None
        self.text = ""

    def setFont_(self, font) -> None:
        self.font = font
        self.font_calls += 1

    def setTextColor_(self, color) -> None:
        self.color = color
        self.color_calls += 1

    def setStringValue_(self, text: str) -> None:
        self.text = text
        self.text_calls += 1


class _FakeBar:
    def __init__(self) -> None:
        self.bounds_calls = 0
        self.position_calls = 0
        self.last_bounds = None
        self.last_position = None

    def setBounds_(self, bounds) -> None:
        self.last_bounds = bounds
        self.bounds_calls += 1

    def setPosition_(self, position) -> None:
        self.last_position = position
        self.position_calls += 1


def _install_fake_foundation(monkeypatch):
    scheduled_calls: list[tuple[float, bool, object]] = []

    class _FakeNSTimer:
        @staticmethod
        def scheduledTimerWithTimeInterval_repeats_block_(
            interval: float, repeats: bool, block
        ):
            scheduled_calls.append((interval, repeats, block))
            return object()

    monkeypatch.setitem(
        sys.modules,
        "Foundation",
        types.SimpleNamespace(NSTimer=_FakeNSTimer),
    )
    return scheduled_calls


def test_format_recording_interim_text_compacts_whitespace():
    text = "  hello   world \n\n from   pulse  "
    assert _format_recording_interim_text(text) == "hello world from pulse"


def test_format_recording_interim_text_keeps_tail_for_long_text():
    text = "alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu"
    max_chars = 18

    formatted = _format_recording_interim_text(text, max_chars=max_chars)

    assert formatted == "..." + " ".join(text.split())[-(max_chars - 3) :]
    assert len(formatted) == max_chars


def test_format_recording_interim_text_uses_default_limit():
    text = "x" * (OVERLAY_INTERIM_MAX_CHARS + 20)
    formatted = _format_recording_interim_text(text)

    assert formatted.startswith("...")
    assert len(formatted) == OVERLAY_INTERIM_MAX_CHARS


def test_sound_wave_view_set_bar_height_skips_small_deltas():
    view = SoundWaveView.__new__(SoundWaveView)
    view.bars = [_FakeBar()]
    view._last_heights = [10.0]

    changed = SoundWaveView._set_bar_height(
        view,
        0,
        10.0 + (WAVE_HEIGHT_UPDATE_EPSILON / 2),
        rect_factory=lambda *args: args,
    )

    assert changed is False
    assert view.bars[0].bounds_calls == 0

    changed = SoundWaveView._set_bar_height(
        view,
        0,
        10.0 + WAVE_HEIGHT_UPDATE_EPSILON + 0.1,
        rect_factory=lambda *args: args,
    )

    assert changed is True
    assert view.bars[0].bounds_calls == 1


def test_sound_wave_view_only_repositions_when_center_changes():
    view = SoundWaveView.__new__(SoundWaveView)
    view.bars = [_FakeBar(), _FakeBar()]
    view._bar_positions = [5.0, 15.0]
    view._last_center_y = None

    SoundWaveView._ensure_bar_positions(view, 20.0)
    SoundWaveView._ensure_bar_positions(view, 20.0)
    SoundWaveView._ensure_bar_positions(view, 22.0)

    assert [bar.position_calls for bar in view.bars] == [2, 2]
    assert view.bars[0].last_position == (5.0, 22.0)
    assert view.bars[1].last_position == (15.0, 22.0)


def test_sound_wave_view_start_level_timer_uses_recording_interval(monkeypatch):
    scheduled_calls = _install_fake_foundation(monkeypatch)
    view = SoundWaveView.__new__(SoundWaveView)
    view._level_timer = None

    SoundWaveView._start_level_timer(view)

    assert scheduled_calls[0][0] == 1.0 / WAVE_ANIMATION_FPS
    assert scheduled_calls[0][1] is True


def test_sound_wave_view_start_processing_timer_uses_active_interval(monkeypatch):
    scheduled_calls = _install_fake_foundation(monkeypatch)
    view = SoundWaveView.__new__(SoundWaveView)
    view._processing_timer = None

    SoundWaveView._start_processing_timer(view)

    assert scheduled_calls[0][0] == 1.0 / WAVE_ANIMATION_FPS_ACTIVE
    assert scheduled_calls[0][1] is True


def test_sound_wave_view_start_done_timer_uses_feedback_interval(monkeypatch):
    scheduled_calls = _install_fake_foundation(monkeypatch)
    view = SoundWaveView.__new__(SoundWaveView)
    view._done_timer = None

    SoundWaveView._start_done_timer(view)

    assert scheduled_calls[0][0] == 1.0 / WAVE_ANIMATION_FPS_FEEDBACK
    assert scheduled_calls[0][1] is True


def test_overlay_controller_text_presentation_skips_duplicate_widget_updates():
    controller = OverlayController.__new__(OverlayController)
    controller._text_field = _FakeTextField()
    controller._text_fonts = {
        "default": "font-default",
        "ghost": "font-ghost",
    }
    controller._text_colors = {
        "muted": "color-muted",
    }
    controller._text_font_key = None
    controller._text_color_key = None
    controller._text_value = None

    OverlayController._apply_text_presentation(
        controller,
        text="Listening ...",
        font_key="default",
        color_key="muted",
    )
    OverlayController._apply_text_presentation(
        controller,
        text="Listening ...",
        font_key="default",
        color_key="muted",
    )
    OverlayController._apply_text_presentation(
        controller,
        text="Listening again",
        font_key="default",
        color_key="muted",
    )

    assert controller._text_field.font_calls == 1
    assert controller._text_field.color_calls == 1
    assert controller._text_field.text_calls == 2
