import math
import sys
import types

import pytest

from config import VAD_THRESHOLD
from ui.animation import BAR_MAX_HEIGHT, listening_bar_heights
from ui.overlay import (
    OVERLAY_INTERIM_MAX_CHARS,
    WAVE_ANIMATION_FPS,
    WAVE_ANIMATION_FPS_IDLE,
    WAVE_AGC_MIN_PEAK,
    WAVE_BAR_COUNT,
    WAVE_BAR_MIN_HEIGHT,
    WAVE_HEIGHT_UPDATE_EPSILON,
    LISTENING_BAR_HEIGHTS,
    OverlayController,
    SoundWaveView,
    _format_recording_interim_text,
)
from ui.overlay_feedback import OverlayContent
from ui.overlay_style import BAR_AREA_WIDTH, SHIMMER_PERIOD_SECONDS, shimmer_track
from utils.state import AppState
from utils.transcript_delivery import PASTE_FAILED


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


def _build_active_recording_view() -> SoundWaveView:
    view = SoundWaveView.__new__(SoundWaveView)
    view._smoothed_level = 0.1
    view._target_level = 0.6
    view._view = _FakeOverlayView(48.0)
    view._center_y = 24.0
    view._last_center_y = 24.0
    view._last_heights = [float(WAVE_BAR_MIN_HEIGHT)] * WAVE_BAR_COUNT
    view._bar_center = (WAVE_BAR_COUNT - 1) / 2
    view._envelope_max_shift_base = max(0.0, view._bar_center * 1.25)
    view._envelope_phase_primary = 0.1
    view._envelope_phase_secondary = 0.2
    view._wander_offset_primary = [0.1 * (i + 1) for i in range(WAVE_BAR_COUNT)]
    view._wander_offset_secondary = [0.2 * (i + 1) for i in range(WAVE_BAR_COUNT)]
    view._wander_offset_primary_sin = tuple(
        math.sin(offset) for offset in view._wander_offset_primary
    )
    view._wander_offset_primary_cos = tuple(
        math.cos(offset) for offset in view._wander_offset_primary
    )
    view._wander_offset_secondary_sin = tuple(
        math.sin(offset) for offset in view._wander_offset_secondary
    )
    view._wander_offset_secondary_cos = tuple(
        math.cos(offset) for offset in view._wander_offset_secondary
    )
    view._height_factors = [1.0] * WAVE_BAR_COUNT
    view._recording_frame_heights = [float(WAVE_BAR_MIN_HEIGHT)] * WAVE_BAR_COUNT
    view.bars = [object()] * WAVE_BAR_COUNT
    view._update_level_timer_interval = lambda: None
    return view


def _install_fake_foundation(monkeypatch):
    scheduled_calls: list[tuple[float, bool, object]] = []
    created_timers: list[object] = []

    class _FakeTimer:
        def __init__(self, interval: float, repeats: bool, block) -> None:
            self.interval = interval
            self.repeats = repeats
            self.block = block
            self.invalidated = 0
            self.tolerance = 0.0

        def invalidate(self) -> None:
            self.invalidated += 1

        def setTolerance_(self, tolerance: float) -> None:
            self.tolerance = tolerance

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


def test_format_recording_duration_uses_minutes_and_padded_seconds():
    from ui.overlay_text import format_recording_duration

    assert format_recording_duration(0) == "0:00"
    assert format_recording_duration(65.4) == "1:05"
    assert format_recording_duration(600) == "10:00"
    assert format_recording_duration(-3) == "0:00"


def test_faded_interim_tail_has_no_ellipsis():
    from ui.overlay_text import split_recording_interim_text

    text = "alpha beta gamma delta"
    earlier, newest = split_recording_interim_text(
        text + " omega", text, max_chars=12, ellipsis=False
    )

    assert earlier + newest == "gamma delta omega"[-12:]
    assert newest == "omega"


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


def _quiet_recording_view():
    view = _build_active_recording_view()
    del view._update_level_timer_interval
    view._target_level = view._smoothed_level = 0.0
    view._agc_peak = WAVE_AGC_MIN_PEAK
    view._level_timer = None
    view._level_timer_interval_seconds = None
    view._level_timer_activity_mode = "idle"
    view.current_animation = "level"
    return view


