import os
import pytest
import subprocess
import sys
import types

pytest.importorskip("PySide6")

from config import VAD_THRESHOLD
from ui.animation import (
    BAR_MAX_HEIGHT,
    LISTENING_BAR_HEIGHTS,
    LISTENING_LEVEL_SPAN,
    AnimationLogic,
    listening_bar_heights,
)

from utils.transcript_delivery import PASTE_FAILED
from ui.overlay_pyside6 import (
    BAR_COUNT,
    BAR_HEIGHT_UPDATE_EPSILON,
    BAR_MIN_HEIGHT,
    FEEDBACK_DISPLAY_MS,
    INTERIM_DIRECT_UPDATE_GRACE_S,
    FRAME_MS,
    INTERIM_POLL_DIRECT_INTERVAL_MS,
    INTERIM_POLL_INTERVAL_MS,
    INTERIM_POLL_MAX_CHARS,
    INTERIM_POLL_STABLE_INTERVAL_MS,
    INTERIM_POLL_STABLE_THRESHOLD,
    PySide6OverlayController,
    PySide6OverlayWidget,
    _format_recording_interim_text,
)
from ui.overlay_feedback import OverlayContent, transcribing_status
from ui.overlay_style import (
    BAR_AREA_WIDTH,
    PHASE_STEP_WIDTH,
    compute_overlay_layout,
    shimmer_track,
)


class _FakeTimer:
    def __init__(self):
        self.stop_calls = 0
        self.start_calls: list[int] = []

    def stop(self) -> None:
        self.stop_calls += 1

    def start(self, interval_ms: int) -> None:
        self.start_calls.append(interval_ms)


class _FakeWidget:
    def __init__(self):
        self.current_state = "RECORDING"
        self.seen_interim: list[str] = []
        self.state_calls: list[tuple[str, str]] = []

    def update_state(self, state: str, text: str | None = None) -> None:
        self.state_calls.append((state, text or ""))

    def update_interim_text(self, text: str) -> None:
        self.seen_interim.append(text)


class _FakeAnimationTimer:
    def __init__(self, *, active: bool = False, interval_ms: int = 0):
        self.active = active
        self.interval_ms = interval_ms
        self.start_calls: list[int] = []
        self.set_interval_calls: list[int] = []

    def isActive(self) -> bool:
        return self.active

    def start(self, interval_ms: int | None = None) -> None:
        self.active = True
        if interval_ms is not None:
            self.interval_ms = interval_ms
        self.start_calls.append(interval_ms)

    def stop(self) -> None:
        self.active = False

    def interval(self) -> int:
        return self.interval_ms

    def setInterval(self, interval_ms: int) -> None:
        self.interval_ms = interval_ms
        self.set_interval_calls.append(interval_ms)


class _FakeInterimTimer:
    def __init__(self, *, active: bool = False, interval_ms: int = 0):
        self.active = active
        self.interval_ms = interval_ms
        self.start_calls: list[int] = []
        self.stop_calls = 0
        self.set_interval_calls: list[int] = []

    def isActive(self) -> bool:
        return self.active

    def start(self, interval_ms: int) -> None:
        self.active = True
        self.interval_ms = interval_ms
        self.start_calls.append(interval_ms)

    def stop(self) -> None:
        self.active = False
        self.stop_calls += 1

    def interval(self) -> int:
        return self.interval_ms

    def setInterval(self, interval_ms: int) -> None:
        self.interval_ms = interval_ms
        self.set_interval_calls.append(interval_ms)


def test_fade_out_timer_is_reused(monkeypatch):
    widget = PySide6OverlayWidget.__new__(PySide6OverlayWidget)
    widget._fade_out_timer = _FakeTimer()
    widget._state = "DONE"

    monkeypatch.setattr(
        "ui.overlay_pyside6.QTimer",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("QTimer should not be re-created in _start_fade_out_timer")
        ),
    )

    PySide6OverlayWidget._start_fade_out_timer(widget)
    PySide6OverlayWidget._start_fade_out_timer(widget)

    assert widget._fade_out_timer.stop_calls == 2
    assert widget._fade_out_timer.start_calls == [
        FEEDBACK_DISPLAY_MS,
        FEEDBACK_DISPLAY_MS,
    ]




