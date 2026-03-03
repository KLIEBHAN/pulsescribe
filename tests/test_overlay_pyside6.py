import pytest

pytest.importorskip("PySide6")

from ui.overlay_pyside6 import (
    FEEDBACK_DISPLAY_MS,
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
    PySide6OverlayController._poll_interim_file(controller)
    assert controller._last_interim_mtime_ns is None