def test_quiet_level_updates_do_not_create_animation_timers(monkeypatch):
    scheduled, _timers = _install_fake_foundation(monkeypatch)
    view = _quiet_recording_view()

    for _ in range(100):
        view.update_levels(0.0)

    assert scheduled == []
    assert view._level_timer is None


def test_settled_level_frame_stops_timer_and_new_audio_restarts_it(monkeypatch):
    scheduled, timers = _install_fake_foundation(monkeypatch)
    view = _quiet_recording_view()
    frames = []
    view._apply_bar_heights = lambda heights, **_kwargs: frames.append(heights)
    view._start_level_timer()
    view._render_level_frame()

    assert timers[0].invalidated == 1
    assert view._level_timer is None
    assert frames == []
    view.update_levels(0.1)
    assert len(scheduled) == 2
    assert timers[-1].interval == 1.0 / WAVE_ANIMATION_FPS
    timers[-1].block(timers[-1])
    assert max(frames[-1]) > WAVE_BAR_MIN_HEIGHT
    assert view._level_timer is timers[-1]


def test_quiet_bars_finish_settling_before_timer_stops(monkeypatch):
    _scheduled, timers = _install_fake_foundation(monkeypatch)
    view = _quiet_recording_view()
    view._last_heights[0] += 3.0
    frames = []

    def apply(heights, *, center_y):
        frames.append(list(heights))
        view._last_heights[:] = heights
        view._last_center_y = center_y

    view._apply_bar_heights = apply
    view.update_levels(0.0)
    assert view._level_timer is not None
    for _ in range(200):
        if view._level_timer is None:
            break
        view._render_level_frame()

    assert frames
    assert view._level_timer is None
    assert timers[-1].invalidated == 1
    assert all(
        abs(height - WAVE_BAR_MIN_HEIGHT) < WAVE_HEIGHT_UPDATE_EPSILON
        for height in view._last_heights
    )


def test_quiet_geometry_change_still_gets_rendered(monkeypatch):
    _install_fake_foundation(monkeypatch)
    view = _quiet_recording_view()
    view._center_y = 30.0
    centers = []
    view._apply_bar_heights = lambda _heights, *, center_y: centers.append(center_y)

    view.update_levels(0.0)
    assert view._level_timer is not None
    view._render_level_frame()

    assert centers == [30.0]


def test_sound_wave_view_level_frame_skips_settled_noop_work(monkeypatch):
    view = SoundWaveView.__new__(SoundWaveView)
    view._smoothed_level = 0.0
    view._target_level = 0.0
    view._level_timer = None
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


def test_sound_wave_view_level_frame_reuses_recording_height_buffer(monkeypatch):
    view = _build_active_recording_view()
    seen_ids: list[int] = []
    view._apply_bar_heights = lambda heights, *, center_y: seen_ids.append(id(heights))

    time_values = iter((10.0, 10.1))
    monkeypatch.setattr("ui.overlay.time.perf_counter", lambda: next(time_values))

    SoundWaveView._render_level_frame(view)
    SoundWaveView._render_level_frame(view)

    assert len(seen_ids) == 2
    assert seen_ids[0] == seen_ids[1]


def test_sound_wave_view_level_frame_uses_cached_trig_tables(monkeypatch):
    view = _build_active_recording_view()
    view._apply_bar_heights = lambda *_args, **_kwargs: None

    sin_calls = 0
    cos_calls = 0
    real_sin = math.sin
    real_cos = math.cos

    def counting_sin(value: float) -> float:
        nonlocal sin_calls
        sin_calls += 1
        return real_sin(value)

    def counting_cos(value: float) -> float:
        nonlocal cos_calls
        cos_calls += 1
        return real_cos(value)

    monkeypatch.setattr("ui.overlay.math.sin", counting_sin)
    monkeypatch.setattr("ui.overlay.math.cos", counting_cos)
    monkeypatch.setattr("ui.overlay.time.perf_counter", lambda: 10.0)

    SoundWaveView._render_level_frame(view)

    assert sin_calls == 4
    assert cos_calls == 2




