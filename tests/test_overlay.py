import sys
import types

from ui.overlay import (
    OVERLAY_INTERIM_MAX_CHARS,
    WAVE_ANIMATION_FPS,
    WAVE_ANIMATION_FPS_ACTIVE,
    WAVE_ANIMATION_FPS_FEEDBACK,
    WAVE_ANIMATION_FPS_IDLE,
    WAVE_BAR_MIN_HEIGHT,
    WAVE_HEIGHT_UPDATE_EPSILON,
    OverlayController,
    SoundWaveView,
    _format_recording_interim_text,
)
from utils.state import AppState


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


class _FakeWaveView:
    def __init__(self) -> None:
        self.recording_calls = 0

    def start_recording_animation(self) -> None:
        self.recording_calls += 1


class _FakeCATransaction:
    begin_calls = 0
    commit_calls = 0
    disable_calls = 0

    @classmethod
    def reset(cls) -> None:
        cls.begin_calls = 0
        cls.commit_calls = 0
        cls.disable_calls = 0

    @classmethod
    def begin(cls) -> None:
        cls.begin_calls += 1

    @classmethod
    def commit(cls) -> None:
        cls.commit_calls += 1

    @classmethod
    def setDisableActions_(cls, _value: bool) -> None:
        cls.disable_calls += 1


class _FakeOverlayFrame:
    def __init__(self, height: float) -> None:
        self.size = types.SimpleNamespace(height=height)


class _FakeOverlayView:
    def __init__(self, height: float) -> None:
        self._frame = _FakeOverlayFrame(height)

    def frame(self):
        return self._frame


class _FakeAnimationLogic:
    def __init__(self, normalized: float) -> None:
        self.normalized = normalized

    def calculate_bar_normalized(self, _index: int, _time_value: float, _state: str) -> float:
        return self.normalized


def _install_fake_foundation(monkeypatch):
    scheduled_calls: list[tuple[float, bool, object]] = []
    created_timers: list[object] = []

    class _FakeTimer:
        def __init__(self, interval: float, repeats: bool, block) -> None:
            self.interval = interval
            self.repeats = repeats
            self.block = block
            self.invalidated = 0

        def invalidate(self) -> None:
            self.invalidated += 1

    class _FakeNSTimer:
        @staticmethod
        def scheduledTimerWithTimeInterval_repeats_block_(
            interval: float, repeats: bool, block
        ):
            scheduled_calls.append((interval, repeats, block))
            timer = _FakeTimer(interval, repeats, block)
            created_timers.append(timer)
            return timer

    monkeypatch.setitem(
        sys.modules,
        "Foundation",
        types.SimpleNamespace(NSTimer=_FakeNSTimer),
    )
    return scheduled_calls, created_timers


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


def test_format_recording_interim_text_trims_from_tail_without_full_normalization():
    text = ("alpha   beta   " * 80) + "  final   words  here "

    formatted = _format_recording_interim_text(text, max_chars=20)

    assert formatted == "... final words here"


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


def test_sound_wave_view_done_frame_skips_transaction_when_nothing_changes(
    monkeypatch,
):
    _FakeCATransaction.reset()
    monkeypatch.setitem(
        sys.modules,
        "Quartz",
        types.SimpleNamespace(CATransaction=_FakeCATransaction),
    )

    view = SoundWaveView.__new__(SoundWaveView)
    view._done_start_time = 1.0
    view._view = _FakeOverlayView(48.0)
    view.bars = [_FakeBar(), _FakeBar()]
    view._bar_positions = [5.0, 15.0]
    view._last_center_y = 24.0
    view._last_heights = [WAVE_BAR_MIN_HEIGHT, WAVE_BAR_MIN_HEIGHT]
    view._anim = _FakeAnimationLogic(0.0)

    monkeypatch.setattr("ui.overlay.time.perf_counter", lambda: 1.5)

    SoundWaveView._render_done_frame(view)

    assert _FakeCATransaction.begin_calls == 0
    assert _FakeCATransaction.commit_calls == 0
    assert [bar.bounds_calls for bar in view.bars] == [0, 0]
    assert [bar.position_calls for bar in view.bars] == [0, 0]


def test_sound_wave_view_done_frame_keeps_transaction_for_reposition_only(
    monkeypatch,
):
    _FakeCATransaction.reset()
    monkeypatch.setitem(
        sys.modules,
        "Quartz",
        types.SimpleNamespace(CATransaction=_FakeCATransaction),
    )

    view = SoundWaveView.__new__(SoundWaveView)
    view._done_start_time = 1.0
    view._view = _FakeOverlayView(52.0)
    view.bars = [_FakeBar(), _FakeBar()]
    view._bar_positions = [5.0, 15.0]
    view._last_center_y = 24.0
    view._last_heights = [WAVE_BAR_MIN_HEIGHT, WAVE_BAR_MIN_HEIGHT]
    view._anim = _FakeAnimationLogic(0.0)

    monkeypatch.setattr("ui.overlay.time.perf_counter", lambda: 1.5)

    SoundWaveView._render_done_frame(view)

    assert _FakeCATransaction.begin_calls == 1
    assert _FakeCATransaction.commit_calls == 1
    assert [bar.bounds_calls for bar in view.bars] == [0, 0]
    assert [bar.position_calls for bar in view.bars] == [1, 1]


