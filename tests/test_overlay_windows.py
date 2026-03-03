from pathlib import Path
import queue
import time
import types

from ui.overlay_windows import (
    FRAME_MS,
    FRAME_MS_ACTIVE,
    FRAME_MS_FEEDBACK,
    INTERIM_QUEUE_BACKPRESSURE_LIMIT,
    QUEUE_POLL_ACTIVE_MS,
    QUEUE_POLL_IDLE_MS,
    STATE_COLORS,
    WINDOW_HEIGHT,
    WINDOW_MARGIN_BOTTOM,
    WINDOW_WIDTH,
    WindowsOverlayController,
)


class _FakeRoot:
    def __init__(self, *, screen_w: int = 1920, screen_h: int = 1080):
        self._screen_w = screen_w
        self._screen_h = screen_h
        self.after_calls: list[int] = []
        self.last_geometry: str | None = None

    def after(self, _ms: int, _callback) -> None:
        self.after_calls.append(_ms)
        return

    def deiconify(self) -> None:
        return

    def withdraw(self) -> None:
        return

    def winfo_screenwidth(self) -> int:
        return self._screen_w

    def winfo_screenheight(self) -> int:
        return self._screen_h

    def geometry(self, geometry: str) -> None:
        self.last_geometry = geometry


class _FakeLabel:
    def __init__(self):
        self.last_config: dict[str, object] = {}

    def config(self, **kwargs) -> None:
        self.last_config.update(kwargs)


def _make_controller(interim_file: Path) -> WindowsOverlayController:
    controller = WindowsOverlayController.__new__(WindowsOverlayController)
    controller._running = True
    controller._root = _FakeRoot()
    controller._interim_file = interim_file
    controller._state = "RECORDING"
    controller._last_interim_text = ""
    controller._last_interim_mtime_ns = None
    return controller


def test_poll_interim_file_reads_only_when_file_changes(tmp_path):
    interim_file = tmp_path / "interim.txt"
    interim_file.write_text("hello", encoding="utf-8")
    controller = _make_controller(interim_file)
    seen_texts: list[str] = []
    controller._handle_interim_text = seen_texts.append

    controller._poll_interim_file()
    controller._poll_interim_file()

    assert seen_texts == ["hello"]

    interim_file.write_text("hello again", encoding="utf-8")
    controller._poll_interim_file()

    assert seen_texts == ["hello", "hello again"]


def test_update_audio_level_does_not_enqueue_messages():
    controller = WindowsOverlayController()

    for _ in range(500):
        controller.update_audio_level(0.42)

    assert controller._audio_level == 0.42
    assert controller._queue.empty()


def test_update_interim_text_applies_backpressure_under_heavy_queue_load():
    controller = WindowsOverlayController()

    for idx in range(INTERIM_QUEUE_BACKPRESSURE_LIMIT):
        controller._queue.put(("interim", f"existing-{idx}", None))

    size_before = controller._queue.qsize()
    controller.update_interim_text("newest")

    assert controller._queue.qsize() == size_before


def test_handle_state_change_uses_feedback_color_for_done_and_error():
    controller = WindowsOverlayController()
    controller._root = _FakeRoot()
    controller._label = _FakeLabel()

    controller._handle_state_change("DONE", None)
    assert controller._label.last_config["text"] == "Done!"
    assert controller._label.last_config["fg"] == STATE_COLORS["DONE"]

    controller._handle_state_change("ERROR", "Boom")
    assert controller._label.last_config["text"] == "Boom"
    assert controller._label.last_config["fg"] == STATE_COLORS["ERROR"]


def test_handle_interim_text_updates_label_for_short_text():
    controller = WindowsOverlayController()
    controller._state = "RECORDING"
    controller._label = _FakeLabel()

    controller._handle_interim_text("short text")

    assert controller._label.last_config["text"] == "short text"
    assert controller._label.last_config["fg"] == "#909090"