class _FakeLayer:
    def __init__(self) -> None:
        self.hidden = False
        self.color = None

    def setHidden_(self, hidden: bool) -> None:
        self.hidden = hidden

    def setBackgroundColor_(self, color) -> None:
        self.color = color


class _FakeModeView(_FakeLayer):
    pass


def _mode_view(monkeypatch):
    _FakeCATransaction.reset()
    monkeypatch.setitem(
        sys.modules, "Quartz", types.SimpleNamespace(CATransaction=_FakeCATransaction)
    )
    _install_fake_foundation(monkeypatch)
    view = _quiet_recording_view()
    view._view = _FakeModeView()
    view._view.frame = lambda: _FakeOverlayFrame(24.0)
    view._line = _FakeLayer()
    view._steps = [_FakeLayer(), _FakeLayer()]
    view._step_colors = {"phase1": ("on", "pending"), "phase2": ("done", "on")}
    view.bars = [_FakeLayer() for _ in range(WAVE_BAR_COUNT)]
    view._bar_colors = {"level": "white", "low": "grey", "line": "dim"}
    view.current_animation = None
    colors = []
    heights = []
    view.set_bar_color = colors.append
    view._set_bar_height = lambda index, height, **_kwargs: heights.append(height)
    return view, colors, heights


def test_static_modes_start_no_timer_and_toggle_visibility(monkeypatch):
    view, colors, heights = _mode_view(monkeypatch)

    view.show_mode("low")
    assert view._level_timer is None
    assert colors == ["grey"]
    assert heights[-WAVE_BAR_COUNT:] == list(LISTENING_BAR_HEIGHTS)
    assert view._line.hidden is True and not view.bars[0].hidden

    view.show_mode("line")
    assert view._line.hidden is False and view.bars[0].hidden
    assert all(step.hidden for step in view._steps)
    assert view._level_timer is None

    view.show_mode("phase2")
    assert view._line.hidden is True
    assert [(step.hidden, step.color) for step in view._steps] == [
        (False, "done"),
        (False, "on"),
    ]
    assert view._level_timer is None

    view.show_mode("none")
    assert view._view.hidden is True


class _FakeShimmerLayer(_FakeLayer):
    def __init__(self) -> None:
        super().__init__()
        self.frame = None
        self.animations: dict[str, object] = {}

    def setFrame_(self, frame) -> None:
        self.frame = frame

    def addAnimation_forKey_(self, animation, key: str) -> None:
        self.animations[key] = animation

    def removeAnimationForKey_(self, key: str) -> None:
        self.animations.pop(key, None)


class _FakeBasicAnimation:
    @classmethod
    def animationWithKeyPath_(cls, key_path: str):
        animation = cls()
        animation.key_path = key_path
        return animation

    def __getattr__(self, name: str):
        # setFromValue_, setDuration_, ... record their value as from_value, duration, ...
        if not name.startswith("set"):
            raise AttributeError(name)
        field = name[3:].rstrip("_")
        return lambda value: setattr(self, field[0].lower() + field[1:], value)


def _shimmer_view(monkeypatch, *, reduce_motion: bool = False):
    view, _colors, _heights = _mode_view(monkeypatch)
    monkeypatch.setitem(
        sys.modules,
        "Quartz",
        types.SimpleNamespace(
            CATransaction=_FakeCATransaction,
            CABasicAnimation=_FakeBasicAnimation,
            CAMediaTimingFunction=types.SimpleNamespace(functionWithName_=lambda name: name),
            kCAMediaTimingFunctionEaseInEaseOut="easeInEaseOut",
        ),
    )
    monkeypatch.setattr("ui.overlay._reduce_motion", lambda: reduce_motion)
    view._shimmer_track = _FakeShimmerLayer()
    view._shimmer_band = _FakeShimmerLayer()
    view._shimmer_active_color = "shimmer-base"
    return view


