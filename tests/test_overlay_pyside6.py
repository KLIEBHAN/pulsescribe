import pytest

pytest.importorskip("PySide6")

from ui.overlay_pyside6 import (
    FEEDBACK_DISPLAY_MS,
    FRAME_MS,
    FRAME_MS_ACTIVE,
    FRAME_MS_FEEDBACK,
    INTERIM_POLL_INTERVAL_MS,
    INTERIM_POLL_MAX_CHARS,
    PySide6OverlayController,
    PySide6OverlayWidget,
    _format_recording_interim_text,
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


class _FakeQtLabel:
    def __init__(self):
        self.font_calls = 0
        self.style_calls = 0
        self.text_calls = 0
        self.text = ""
        self.style = ""
        self.font = None

    def setFont(self, font) -> None:
        self.font = font
        self.font_calls += 1

    def setStyleSheet(self, style: str) -> None:
        self.style = style
        self.style_calls += 1

    def setText(self, text: str) -> None:
        self.text = text
        self.text_calls += 1


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


def test_set_interim_polling_active_starts_and_stops_timer():
    controller = PySide6OverlayController.__new__(PySide6OverlayController)
    controller._interim_timer = _FakeInterimTimer(active=True, interval_ms=1000)
    controller._last_interim_text = "stale"
    controller._last_interim_mtime_ns = 123

    PySide6OverlayController._set_interim_polling_active(controller, False)

    assert controller._interim_timer.stop_calls == 1
    assert controller._last_interim_text == ""
    assert controller._last_interim_mtime_ns is None

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


def test_on_interim_changed_restores_default_recording_label_when_empty():
    widget = PySide6OverlayWidget.__new__(PySide6OverlayWidget)
    widget._state = "RECORDING"
    seen_calls: list[tuple[str, str, bool]] = []
    widget._update_label = lambda state, text, italic=False: seen_calls.append(
        (state, text, italic)
    )

    PySide6OverlayWidget._on_interim_changed(widget, "")

    assert seen_calls == [("RECORDING", "Recording...", False)]


def test_format_recording_interim_text_compacts_whitespace():
    text = "  hello   world \n\n from   pulse  "
    assert _format_recording_interim_text(text) == "hello world from pulse"


def test_on_interim_changed_uses_compacted_tail_text():
    widget = PySide6OverlayWidget.__new__(PySide6OverlayWidget)
    widget._state = "RECORDING"
    seen_calls: list[tuple[str, str, bool]] = []
    widget._update_label = lambda state, text, italic=False: seen_calls.append(
        (state, text, italic)
    )
    text = "alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu"
    cleaned = " ".join(text.split())

    PySide6OverlayWidget._on_interim_changed(widget, text)

    assert seen_calls == [("RECORDING", "..." + cleaned[-42:], True)]


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


def test_update_label_skips_duplicate_font_style_and_text_updates():
    widget = PySide6OverlayWidget.__new__(PySide6OverlayWidget)
    widget._label = _FakeQtLabel()

    PySide6OverlayWidget._update_label(widget, "RECORDING", "Recording...")
    PySide6OverlayWidget._update_label(widget, "RECORDING", "Recording...")

    assert widget._label.font_calls == 1
    assert widget._label.style_calls == 1
    assert widget._label.text_calls == 1


def test_update_label_only_mutates_changed_parts():
    widget = PySide6OverlayWidget.__new__(PySide6OverlayWidget)
    widget._label = _FakeQtLabel()

    PySide6OverlayWidget._update_label(widget, "RECORDING", "Recording...")
    PySide6OverlayWidget._update_label(widget, "RECORDING", "Interim...", italic=True)

    assert widget._label.font_calls == 2
    assert widget._label.style_calls == 2
    assert widget._label.text_calls == 2
