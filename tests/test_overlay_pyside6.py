import pytest

pytest.importorskip("PySide6")

from ui.overlay_pyside6 import (
    FEEDBACK_DISPLAY_MS,
    FRAME_MS,
    FRAME_MS_ACTIVE,
    FRAME_MS_FEEDBACK,
    INTERIM_POLL_MAX_CHARS,
    PySide6OverlayController,
    PySide6OverlayWidget,
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


def test_fade_out_timer_is_reused(monkeypatch):
    widget = PySide6OverlayWidget.__new__(PySide6OverlayWidget)
    widget._fade_out_timer = _FakeTimer()

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


def test_frame_interval_ms_is_state_aware():
    widget = PySide6OverlayWidget.__new__(PySide6OverlayWidget)

    widget._state = "RECORDING"
    assert widget._frame_interval_ms() == FRAME_MS

    widget._state = "TRANSCRIBING"
    assert widget._frame_interval_ms() == FRAME_MS_ACTIVE

    widget._state = "DONE"
    assert widget._frame_interval_ms() == FRAME_MS_FEEDBACK


def test_start_animation_uses_state_dependent_interval(monkeypatch):
    widget = PySide6OverlayWidget.__new__(PySide6OverlayWidget)
    widget._state = "TRANSCRIBING"
    widget._animation_timer = _FakeAnimationTimer(active=False, interval_ms=0)

    monkeypatch.setattr("ui.overlay_pyside6.time.perf_counter", lambda: 123.0)

    PySide6OverlayWidget._start_animation(widget)

    assert widget._animation_timer.start_calls == [FRAME_MS_ACTIVE]
    assert widget._animation_timer.interval_ms == FRAME_MS_ACTIVE
    assert widget._animation_start == 123.0


def test_update_animation_timer_interval_updates_active_timer():
    widget = PySide6OverlayWidget.__new__(PySide6OverlayWidget)
    widget._state = "DONE"
    widget._animation_timer = _FakeAnimationTimer(
        active=True,
        interval_ms=FRAME_MS,
    )

    PySide6OverlayWidget._update_animation_timer_interval(widget)

    assert widget._animation_timer.set_interval_calls == [FRAME_MS_FEEDBACK]
    assert widget._animation_timer.interval_ms == FRAME_MS_FEEDBACK