def test_processing_lines_shimmer_only_while_shown(monkeypatch):
    view = _shimmer_view(monkeypatch)

    y = view._wave_center_y() - 1.5
    view.show_mode("line")
    animation = view._shimmer_band.animations["shimmer"]
    assert animation.key_path == "position.x"
    assert animation.duration == SHIMMER_PERIOD_SECONDS
    assert animation.repeatCount == float("inf")
    assert animation.timingFunction == "easeInEaseOut"
    assert view._shimmer_track.hidden is False
    assert view._shimmer_track.frame == ((0.0, y), (float(BAR_AREA_WIDTH), 3))

    view.show_mode("phase2")
    x, width = shimmer_track("phase2")
    assert view._shimmer_track.frame == ((x, y), (width, 3))
    assert [step.color for step in view._steps] == ["done", "shimmer-base"]
    assert view._shimmer_band.animations["shimmer"].toValue == width + 11

    for mode in ("level", "none"):
        view.show_mode(mode)
        assert view._shimmer_band.animations == {}
        assert view._shimmer_track.hidden is True

    view.show_mode("line")
    view.stop_animating()
    assert view._shimmer_band.animations == {}
    assert view._shimmer_track.hidden is True


def test_reduce_motion_keeps_the_static_line(monkeypatch):
    view = _shimmer_view(monkeypatch, reduce_motion=True)

    view.show_mode("phase1")

    assert view._shimmer_band.animations == {}
    assert view._shimmer_track.hidden is True
    assert [step.color for step in view._steps] == ["on", "pending"]


def test_audio_level_switches_to_level_mode(monkeypatch):
    view, colors, _heights = _mode_view(monkeypatch)
    view.show_mode("low")

    view.update_levels(0.5)

    assert view.current_animation == "level"
    assert colors[-1] == "white"
    assert view._level_timer is not None


def _listening_view(monkeypatch):
    """Listening view that commits frames through the real subpixel gate."""
    _FakeCATransaction.reset()
    monkeypatch.setitem(
        sys.modules, "Quartz", types.SimpleNamespace(CATransaction=_FakeCATransaction)
    )
    _scheduled, timers = _install_fake_foundation(monkeypatch)
    view = _quiet_recording_view()
    view.current_animation = "low"
    view.bars = [_FakeBar() for _ in range(WAVE_BAR_COUNT)]
    view._rect_factory = lambda *rect: rect
    view._last_heights = list(LISTENING_BAR_HEIGHTS)
    return view, timers


def _run_level_frames(view, limit: int = 400) -> int:
    for frame in range(limit):
        if view._level_timer is None:
            return frame
        view._render_level_frame()
    return limit


def test_listening_level_meter_rises_settles_and_returns_to_rest(monkeypatch):
    view, timers = _listening_view(monkeypatch)

    view.update_listening_level(VAD_THRESHOLD * 2)  # clipped to full amplitude
    assert view._level_timer is not None
    assert _run_level_frames(view) < 400
    peak = listening_bar_heights(1.0)
    # Settles within the level epsilon (~a quarter pixel) of the full amplitude.
    assert all(abs(h - p) < 2 * WAVE_HEIGHT_UPDATE_EPSILON for h, p in zip(view._last_heights, peak))
    assert max(view._last_heights) <= max(LISTENING_BAR_HEIGHTS) + 0.35 * BAR_MAX_HEIGHT
    assert timers[-1].invalidated == 1  # steady level: no frames until it changes

    view.update_listening_level(0.0)
    assert _run_level_frames(view) < 400
    assert all(
        abs(h - rest) < WAVE_HEIGHT_UPDATE_EPSILON
        for h, rest in zip(view._last_heights, LISTENING_BAR_HEIGHTS)
    )


def test_listening_room_noise_below_the_gate_starts_no_timer(monkeypatch):
    view, timers = _listening_view(monkeypatch)

    for _ in range(50):
        view.update_listening_level(0.001)

    assert timers == [] and view._level_timer is None


def test_listening_level_is_ignored_outside_the_low_row(monkeypatch):
    view, timers = _listening_view(monkeypatch)
    view.current_animation = "line"

    view.update_listening_level(VAD_THRESHOLD)

    assert timers == [] and view._target_level == 0.0