def test_handle_state_change_repositions_on_active_monitor_when_leaving_idle():
    controller = WindowsOverlayController()
    controller._root = _FakeRoot()
    controller._label = _FakeLabel()

    calls: list[bool] = []
    controller._position_window = lambda use_active_monitor: calls.append(
        use_active_monitor
    )

    controller._handle_state_change("RECORDING", None)

    assert calls == [True]


def test_position_window_uses_active_monitor_work_area():
    controller = WindowsOverlayController.__new__(WindowsOverlayController)
    controller._root = _FakeRoot(screen_w=1920, screen_h=1080)
    controller._get_active_monitor_work_area = lambda: (1920, 0, 1920, 1080)

    controller._position_window(use_active_monitor=True)

    expected_x = 1920 + (1920 - WINDOW_WIDTH) // 2
    expected_y = 1080 - WINDOW_HEIGHT - WINDOW_MARGIN_BOTTOM
    assert (
        controller._root.last_geometry
        == f"{WINDOW_WIDTH}x{WINDOW_HEIGHT}+{expected_x}+{expected_y}"
    )


def test_poll_queue_uses_idle_interval_when_idle():
    controller = WindowsOverlayController.__new__(WindowsOverlayController)
    controller._running = True
    controller._root = _FakeRoot()
    controller._queue = queue.Queue()
    controller._state = "IDLE"

    controller._poll_queue()

    assert controller._root.after_calls[-1] == QUEUE_POLL_IDLE_MS


def test_poll_queue_uses_active_interval_when_overlay_visible():
    controller = WindowsOverlayController.__new__(WindowsOverlayController)
    controller._running = True
    controller._root = _FakeRoot()
    controller._queue = queue.Queue()
    controller._state = "RECORDING"

    controller._poll_queue()

    assert controller._root.after_calls[-1] == QUEUE_POLL_ACTIVE_MS


def test_poll_queue_coalesces_interim_messages_to_latest_text():
    controller = WindowsOverlayController.__new__(WindowsOverlayController)
    controller._running = True
    controller._root = _FakeRoot()
    controller._queue = queue.Queue()
    controller._state = "RECORDING"
    controller._audio_level = 0.0

    seen_texts: list[str] = []
    controller._handle_interim_text = seen_texts.append
    controller._handle_state_change = lambda *_args: None

    controller._queue.put(("interim", "one", None))
    controller._queue.put(("interim", "two", None))
    controller._queue.put(("interim", "three", None))

    controller._poll_queue()

    assert seen_texts == ["three"]


def test_animate_stops_loop_while_idle():
    controller = WindowsOverlayController.__new__(WindowsOverlayController)
    controller._running = True
    controller._root = _FakeRoot()
    controller._state = "IDLE"
    controller._animation_running = True

    controller._animate()

    assert controller._animation_running is False
    assert controller._root.after_calls == []


def test_handle_state_change_restarts_animation_loop_when_overlay_becomes_active():
    controller = WindowsOverlayController()
    controller._root = _FakeRoot()
    controller._label = _FakeLabel()

    starts: list[bool] = []
    controller._start_animation_loop = lambda: starts.append(True)

    controller._handle_state_change("RECORDING", None)

    assert starts == [True]


def test_frame_interval_ms_is_state_aware():
    controller = WindowsOverlayController.__new__(WindowsOverlayController)

    controller._state = "RECORDING"
    assert controller._frame_interval_ms() == FRAME_MS

    controller._state = "TRANSCRIBING"
    assert controller._frame_interval_ms() == FRAME_MS_ACTIVE

    controller._state = "DONE"
    assert controller._frame_interval_ms() == FRAME_MS_FEEDBACK


def test_animate_uses_reduced_fps_for_non_recording_states():
    controller = WindowsOverlayController.__new__(WindowsOverlayController)
    controller._running = True
    controller._root = _FakeRoot()
    controller._state = "TRANSCRIBING"
    controller._animation_start = time.perf_counter()
    controller._audio_level = 0.0
    controller._anim = types.SimpleNamespace(
        update_level=lambda _level: None,
        update_agc=lambda: None,
    )
    controller._render_bars = lambda _t: None

    controller._animate()

    assert controller._root.after_calls[-1] == FRAME_MS_ACTIVE