def test_sound_wave_view_start_level_timer_uses_recording_interval(monkeypatch):
    scheduled_calls, _created_timers = _install_fake_foundation(monkeypatch)
    view = SoundWaveView.__new__(SoundWaveView)
    view._level_timer = None
    view._target_level = 1.0
    view._smoothed_level = 1.0
    view._level_timer_activity_mode = "active"

    SoundWaveView._start_level_timer(view)

    assert scheduled_calls[0][0] == 1.0 / WAVE_ANIMATION_FPS
    assert scheduled_calls[0][1] is True


def test_sound_wave_view_start_processing_timer_uses_active_interval(monkeypatch):
    scheduled_calls, _created_timers = _install_fake_foundation(monkeypatch)
    view = SoundWaveView.__new__(SoundWaveView)
    view._processing_timer = None

    SoundWaveView._start_processing_timer(view)

    assert scheduled_calls[0][0] == 1.0 / WAVE_ANIMATION_FPS_ACTIVE
    assert scheduled_calls[0][1] is True


def test_sound_wave_view_start_done_timer_uses_feedback_interval(monkeypatch):
    scheduled_calls, _created_timers = _install_fake_foundation(monkeypatch)
    view = SoundWaveView.__new__(SoundWaveView)
    view._done_timer = None

    SoundWaveView._start_done_timer(view)

    assert scheduled_calls[0][0] == 1.0 / WAVE_ANIMATION_FPS_FEEDBACK
    assert scheduled_calls[0][1] is True


def test_sound_wave_view_start_level_timer_uses_idle_interval_for_quiet_input(
    monkeypatch,
):
    scheduled_calls, _created_timers = _install_fake_foundation(monkeypatch)
    view = SoundWaveView.__new__(SoundWaveView)
    view._level_timer = None
    view._target_level = 0.0
    view._smoothed_level = 0.0
    view._level_timer_activity_mode = "idle"

    SoundWaveView._start_level_timer(view)

    assert scheduled_calls[0][0] == 1.0 / WAVE_ANIMATION_FPS_IDLE
    assert scheduled_calls[0][1] is True


def test_sound_wave_view_level_timer_reschedules_when_activity_mode_changes(
    monkeypatch,
):
    scheduled_calls, created_timers = _install_fake_foundation(monkeypatch)
    view = SoundWaveView.__new__(SoundWaveView)
    view._target_level = 0.0
    view._smoothed_level = 0.0
    view._level_timer = None
    view._level_timer_interval_seconds = None
    view._level_timer_activity_mode = "idle"

    SoundWaveView._start_level_timer(view)

    view._target_level = 0.2
    view._smoothed_level = 0.2
    SoundWaveView._update_level_timer_interval(view)

    assert len(scheduled_calls) == 2
    assert scheduled_calls[0][0] == 1.0 / WAVE_ANIMATION_FPS_IDLE
    assert scheduled_calls[1][0] == 1.0 / WAVE_ANIMATION_FPS
    assert created_timers[0].invalidated == 1
    assert view._level_timer is created_timers[1]


def test_sound_wave_view_level_frame_skips_settled_noop_work(monkeypatch):
    view = SoundWaveView.__new__(SoundWaveView)
    view._smoothed_level = 0.0
    view._target_level = 0.0
    view._view = _FakeOverlayView(48.0)
    view._last_center_y = 24.0
    view._last_heights = [float(WAVE_BAR_MIN_HEIGHT)] * 10
    view._update_level_timer_interval = lambda: None
    view._apply_bar_heights = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        AssertionError("settled frame should not touch bar updates")
    )

    monkeypatch.setattr(
        "ui.overlay.time.perf_counter",
        lambda: (_ for _ in ()).throw(
            AssertionError("settled frame should return before timing/math work")
        ),
    )

    SoundWaveView._render_level_frame(view)


def test_sound_wave_view_level_frame_keeps_rendering_until_bars_settle(monkeypatch):
    view = SoundWaveView.__new__(SoundWaveView)
    view._smoothed_level = 0.0
    view._target_level = 0.0
    view._view = _FakeOverlayView(48.0)
    view._last_center_y = 24.0
    view._last_heights = [float(WAVE_BAR_MIN_HEIGHT) + 1.0] + [float(WAVE_BAR_MIN_HEIGHT)] * 9
    view._bar_center = 4.5
    view._envelope_max_shift_base = max(0.0, view._bar_center * 1.25)
    view._envelope_phase_primary = 0.1
    view._envelope_phase_secondary = 0.2
    view._wander_offset_primary = [0.0] * 10
    view._wander_offset_secondary = [0.0] * 10
    view._height_factors = [1.0] * 10
    view.bars = [object()] * 10
    view._update_level_timer_interval = lambda: None

    apply_calls: list[tuple[list[float], float]] = []
    view._apply_bar_heights = (
        lambda heights, *, center_y: apply_calls.append((heights, center_y))
    )

    monkeypatch.setattr("ui.overlay.time.perf_counter", lambda: 10.0)

    SoundWaveView._render_level_frame(view)

    assert len(apply_calls) == 1
    assert apply_calls[0][1] == 24.0


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


def test_overlay_controller_update_state_skips_duplicate_transition_work():
    controller = OverlayController.__new__(OverlayController)
    controller.window = object()
    controller._wave_view = _FakeWaveView()
    controller._current_state = AppState.IDLE
    controller._last_state_payload = None
    controller._feedback_timer = None
    controller._apply_text_presentation = lambda **_kwargs: None
    fade_calls: list[str] = []
    controller._fade_in = lambda: fade_calls.append("in")

    OverlayController.update_state(controller, AppState.RECORDING, "alpha")
    OverlayController.update_state(controller, AppState.RECORDING, "alpha")

    assert controller._wave_view.recording_calls == 1
    assert fade_calls == ["in"]