def test_recording_grows_out_of_the_listening_row(monkeypatch):
    view, colors, heights = _mode_view(monkeypatch)
    view.show_mode("low")
    mid = list(listening_bar_heights(0.5))
    view._last_heights = list(mid)
    view._smoothed_level = 0.5
    heights.clear()

    view.show_mode("level")

    assert colors[-1] == "white"
    assert heights == []  # no reset to the minimum height
    assert view._last_heights == mid and view._smoothed_level == 0.5
    assert view._level_timer is not None


class _FakeWaveModes:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.levels: list[tuple[str, float]] = []

    def update_levels(self, level: float) -> None:
        self.levels.append(("recording", level))

    def update_listening_level(self, level: float) -> None:
        self.levels.append(("listening", level))

    def show_mode(self, mode: str) -> None:
        self.events.append(f"bars:{mode}")

    def stop_animating(self) -> None:
        self.events.append("stop")


class _FakeWindow:
    def __init__(self) -> None:
        self.ignores_mouse = True

    def setIgnoresMouseEvents_(self, ignores: bool) -> None:
        self.ignores_mouse = ignores


def _controller(events: list[str] | None = None) -> OverlayController:
    events = [] if events is None else events
    controller = OverlayController.__new__(OverlayController)
    controller.window = _FakeWindow()
    controller._current_state = AppState.IDLE
    controller._last_state_payload = None
    controller._feedback_timer = None
    controller._previous_interim = ""
    controller._persistent = False
    controller._wave_view = _FakeWaveModes(events)
    # The recording clock has its own test; keep timer lists about feedback.
    controller._start_clock = lambda: None
    controller._stop_clock = lambda: None
    controller.rendered = []
    controller._render = lambda content, interim: controller.rendered.append(
        (content, interim)
    )
    controller._fade_in = lambda: events.append("show")
    controller._fade_out = lambda: events.append("hide")
    return controller


def test_audio_level_routes_by_state():
    controller = _controller()

    for state in (AppState.LISTENING, AppState.RECORDING, AppState.TRANSCRIBING):
        controller._current_state = state
        controller.update_audio_level(0.01)

    assert controller._wave_view.levels == [("listening", 0.01), ("recording", 0.01)]


def test_update_state_skips_duplicate_transition_work(monkeypatch):
    events: list[str] = []
    controller = _controller(events)

    controller.update_state(AppState.RECORDING, "alpha")
    controller.update_state(AppState.RECORDING, "alpha")

    assert events == ["bars:level", "show"]
    assert len(controller.rendered) == 1


def test_recording_interim_emphasizes_newest_words():
    controller = _controller()

    controller.update_state(AppState.RECORDING, "hello")
    controller.update_state(AppState.RECORDING, "hello world")

    assert controller.rendered[-1][1] == ("hello ", "world")


def test_states_map_to_bars_badges_and_copy(monkeypatch):
    _install_fake_foundation(monkeypatch)
    controller = _controller()
    cases = [
        (AppState.LISTENING, None, OverlayContent("low", "Listening…")),
        (AppState.TRANSCRIBING, None, OverlayContent("line", "Transcribing…")),
        (AppState.DONE, "Transcript pasted", OverlayContent(badge="Pasted", tone="success")),
        (
            AppState.NO_SPEECH,
            None,
            OverlayContent(text="Nothing was pasted", badge="No speech", tone="warn"),
        ),
        (
            AppState.ERROR,
            " microphone\nmissing ",
            OverlayContent(
                text="Microphone unavailable",
                badge="Error",
                tone="error",
                dismissible=True,
                meta="Settings",
                action="settings",
            ),
        ),
    ]
    for state, text, expected in cases:
        controller.update_state(state, text)
        assert controller.rendered[-1][0] == expected


def test_success_confirmation_survives_immediate_ready_state(monkeypatch):
    scheduled, timers = _install_fake_foundation(monkeypatch)
    events: list[str] = []
    controller = _controller(events)

    controller.update_state(AppState.DONE, "Transcript pasted")
    controller.update_state(AppState.IDLE)

    assert events == ["bars:none", "show"]
    assert scheduled[0][0] == 0.8
    timers[0].block(timers[0])
    assert events == ["bars:none", "show", "stop", "hide"]
    assert controller._feedback_timer is None