def test_poll_interim_file_uses_mtime_cache(tmp_path):
    interim_file = tmp_path / "interim.txt"
    interim_file.write_text("first", encoding="utf-8")

    controller = PySide6OverlayController.__new__(PySide6OverlayController)
    controller._running = True
    controller._interim_file = interim_file
    controller._widget = _FakeWidget()
    controller._last_interim_text = ""
    controller._last_interim_signature = None

    PySide6OverlayController._poll_interim_file(controller)
    PySide6OverlayController._poll_interim_file(controller)

    assert controller._widget.seen_interim == ["first"]

    interim_file.write_text("second", encoding="utf-8")
    PySide6OverlayController._poll_interim_file(controller)
    assert controller._widget.seen_interim == ["first", "second"]

    controller._widget.current_state = "IDLE"
    controller._last_interim_signature = (123, 5)
    controller._last_interim_text = "stale"
    PySide6OverlayController._poll_interim_file(controller)
    assert controller._last_interim_signature is None
    assert controller._last_interim_text == ""


def test_poll_interim_file_clears_stale_text_when_file_becomes_empty(tmp_path):
    interim_file = tmp_path / "interim.txt"
    interim_file.write_text("first", encoding="utf-8")

    controller = PySide6OverlayController.__new__(PySide6OverlayController)
    controller._running = True
    controller._interim_file = interim_file
    controller._widget = _FakeWidget()
    controller._last_interim_text = ""
    controller._last_interim_signature = None

    PySide6OverlayController._poll_interim_file(controller)
    interim_file.write_text("", encoding="utf-8")
    PySide6OverlayController._poll_interim_file(controller)

    assert controller._widget.seen_interim == ["first", ""]


def test_poll_interim_file_detects_rewrite_with_unchanged_mtime(tmp_path):
    # Windows file times are coarse: a rewrite within one tick keeps the mtime.
    interim_file = tmp_path / "interim.txt"
    interim_file.write_text("first", encoding="utf-8")
    mtime_ns = interim_file.stat().st_mtime_ns

    controller = PySide6OverlayController.__new__(PySide6OverlayController)
    controller._running = True
    controller._interim_file = interim_file
    controller._widget = _FakeWidget()
    controller._last_interim_text = ""
    controller._last_interim_signature = None

    PySide6OverlayController._poll_interim_file(controller)
    interim_file.write_text("first words", encoding="utf-8")
    os.utime(interim_file, ns=(mtime_ns, mtime_ns))
    PySide6OverlayController._poll_interim_file(controller)

    assert controller._widget.seen_interim == ["first", "first words"]


def test_poll_interim_file_reads_tail_text_only(tmp_path, monkeypatch):
    interim_file = tmp_path / "interim.txt"
    interim_file.write_text("full interim payload", encoding="utf-8")

    calls: list[tuple[object, int]] = []

    monkeypatch.setattr(
        "ui.overlay_pyside6.read_file_tail_text",
        lambda path, *, max_chars, errors="replace", **_kwargs: (
            calls.append((path, max_chars)),
            "tail-only",
        )[1],
    )

    controller = PySide6OverlayController.__new__(PySide6OverlayController)
    controller._running = True
    controller._interim_file = interim_file
    controller._widget = _FakeWidget()
    controller._last_interim_text = ""
    controller._last_interim_signature = None

    PySide6OverlayController._poll_interim_file(controller)

    assert calls == [(interim_file, INTERIM_POLL_MAX_CHARS)]
    assert controller._widget.seen_interim == ["tail-only"]


def test_direct_update_from_worker_preserves_native_qt_timer():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import threading
import time
from pathlib import Path
from PySide6.QtCore import QCoreApplication, QTimer, qInstallMessageHandler
from ui.overlay_pyside6 import PySide6OverlayController, INTERIM_POLL_DIRECT_INTERVAL_MS

