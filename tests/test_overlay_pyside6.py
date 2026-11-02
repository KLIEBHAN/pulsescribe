import pytest
import subprocess
import sys
import types

pytest.importorskip("PySide6")

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
from ui.overlay_feedback import OverlayContent


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

    def start(self, interval_ms: int) -> None:
        self.active = True
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
    controller._last_interim_mtime_ns = None

    PySide6OverlayController._poll_interim_file(controller)
    PySide6OverlayController._poll_interim_file(controller)

    assert controller._widget.seen_interim == ["first"]

    interim_file.write_text("second", encoding="utf-8")
    PySide6OverlayController._poll_interim_file(controller)
    assert controller._widget.seen_interim == ["first", "second"]

    controller._widget.current_state = "IDLE"
    controller._last_interim_mtime_ns = 123
    controller._last_interim_text = "stale"
    PySide6OverlayController._poll_interim_file(controller)
    assert controller._last_interim_mtime_ns is None
    assert controller._last_interim_text == ""


def test_poll_interim_file_clears_stale_text_when_file_becomes_empty(tmp_path):
    interim_file = tmp_path / "interim.txt"
    interim_file.write_text("first", encoding="utf-8")

    controller = PySide6OverlayController.__new__(PySide6OverlayController)
    controller._running = True
    controller._interim_file = interim_file
    controller._widget = _FakeWidget()
    controller._last_interim_text = ""
    controller._last_interim_mtime_ns = None

    PySide6OverlayController._poll_interim_file(controller)
    interim_file.write_text("", encoding="utf-8")
    PySide6OverlayController._poll_interim_file(controller)

    assert controller._widget.seen_interim == ["first", ""]


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
    controller._last_interim_mtime_ns = None

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
    controller._last_interim_mtime_ns = None

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
    controller._last_interim_mtime_ns = None

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
    current_mtime_ns = interim_file.stat().st_mtime_ns

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
    controller._last_interim_mtime_ns = current_mtime_ns
    controller._stable_interim_polls = INTERIM_POLL_STABLE_THRESHOLD

    PySide6OverlayController._poll_interim_file(controller)

    assert controller._interim_timer.set_interval_calls[-1] == (
        INTERIM_POLL_STABLE_INTERVAL_MS
    )


def test_set_interim_polling_active_starts_and_stops_timer():
    controller = PySide6OverlayController.__new__(PySide6OverlayController)
    controller._interim_timer = _FakeInterimTimer(active=True, interval_ms=1000)
    controller._last_interim_text = "stale"
    controller._last_interim_mtime_ns = 123
    controller._direct_interim_until = 42.0

    PySide6OverlayController._set_interim_polling_active(controller, False)

    assert controller._interim_timer.stop_calls == 1
    assert controller._last_interim_text == ""
    assert controller._last_interim_mtime_ns is None
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


def _state_widget(monkeypatch):
    widget = PySide6OverlayWidget.__new__(PySide6OverlayWidget)
    widget._state = "IDLE"
    widget._last_state_payload = None
    widget._content = OverlayContent()
    widget._interim = ("", "")
    widget._previous_interim = ""
    widget._animation_timer = _FakeAnimationTimer()
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


def test_only_recording_runs_the_animation_timer(monkeypatch):
    widget = _state_widget(monkeypatch)

    widget._on_state_changed("LISTENING", "")
    assert widget._animation_timer.start_calls == []

    widget._on_state_changed("RECORDING", "")
    assert widget._animation_timer.start_calls == [FRAME_MS]

    for state in ("TRANSCRIBING", "REFINING", "LOADING", "DONE"):
        widget._on_state_changed(state, "")
        assert widget._animation_timer.active is False
    assert widget._animation_timer.start_calls == [FRAME_MS]


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
                text="Microphone unavailable", badge="Error", tone="error", dismissible=True
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


def test_interim_runs_keep_newest_words_when_elided():
    class _Metrics:
        def elidedText(self, text, mode, width):
            assert mode.name == "ElideLeft"
            return "\u2026" + text[-12:]

        def horizontalAdvance(self, text):
            return 7.0 * len(text)

    widget = PySide6OverlayWidget.__new__(PySide6OverlayWidget)
    widget._content = OverlayContent("level", "Recording", emphasized=True)
    widget._colors = {"text": "bright", "muted": "muted", "interim": "dim"}
    layout = types.SimpleNamespace(text_x=85.0, text_width=90.0)

    runs = PySide6OverlayWidget._build_text_runs(
        widget, layout, _Metrics(), "alpha beta gamma delta", "delta"
    )

    assert runs == [(85.0, "\u2026 gamma ", "dim"), (85.0 + 7.0 * 8, "delta", "bright")]


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

    widget._on_state_changed("ERROR", "Paste failed — transcript copied")
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