def test_native_success_timer_cleans_up_after_ready():
    pytest.importorskip("Foundation")
    events: list[str] = []
    controller = _controller(events)
    controller.update_state(AppState.DONE)
    timer = controller._feedback_timer
    try:
        controller.update_state(AppState.IDLE)
        timer.fire()
        assert events == ["bars:none", "show", "stop", "hide"]
        assert controller._feedback_timer is None
    finally:
        timer.invalidate()


def test_new_recording_replaces_held_confirmation_and_rejects_stale_fade(monkeypatch):
    _scheduled, timers = _install_fake_foundation(monkeypatch)
    events: list[str] = []
    controller = _controller(events)
    controller.update_state(AppState.DONE)
    controller.update_state(AppState.IDLE)

    controller.update_state(AppState.LISTENING)
    timers[0].block(timers[0])

    assert controller._current_state == AppState.LISTENING
    assert timers[0].invalidated == 1
    assert "bars:low" in events
    assert "hide" not in events


def test_older_confirmation_cannot_hide_a_newer_success(monkeypatch):
    _scheduled, timers = _install_fake_foundation(monkeypatch)
    events: list[str] = []
    controller = _controller(events)
    controller.update_state(AppState.DONE, "first")
    controller.update_state(AppState.IDLE)
    controller.update_state(AppState.LISTENING)
    controller.update_state(AppState.DONE, "second")
    controller.update_state(AppState.IDLE)

    timers[0].block(timers[0])
    assert "hide" not in events
    assert controller._feedback_timer is timers[1]
    timers[1].block(timers[1])
    assert events.count("hide") == 1
    assert events[-2:] == ["stop", "hide"]




def test_render_skips_identical_presentations():
    controller = OverlayController.__new__(OverlayController)
    controller._presentation = None
    calls = []
    controller._text_field = types.SimpleNamespace(
        setAttributedStringValue_=calls.append, setHidden_=lambda _hidden: None
    )
    controller._badge_view = types.SimpleNamespace(setHidden_=lambda _hidden: None)
    controller._dismiss_field = types.SimpleNamespace(setHidden_=lambda _hidden: None)
    metas = []
    controller._meta_field = types.SimpleNamespace(
        setStringValue_=metas.append,
        setHidden_=lambda _hidden: None,
        setTextColor_=lambda _color: None,
    )
    controller._colors = {"muted": "muted"}
    controller._meta_text = "0:00"
    controller._attributed_text = lambda parts: parts
    layouts = []
    controller._layout = lambda *args: layouts.append(args)
    content = OverlayContent("low", "Listening…")

    controller._render(content, ("", ""))
    controller._render(content, ("", ""))
    controller._meta_text = "0:01"  # clock tick: meta only, text untouched
    controller._render(content, ("", ""))
    controller._render(content, ("", "new words"))

    assert calls == [
        (("Listening…", "muted"),),
        (("", "interim"), ("new words", "text")),
    ]
    assert metas == ["0:00", "0:01"]
    assert [args[2] for args in layouts] == [False, False, True]


def test_recording_clock_ticks_at_1hz_only_while_listening_or_recording(monkeypatch):
    scheduled, timers = _install_fake_foundation(monkeypatch)
    now = [50.0]
    monkeypatch.setattr("ui.overlay.time.monotonic", lambda: now[0])
    controller = _controller()
    del controller._start_clock, controller._stop_clock
    controller._clock_timer = None
    controller._meta_text = ""
    controller._presentation = None
    metas = []

    def render(content, interim):
        metas.append(controller._meta_text)
        controller._presentation = (content, interim, controller._meta_text)

    controller._render = render

    controller.update_state(AppState.LISTENING)
    controller.update_state(AppState.RECORDING)
    assert [(interval, repeats) for interval, repeats, _ in scheduled] == [(1.0, True)]
    assert timers[0].tolerance > 0
    now[0] = 121.0
    timers[0].block(timers[0])
    assert metas == ["0:00", "0:00", "1:11"]

    controller.update_state(AppState.TRANSCRIBING)
    assert timers[0].invalidated == 1
    assert metas[-1] == ""
    timers[0].block(timers[0])  # a stale tick after stop is ignored
    assert metas[-1] == ""
    assert timers[0].invalidated == 2