app = QCoreApplication([])
warnings = []
qInstallMessageHandler(lambda _kind, _context, message: warnings.append(message))
timer = QTimer()
timer.start(200)
controller = PySide6OverlayController()
controller._interim_timer = timer
controller._running = True
controller._interim_file = Path("unused-during-direct-updates")
controller._widget = type("Widget", (), {
    "current_state": "RECORDING",
    "update_interim_text": lambda self, text: None,
})()
worker = threading.Thread(target=lambda: controller.update_interim_text("alpha"))
worker.start()
worker.join(timeout=1)
assert not worker.is_alive()
assert timer.isActive(), warnings
assert timer.interval() == 200
controller._direct_interim_until = time.monotonic() + 60
controller._poll_interim_file()
assert timer.isActive(), warnings
assert timer.interval() == INTERIM_POLL_DIRECT_INTERVAL_MS
timer.stop()
assert not warnings, warnings
""",
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_update_interim_text_leaves_timer_changes_to_polling_thread(tmp_path):
    controller = PySide6OverlayController.__new__(PySide6OverlayController)
    controller._running = True
    controller._interim_file = tmp_path / "interim.txt"
    controller._interim_timer = _FakeInterimTimer(
        active=True,
        interval_ms=INTERIM_POLL_INTERVAL_MS,
    )
    controller._widget = _FakeWidget()
    controller._direct_interim_until = 0.0

    PySide6OverlayController.update_interim_text(controller, "alpha")

    assert controller._widget.seen_interim == ["alpha"]
    assert controller._interim_timer.set_interval_calls == []
    assert controller._direct_interim_until > 0

    PySide6OverlayController._poll_interim_file(controller)

    assert controller._interim_timer.set_interval_calls == [
        INTERIM_POLL_DIRECT_INTERVAL_MS
    ]


def test_update_state_skips_duplicate_payloads() -> None:
    controller = PySide6OverlayController.__new__(PySide6OverlayController)
    controller._widget = _FakeWidget()
    controller._pending_state = None

    PySide6OverlayController.update_state(controller, "LISTENING")
    PySide6OverlayController.update_state(controller, "LISTENING")
    PySide6OverlayController.update_state(controller, "LISTENING", "ready")

    assert controller._widget.state_calls == [
        ("LISTENING", ""),
        ("LISTENING", "ready"),
    ]


def test_update_interim_text_skips_duplicate_widget_updates(monkeypatch):
    controller = PySide6OverlayController.__new__(PySide6OverlayController)
    controller._widget = _FakeWidget()
    controller._interim_timer = _FakeInterimTimer(
        active=True,
        interval_ms=INTERIM_POLL_INTERVAL_MS,
    )

    monotonic_values = iter([10.0, 10.4])
    monkeypatch.setattr(
        "ui.overlay_pyside6.time.monotonic",
        lambda: next(monotonic_values),
    )

    PySide6OverlayController.update_interim_text(controller, "alpha")
    PySide6OverlayController.update_interim_text(controller, "alpha")

    assert controller._widget.seen_interim == ["alpha"]
    assert controller._interim_timer.set_interval_calls == []
    assert controller._direct_interim_until == 10.4 + INTERIM_DIRECT_UPDATE_GRACE_S


def test_poll_interim_file_skips_file_reads_while_direct_updates_are_recent(
    tmp_path,
    monkeypatch,
):
    interim_file = tmp_path / "interim.txt"
    interim_file.write_text("disk", encoding="utf-8")

    monkeypatch.setattr("ui.overlay_pyside6.time.monotonic", lambda: 10.0)
    monkeypatch.setattr(
        "ui.overlay_pyside6.read_file_tail_text",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("file read should be skipped during direct-update grace")
        ),
    )

    controller = PySide6OverlayController.__new__(PySide6OverlayController)
    controller._running = True
    controller._interim_file = interim_file
    controller._widget = _FakeWidget()
    controller._interim_timer = _FakeInterimTimer(
        active=True,
        interval_ms=INTERIM_POLL_INTERVAL_MS,
    )
    controller._direct_interim_until = 10.0 + INTERIM_DIRECT_UPDATE_GRACE_S
    controller._last_interim_text = ""
    controller._last_interim_signature = None

    PySide6OverlayController._poll_interim_file(controller)

    assert controller._widget.seen_interim == []
    assert controller._interim_timer.set_interval_calls == [INTERIM_POLL_DIRECT_INTERVAL_MS]


def test_poll_interim_file_restores_fast_interval_after_direct_update_grace(
    tmp_path,
    monkeypatch,
):
    interim_file = tmp_path / "interim.txt"
    interim_file.write_text("disk", encoding="utf-8")

    monkeypatch.setattr(
        "ui.overlay_pyside6.time.monotonic",
        lambda: 10.0 + INTERIM_DIRECT_UPDATE_GRACE_S + 0.1,
    )

    controller = PySide6OverlayController.__new__(PySide6OverlayController)
    controller._running = True
    controller._interim_file = interim_file
    controller._widget = _FakeWidget()
    controller._interim_timer = _FakeInterimTimer(
        active=True,
        interval_ms=INTERIM_POLL_DIRECT_INTERVAL_MS,
    )
    controller._direct_interim_until = 10.0 + INTERIM_DIRECT_UPDATE_GRACE_S
    controller._last_interim_text = ""
    controller._last_interim_signature = None

    PySide6OverlayController._poll_interim_file(controller)

    assert controller._interim_timer.set_interval_calls == [INTERIM_POLL_INTERVAL_MS]
    assert controller._widget.seen_interim == ["disk"]


def test_apply_interim_poll_interval_uses_stable_interval() -> None:
    controller = PySide6OverlayController.__new__(PySide6OverlayController)
    controller._interim_timer = _FakeInterimTimer(
        active=True,
        interval_ms=INTERIM_POLL_INTERVAL_MS,
    )
    controller._stable_interim_polls = INTERIM_POLL_STABLE_THRESHOLD

    PySide6OverlayController._apply_interim_poll_interval(controller)

    assert controller._interim_timer.set_interval_calls == [
        INTERIM_POLL_STABLE_INTERVAL_MS
    ]


def test_poll_interim_file_backs_off_after_repeated_stable_checks(tmp_path):
    interim_file = tmp_path / "interim.txt"
    interim_file.write_text("disk", encoding="utf-8")
    stat = interim_file.stat()

    controller = PySide6OverlayController.__new__(PySide6OverlayController)
    controller._running = True
    controller._interim_file = interim_file
    controller._widget = _FakeWidget()
    controller._interim_timer = _FakeInterimTimer(
        active=True,
        interval_ms=INTERIM_POLL_INTERVAL_MS,
    )
    controller._direct_interim_until = 0.0
    controller._last_interim_text = "disk"
    controller._last_interim_signature = (stat.st_mtime_ns, stat.st_size)
    controller._stable_interim_polls = INTERIM_POLL_STABLE_THRESHOLD

    PySide6OverlayController._poll_interim_file(controller)

    assert controller._interim_timer.set_interval_calls[-1] == (
        INTERIM_POLL_STABLE_INTERVAL_MS
    )


def test_set_interim_polling_active_starts_and_stops_timer():
    controller = PySide6OverlayController.__new__(PySide6OverlayController)
    controller._interim_timer = _FakeInterimTimer(active=True, interval_ms=1000)
    controller._last_interim_text = "stale"
    controller._last_interim_signature = 123
    controller._direct_interim_until = 42.0

    PySide6OverlayController._set_interim_polling_active(controller, False)

    assert controller._interim_timer.stop_calls == 1
    assert controller._last_interim_text == ""
    assert controller._last_interim_signature is None
    assert controller._direct_interim_until == 0.0

    PySide6OverlayController._set_interim_polling_active(controller, True)

    assert controller._interim_timer.start_calls == [INTERIM_POLL_INTERVAL_MS]
    assert controller._interim_timer.interval_ms == INTERIM_POLL_INTERVAL_MS


def test_set_interim_polling_active_avoids_restarting_active_timer():
    controller = PySide6OverlayController.__new__(PySide6OverlayController)
    controller._interim_timer = _FakeInterimTimer(
        active=True,
        interval_ms=INTERIM_POLL_INTERVAL_MS,
    )

    PySide6OverlayController._set_interim_polling_active(controller, True)

    assert controller._interim_timer.start_calls == []
    assert controller._interim_timer.set_interval_calls == []


def test_format_recording_interim_text_compacts_whitespace():
    text = "  hello   world \n\n from   pulse  "
    assert _format_recording_interim_text(text) == "hello world from pulse"


def test_format_recording_interim_text_handles_long_whitespace_heavy_text():
    text = ("alpha   beta   " * 80) + "  final   words  here "

    assert _format_recording_interim_text(text, max_chars=20) == "... final words here"


def test_controller_wait_until_ready_uses_ready_event():
    controller = PySide6OverlayController.__new__(PySide6OverlayController)
    calls: list[float | None] = []
    controller._ready_event = types.SimpleNamespace(
        wait=lambda timeout=None: calls.append(timeout) or True
    )

    assert PySide6OverlayController.wait_until_ready(controller, timeout=0.15) is True
    assert calls == [0.15]


class _FakeShimmerAnimation:
    def __init__(self):
        self.running = False
        self.end_values: list[float] = []

    def setEndValue(self, value: float) -> None:
        self.end_values.append(value)

    def start(self) -> None:
        self.running = True

    def stop(self) -> None:
        self.running = False


def _state_widget(monkeypatch):
    widget = PySide6OverlayWidget.__new__(PySide6OverlayWidget)
    widget._state = "IDLE"
    widget._last_state_payload = None
    widget._content = OverlayContent()
    widget._interim = ("", "")
    widget._previous_interim = ""
    widget._animation_timer = _FakeAnimationTimer()
    widget._clock_timer = _FakeAnimationTimer()
    widget._audio_level = 0.0
    widget._anim = AnimationLogic()
    widget._bar_heights = [float(BAR_MIN_HEIGHT)] * BAR_COUNT
    widget._painted_bar_heights = [float(BAR_MIN_HEIGHT)] * BAR_COUNT
    widget._listening_settled = False
    widget.level_changed = types.SimpleNamespace(
        emit=lambda level: widget._on_level_changed(level)
    )
    widget._shimmer_animation = _FakeShimmerAnimation()
    widget._shimmer_track = None
    widget._shimmer_x = None
    widget._shimmer_due = 0.0
    widget.update = lambda *_args: None
    widget._meta_text = ""
    widget._clock_start = 0.0
    widget._link_span = None
    widget.on_open_settings = None
    widget.events = []
    widget._schedule_center_on_active_screen = lambda: None
    widget._fade_in = lambda: widget.events.append("show")
    widget._fade_out = lambda: widget.events.append("hide")
    widget._start_fade_out_timer = lambda: widget.events.append("auto-hide")
    widget._relayout = lambda: widget.events.append("layout")
    widget.setAccessibleDescription = lambda _text: None
    widget._persistent = False
    widget.click_through = True
    widget._dismiss_font = None
    widget.windowHandle = lambda: types.SimpleNamespace(
        setFlag=lambda _flag, on: setattr(widget, "click_through", on)
    )
    widget._cancel_fade_out_timer = lambda: None
    widget.recording_state_changed = types.SimpleNamespace(emit=lambda *_args: None)
    monkeypatch.setattr("ui.overlay_pyside6.time.perf_counter", lambda: 123.0)
    return widget


def _run_frames(widget, limit: int = 400) -> int:
    for frame in range(limit):
        if not widget._animation_timer.active:
            return frame
        widget._animate_frame()
    return limit


def test_only_listening_and_recording_run_the_animation_timer(monkeypatch):
    widget = _state_widget(monkeypatch)

    widget._on_state_changed("LISTENING", "")
    assert widget._animation_timer.start_calls == [FRAME_MS]
    assert widget._bar_heights == list(LISTENING_BAR_HEIGHTS)
    # Silence: the rest row is already settled, so the loop stops at once.
    assert _run_frames(widget) == 1
    assert widget._listening_settled is True

    widget._on_state_changed("RECORDING", "")
    assert widget._animation_timer.start_calls == [FRAME_MS, FRAME_MS]

    for state in ("TRANSCRIBING", "REFINING", "LOADING", "DONE"):
        widget._on_state_changed(state, "")
        assert widget._animation_timer.active is False
    assert widget._animation_timer.start_calls == [FRAME_MS, FRAME_MS]


def test_listening_level_meter_settles_and_wakes_on_change(monkeypatch):
    widget = _state_widget(monkeypatch)
    widget._on_state_changed("LISTENING", "")
    _run_frames(widget)

    # Room noise below the gate does not wake the settled loop.
    widget.update_audio_level(0.001)
    assert widget._animation_timer.active is False

    widget.update_audio_level(VAD_THRESHOLD)
    assert widget._animation_timer.active is True
    assert _run_frames(widget) < 400
    peak = listening_bar_heights(1.0)
    assert all(abs(h - p) < 2 * BAR_HEIGHT_UPDATE_EPSILON for h, p in zip(widget._bar_heights, peak))
    assert max(widget._bar_heights) <= max(LISTENING_BAR_HEIGHTS) + LISTENING_LEVEL_SPAN

    # A steady level keeps the loop stopped.
    widget.update_audio_level(VAD_THRESHOLD)
    assert widget._animation_timer.active is False


def test_queued_level_wake_keeps_the_newest_level(monkeypatch):
    widget = _state_widget(monkeypatch)
    widget._on_state_changed("LISTENING", "")
    widget._audio_level = 0.004  # newer than the queued signal value

    widget._on_level_changed(0.012)

    assert widget._audio_level == 0.004
    assert widget._animation_timer.active is True


def test_recording_continues_from_the_listening_heights(monkeypatch):
    widget = _state_widget(monkeypatch)
    widget._on_state_changed("LISTENING", "")
    widget.update_audio_level(VAD_THRESHOLD)
    _run_frames(widget)
    listening = list(widget._bar_heights)

    widget._on_state_changed("RECORDING", "")

    assert widget._bar_heights == listening
    widget._animate_frame()
    # One smoothing step from the listening row, never a reset to the minimum.
    assert all(
        abs(after - before) <= 0.4 * BAR_MAX_HEIGHT
        for after, before in zip(widget._bar_heights, listening)
    )
    assert min(widget._bar_heights) > BAR_MIN_HEIGHT


def test_shimmer_runs_only_on_processing_lines(monkeypatch):
    widget = _state_widget(monkeypatch)
    monkeypatch.setattr("ui.overlay_pyside6._reduce_motion", lambda: False)
    animation = widget._shimmer_animation

    widget._on_state_changed("RECORDING", "")
    assert animation.running is False

    widget._on_state_changed("LOADING", "")
    widget._on_state_changed("TRANSCRIBING", "")
    # Same track: the running sweep continues instead of restarting.
    assert animation.running is True and animation.end_values == [BAR_AREA_WIDTH]

    widget._on_state_changed("TRANSCRIBING", transcribing_status(None, refine=True))
    widget._on_state_changed("REFINING", "")
    assert widget._shimmer_track == shimmer_track("phase2")
    assert animation.end_values[-1] == PHASE_STEP_WIDTH

    for state in ("DONE", "IDLE"):
        widget._on_state_changed("REFINING", "again")
        widget._on_state_changed(state, "Pasted")
        assert animation.running is False and widget._shimmer_track is None


def test_reduce_motion_keeps_the_static_line(monkeypatch):
    widget = _state_widget(monkeypatch)
    monkeypatch.setattr("ui.overlay_pyside6._reduce_motion", lambda: True)

    widget._on_state_changed("TRANSCRIBING", "")

    assert widget._shimmer_animation.running is False
    assert widget._shimmer_track is None


def test_shimmer_repaints_only_the_track_at_most_30_fps(monkeypatch):
    widget = _state_widget(monkeypatch)
    widget._layout = compute_overlay_layout(text_width=0)
    widget._shimmer_track = shimmer_track("line")
    repaints = []
    widget.update = lambda *args: repaints.append(args)
    clock = iter([10.0, 10.016, 10.033, 10.049])
    monkeypatch.setattr("ui.overlay_pyside6.time.perf_counter", lambda: next(clock))

    for value in (1.0, 2.0, 3.0, 4.0):
        widget._on_shimmer_value(value)

    assert len(repaints) == 2 and widget._shimmer_x == 3.0
    rect = repaints[0][0]
    assert rect.width() <= BAR_AREA_WIDTH + 3 and rect.height() <= 6


def test_on_state_changed_skips_duplicate_transition_work(monkeypatch):
    widget = _state_widget(monkeypatch)

    widget._on_state_changed("TRANSCRIBING", "")
    widget._on_state_changed("TRANSCRIBING", "")

    assert widget.events == ["layout", "show"]


@pytest.mark.parametrize(
    ("state", "text", "expected"),
    [
        ("LOADING", "", OverlayContent("line", "Preparing…")),
        (
            "ERROR",
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
        (
            "NO_SPEECH",
            "",
            OverlayContent(text="Nothing was pasted", badge="No speech", tone="warn"),
        ),
        ("DONE", "Copied to clipboard", OverlayContent(badge="Copied", tone="success")),
    ],
)
def test_on_state_changed_uses_shared_content(monkeypatch, state, text, expected):
    widget = _state_widget(monkeypatch)

    widget._on_state_changed(state, text)

    assert widget._content == expected
    assert ("auto-hide" in widget.events) == (state in ("NO_SPEECH", "DONE"))


def test_on_interim_changed_marks_newest_words(monkeypatch):
    widget = _state_widget(monkeypatch)
    widget._on_state_changed("RECORDING", "")

    widget._on_interim_changed("hello")
    widget._on_interim_changed("hello world")
    assert widget._interim == ("hello ", "world")

    widget._on_interim_changed("")
    assert widget._interim == ("", "")


class _Metrics:
    def horizontalAdvance(self, text):
        return 7.0 * len(text)


def _runs_widget():
    widget = PySide6OverlayWidget.__new__(PySide6OverlayWidget)
    widget._content = OverlayContent("level", "Recording", emphasized=True)
    widget._colors = {"text": "bright", "muted": "muted", "interim": "dim"}
    return widget


def test_overflowing_interim_text_ends_at_the_zone_edge_and_is_faded():
    widget = _runs_widget()
    layout = types.SimpleNamespace(text_x=112.0, text_width=90.0)
    text = "alpha beta gamma delta"  # 22 chars = 154 px

    runs = PySide6OverlayWidget._build_text_runs(
        widget, layout, _Metrics(), text, "delta"
    )

    start = 112.0 + 90.0 - 7.0 * len(text)
    assert runs == [
        (start, "alpha beta gamma ", "dim"),
        (start + 7.0 * 17, "delta", "bright"),
    ]
    assert widget._text_clipped is True


def test_fitting_interim_text_starts_at_the_zone_edge_without_fade():
    widget = _runs_widget()
    layout = types.SimpleNamespace(text_x=112.0, text_width=200.0)

    runs = PySide6OverlayWidget._build_text_runs(
        widget, layout, _Metrics(), "hello world", "world"
    )

    assert runs == [(112.0, "hello ", "dim"), (112.0 + 7.0 * 6, "world", "bright")]
    assert widget._text_clipped is False


def test_tabular_figures_keeps_font_when_tag_from_string_is_unsupported(monkeypatch):
    from ui import overlay_pyside6

    class _OldTag:
        def __init__(self, *_args):
            raise TypeError("no string constructor")

    monkeypatch.setattr(overlay_pyside6.QFont, "Tag", _OldTag, raising=False)
    font = overlay_pyside6.QFont()

    assert overlay_pyside6._tabular_figures(font) is font


def test_recording_clock_runs_only_while_listening_or_recording(monkeypatch):
    widget = _state_widget(monkeypatch)
    now = [100.0]
    monkeypatch.setattr("ui.overlay_pyside6.time.monotonic", lambda: now[0])

    widget._on_state_changed("LISTENING", "")
    assert widget._clock_timer.active is True
    assert widget._meta_text == "0:00"
    now[0] = 103.0
    widget._on_state_changed("RECORDING", "")
    assert widget._clock_timer.start_calls == [None]  # not restarted
    now[0] = 164.97  # an early coarse tick still shows the full second
    widget._tick_clock()
    assert widget._meta_text == "1:05"

    widget._on_state_changed("TRANSCRIBING", "")
    assert widget._clock_timer.active is False
    assert widget._meta_text == ""


def _recording_widget(anim):
    widget = PySide6OverlayWidget.__new__(PySide6OverlayWidget)
    widget._state = "RECORDING"
    widget._animation_start = 0.0
    widget._audio_level = 0.0
    widget._bar_heights = [float(BAR_MIN_HEIGHT)] * BAR_COUNT
    widget._painted_bar_heights = [float(BAR_MIN_HEIGHT)] * BAR_COUNT
    widget._anim = anim
    widget.repaints = []
    widget.update = lambda: widget.repaints.append("update")
    return widget


def test_animate_frame_skips_repaint_for_subpixel_height_changes(monkeypatch):
    widget = _recording_widget(
        types.SimpleNamespace(
            update_level=lambda _level: None,
            update_agc=lambda: None,
            calculate_frame_heights=lambda *_args: (
                BAR_MIN_HEIGHT + BAR_HEIGHT_UPDATE_EPSILON / 2,
            )
            * BAR_COUNT,
        )
    )
    monkeypatch.setattr("ui.overlay_pyside6.time.perf_counter", lambda: 1.0)

    PySide6OverlayWidget._animate_frame(widget)

    assert widget.repaints == []


def test_animate_frame_repaints_when_bars_move(monkeypatch):
    widget = _recording_widget(
        types.SimpleNamespace(
            update_level=lambda _level: None,
            update_agc=lambda: None,
            calculate_frame_heights=lambda *_args: (BAR_MIN_HEIGHT + 4,) * BAR_COUNT,
        )
    )
    monkeypatch.setattr("ui.overlay_pyside6.time.perf_counter", lambda: 1.0)

    PySide6OverlayWidget._animate_frame(widget)

    assert widget.repaints == ["update"]


def test_animate_frame_stops_timer_outside_recording():
    widget = _recording_widget(None)
    widget._state = "TRANSCRIBING"
    widget._animation_timer = _FakeAnimationTimer(active=True, interval_ms=FRAME_MS)

    PySide6OverlayWidget._animate_frame(widget)

    assert widget._animation_timer.active is False
    assert widget.repaints == []


def test_errors_persist_through_idle_and_accept_clicks_until_dismissed(monkeypatch):
    widget = _state_widget(monkeypatch)

    widget._on_state_changed("ERROR", PASTE_FAILED)
    assert widget.click_through is False
    widget._on_state_changed("IDLE", "")
    assert "hide" not in widget.events and "auto-hide" not in widget.events

    widget.mousePressEvent(types.SimpleNamespace(accept=lambda: None))
    assert widget.events[-1] == "hide"
    assert widget.click_through is True
    assert widget._persistent is False


def test_next_recording_replaces_persistent_error(monkeypatch):
    widget = _state_widget(monkeypatch)
    widget._on_state_changed("ERROR", "boom")
    widget._on_state_changed("IDLE", "")

    widget._on_state_changed("LISTENING", "")

    assert widget.click_through is True
    assert widget._content.dismissible is False
    widget._on_state_changed("IDLE", "")
    assert widget.events[-1] == "hide"


def test_new_error_after_dismissal_is_shown_again(monkeypatch):
    widget = _state_widget(monkeypatch)
    widget._on_state_changed("ERROR", "first")
    widget._dismiss()

    widget._on_state_changed("ERROR", "second")

    assert widget.events[-1] == "show"
    assert widget.click_through is False



@pytest.mark.parametrize(("x", "opened"), [(300.0, True), (120.0, False)])
def test_settings_link_click_opens_settings_then_dismisses(monkeypatch, x, opened):
    widget = _state_widget(monkeypatch)
    opens = []
    widget.on_open_settings = lambda: opens.append(True)
    widget._on_state_changed("ERROR", "OPENAI_API_KEY not set")
    widget._link_span = (290.0, 340.0)
    event = types.SimpleNamespace(
        accept=lambda: None, position=lambda: types.SimpleNamespace(x=lambda: x)
    )

    widget.mousePressEvent(event)

    assert opens == ([True] if opened else [])
    assert widget.events[-1] == "hide"
    assert widget._persistent is False