@pytest.mark.parametrize(
    "message", ["Transcription timed out", PASTE_FAILED]
)
def test_errors_stay_visible_through_idle_until_clicked(monkeypatch, message):
    scheduled, _timers = _install_fake_foundation(monkeypatch)
    events: list[str] = []
    controller = _controller(events)

    controller.update_state(AppState.ERROR, message)
    error_content = controller.rendered[-1][0]
    assert error_content.dismissible is True
    assert controller.window.ignores_mouse is False
    controller.update_state(AppState.IDLE)

    assert scheduled == []
    assert "hide" not in events
    assert controller.rendered[-1][0] == error_content
    controller._dismiss()
    assert events[-2:] == ["stop", "hide"]
    assert controller.window.ignores_mouse is True
    controller._dismiss()
    assert events.count("hide") == 1


def test_next_recording_replaces_persistent_error_and_restores_click_through(monkeypatch):
    _install_fake_foundation(monkeypatch)
    events: list[str] = []
    controller = _controller(events)
    controller.update_state(AppState.ERROR, "boom")
    controller.update_state(AppState.IDLE)

    controller.update_state(AppState.LISTENING)

    assert controller.window.ignores_mouse is True
    assert events[-2:] == ["bars:low", "show"]
    assert controller.rendered[-1][0] == OverlayContent("low", "Listening…")
    controller.update_state(AppState.IDLE)
    assert events[-2:] == ["stop", "hide"]


def test_new_error_after_dismissal_is_shown_again(monkeypatch):
    _install_fake_foundation(monkeypatch)
    events: list[str] = []
    controller = _controller(events)
    controller.update_state(AppState.ERROR, "first")
    controller._dismiss()

    controller.update_state(AppState.ERROR, "second")

    assert events[-1] == "show"
    assert controller.window.ignores_mouse is False


def test_native_overlay_view_routes_every_click_to_dismiss():
    appkit = pytest.importorskip("AppKit")
    from ui.overlay import _click_view_class

    view = _click_view_class().alloc().initWithFrame_(appkit.NSMakeRect(0, 0, 200, 44))
    label = appkit.NSTextField.labelWithString_("Error")
    label.setFrame_(appkit.NSMakeRect(10, 10, 60, 20))
    view.addSubview_(label)
    clicks = []
    view.pulsescribe_on_click = clicks.append

    assert view.hitTest_(appkit.NSMakePoint(20, 20)) is view
    assert view.hitTest_(appkit.NSMakePoint(500, 20)) is None
    view.mouseDown_(None)
    assert clicks == [None]  # no event, no position


def test_same_state_payload_can_switch_to_the_refine_steps():
    events: list[str] = []
    controller = _controller(events)

    controller.update_state(AppState.TRANSCRIBING, "Finishing...")
    controller.update_state(AppState.TRANSCRIBING, "Transcribing, then refining")

    assert [event for event in events if event.startswith("bars:")] == [
        "bars:line",
        "bars:phase1",
    ]
    assert controller.rendered[-1][0].meta == "1 of 2"



@pytest.mark.parametrize(("x", "opened"), [(300.0, True), (100.0, False), (None, False)])
def test_click_on_settings_link_opens_settings_and_any_click_dismisses(
    monkeypatch, x, opened
):
    _install_fake_foundation(monkeypatch)
    events: list[str] = []
    controller = _controller(events)
    opens = []
    controller._on_open_settings = lambda: opens.append(True)
    controller.update_state(AppState.ERROR, "OPENAI_API_KEY not set")
    controller._link_span = (290.0, 340.0)

    controller._on_click(x)

    assert opens == ([True] if opened else [])
    assert events[-1] == "hide"
    assert controller.window.ignores_mouse is True
