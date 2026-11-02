from pathlib import Path
import queue
import types

from ui.overlay_windows import (
    BAR_COUNT,
    BAR_HEIGHT_UPDATE_EPSILON,
    BAR_MIN_HEIGHT,
    INTERIM_DIRECT_UPDATE_GRACE_S,
    INTERIM_POLL_DIRECT_INTERVAL_MS,
    INTERIM_POLL_INTERVAL_MS,
    INTERIM_POLL_STABLE_INTERVAL_MS,
    INTERIM_POLL_STABLE_THRESHOLD,
    INTERIM_QUEUE_BACKPRESSURE_LIMIT,
    INTERIM_POLL_MAX_CHARS,
    QUEUE_MAX_MESSAGES_PER_TICK,
    QUEUE_POLL_ACTIVE_MS,
    QUEUE_POLL_ACTIVE_IDLE_MS,
    QUEUE_POLL_IDLE_MS,
    WINDOW_HEIGHT,
    WINDOW_MARGIN_BOTTOM,
    WINDOW_WIDTH,
    TEXT_X_AFTER_BARS,
    WindowsOverlayController,
    _format_recording_interim_text,
)
from ui.animation import LISTENING_BAR_HEIGHTS
from ui.overlay_style import (
    BADGE_TONES,
    BAR_COLORS,
    INTERIM_TEXT_COLOR,
    MUTED_TEXT_COLOR,
    OVERLAY_GAP,
    OVERLAY_PADDING_H,
    TEXT_COLOR,
)


class _FakeRoot:
    def __init__(self, *, screen_w: int = 1920, screen_h: int = 1080):
        self._screen_w = screen_w
        self._screen_h = screen_h
        self.after_calls: list[int] = []
        self.after_cancel_calls: list[object] = []
        self._after_id_counter = 0
        self.last_geometry: str | None = None

    def after(self, _ms: int, _callback) -> str:
        self.after_calls.append(_ms)
        self._after_id_counter += 1
        return f"after-{self._after_id_counter}"

    def after_cancel(self, after_id: object) -> None:
        self.after_cancel_calls.append(after_id)

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
        self.config_calls = 0
        self.placed: dict[str, object] | None = None

    def config(self, **kwargs) -> None:
        self.config_calls += 1
        self.last_config.update(kwargs)

    def place(self, **kwargs) -> None:
        self.placed = kwargs

    def place_forget(self) -> None:
        self.placed = None

    def winfo_reqwidth(self) -> int:
        return 70


class _FakeCanvas:
    def __init__(self):
        self._item_id = 0
        self.create_calls = 0
        self.coords_calls = 0
        self.delete_calls: list[object] = []
        self.item_configs: list[dict[str, object]] = []

    def _create_item(self) -> int:
        self._item_id += 1
        self.create_calls += 1
        return self._item_id

    def create_arc(self, *_args, **_kwargs) -> int:
        return self._create_item()

    def create_rectangle(self, *_args, **_kwargs) -> int:
        return self._create_item()

    def coords(self, *_args, **_kwargs) -> None:
        self.coords_calls += 1

    def itemconfig(self, _item_id, **kwargs) -> None:
        self.item_configs.append(kwargs)

    def delete(self, tag_or_id) -> None:
        self.delete_calls.append(tag_or_id)


def _make_controller(interim_file: Path) -> WindowsOverlayController:
    controller = WindowsOverlayController.__new__(WindowsOverlayController)
    controller._running = True
    controller._root = _FakeRoot()
    controller._interim_file = interim_file
    controller._state = "RECORDING"
    controller._last_interim_text = ""
    controller._last_interim_signature = None
    controller._stable_interim_polls = 0
    controller._interim_polling_active = True
    controller._interim_poll_after_id = None
    controller._direct_interim_until = 0.0
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


def test_poll_interim_file_clears_stale_text_when_file_becomes_empty(tmp_path):
    interim_file = tmp_path / "interim.txt"
    interim_file.write_text("hello", encoding="utf-8")
    controller = _make_controller(interim_file)
    seen_texts: list[str] = []
    controller._handle_interim_text = seen_texts.append

    controller._poll_interim_file()
    interim_file.write_text("", encoding="utf-8")
    controller._poll_interim_file()

    assert seen_texts == ["hello", ""]


def test_poll_interim_file_reads_tail_text_only(tmp_path, monkeypatch):
    interim_file = tmp_path / "interim.txt"
    interim_file.write_text("full interim payload", encoding="utf-8")
    controller = _make_controller(interim_file)

    calls: list[tuple[object, int]] = []
    monkeypatch.setattr(
        "ui.overlay_windows.read_file_tail_text",
        lambda path, *, max_chars, errors="replace", **_kwargs: (
            calls.append((path, max_chars)),
            "tail-only",
        )[1],
    )

    seen_texts: list[str] = []
    controller._handle_interim_text = seen_texts.append

    controller._poll_interim_file()

    assert calls == [(interim_file, INTERIM_POLL_MAX_CHARS)]
    assert seen_texts == ["tail-only"]


def test_poll_interim_file_skips_reads_during_direct_update_grace(
    tmp_path, monkeypatch
):
    interim_file = tmp_path / "interim.txt"
    interim_file.write_text("hello", encoding="utf-8")
    controller = _make_controller(interim_file)

    monkeypatch.setattr("ui.overlay_windows.time.monotonic", lambda: 10.0)
    monkeypatch.setattr(
        "ui.overlay_windows.read_file_tail_text",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("file read should be skipped during direct-update grace")
        ),
    )
    controller._direct_interim_until = 10.0 + INTERIM_DIRECT_UPDATE_GRACE_S

    controller._poll_interim_file()

    assert controller._root.after_calls == [INTERIM_POLL_DIRECT_INTERVAL_MS]


def test_poll_interim_file_restores_fast_interval_after_direct_update_grace(
    tmp_path, monkeypatch
):
    interim_file = tmp_path / "interim.txt"
    interim_file.write_text("hello", encoding="utf-8")
    controller = _make_controller(interim_file)

    monotonic_values = iter([10.0, 10.0 + INTERIM_DIRECT_UPDATE_GRACE_S + 0.1])
    monkeypatch.setattr(
        "ui.overlay_windows.time.monotonic",
        lambda: next(monotonic_values),
    )
    controller._direct_interim_until = 10.0 + INTERIM_DIRECT_UPDATE_GRACE_S
    seen_texts: list[str] = []
    controller._handle_interim_text = seen_texts.append

    controller._poll_interim_file()
    controller._poll_interim_file()

    assert controller._root.after_calls == [
        INTERIM_POLL_DIRECT_INTERVAL_MS,
        INTERIM_POLL_INTERVAL_MS,
    ]
    assert seen_texts == ["hello"]


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


def test_update_state_skips_duplicate_payloads() -> None:
    controller = WindowsOverlayController()

    controller.update_state("LISTENING")
    controller.update_state("LISTENING")
    controller.update_state("LISTENING", "ready")

    assert controller._queue.qsize() == 2


def test_update_interim_text_skips_duplicate_queue_messages(monkeypatch) -> None:
    controller = WindowsOverlayController()

    monotonic_values = iter([10.0, 10.4])
    monkeypatch.setattr(
        "ui.overlay_windows.time.monotonic",
        lambda: next(monotonic_values),
    )

    controller.update_interim_text("alpha")
    controller.update_interim_text("alpha")

    assert controller._queue.qsize() == 1
    assert controller._direct_interim_until == 10.4 + INTERIM_DIRECT_UPDATE_GRACE_S


def test_handle_interim_text_skips_duplicate_label_configurations():
    controller = WindowsOverlayController()
    controller._state = "RECORDING"
    controller._label = _FakeLabel()

    controller._handle_interim_text("short text")
    controller._handle_interim_text("short text")

    assert controller._label.config_calls == 1


def test_format_recording_interim_text_compacts_whitespace():
    text = "  hello   world \n\n from   pulse  "
    assert _format_recording_interim_text(text) == "hello world from pulse"


def test_format_recording_interim_text_keeps_tail_for_long_text():
    text = "alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu"
    cleaned = " ".join(text.split())

    assert _format_recording_interim_text(text) == "..." + cleaned[-42:]


def test_format_recording_interim_text_handles_long_whitespace_heavy_text():
    text = ("alpha   beta   " * 80) + "  final   words  here "

    assert _format_recording_interim_text(text, max_chars=20) == "... final words here"


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


def test_get_primary_work_area_prefers_windows_work_area(monkeypatch):
    import ctypes
    import ui.overlay_windows as overlay_mod

    controller = WindowsOverlayController.__new__(WindowsOverlayController)
    controller._root = _FakeRoot(screen_w=1920, screen_h=1080)

    monkeypatch.setattr(overlay_mod.sys, "platform", "win32", raising=False)

    def fake_system_parameters_info(_action, _ui_param, rect_ptr, _flags):
        rect = rect_ptr._obj
        rect.left = 0
        rect.top = 0
        rect.right = 1600
        rect.bottom = 900
        return 1

    monkeypatch.setattr(
        ctypes,
        "windll",
        types.SimpleNamespace(
            user32=types.SimpleNamespace(
                SystemParametersInfoW=fake_system_parameters_info
            )
        ),
        raising=False,
    )

    assert controller._get_primary_work_area() == (0, 0, 1600, 900)


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

    assert controller._root.after_calls[-1] == QUEUE_POLL_ACTIVE_IDLE_MS


def test_poll_queue_keeps_fast_interval_after_processing_active_messages():
    controller = WindowsOverlayController.__new__(WindowsOverlayController)
    controller._running = True
    controller._root = _FakeRoot()
    controller._queue = queue.Queue()
    controller._state = "RECORDING"
    controller._audio_level = 0.0
    controller._handle_state_change = lambda *_args: None
    controller._handle_interim_text = lambda *_args: None
    controller._queue.put(("state", "RECORDING", None))

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


def test_poll_queue_limits_messages_per_tick_to_keep_ui_responsive():
    controller = WindowsOverlayController.__new__(WindowsOverlayController)
    controller._running = True
    controller._root = _FakeRoot()
    controller._queue = queue.Queue()
    controller._state = "IDLE"
    controller._audio_level = 0.0
    controller._handle_interim_text = lambda *_args: None

    for idx in range(QUEUE_MAX_MESSAGES_PER_TICK + 37):
        controller._queue.put(("interim", f"msg-{idx}", None))

    controller._poll_queue()

    assert controller._queue.qsize() == 37
    assert controller._root.after_calls[-1] == QUEUE_POLL_ACTIVE_MS


def test_handle_state_change_toggles_interim_polling_for_recording_only(tmp_path):
    interim_file = tmp_path / "interim.txt"
    interim_file.write_text("hello", encoding="utf-8")

    controller = WindowsOverlayController(interim_file=interim_file)
    controller._root = _FakeRoot()
    controller._label = _FakeLabel()
    controller._interim_poll_after_id = "after-existing"
    controller._interim_polling_active = True

    controller._handle_state_change("TRANSCRIBING", None)
    assert controller._interim_polling_active is False
    assert controller._root.after_cancel_calls == ["after-existing"]
    assert controller._last_interim_text == ""
    assert controller._last_interim_signature is None

    controller._handle_state_change("RECORDING", None)
    assert controller._interim_polling_active is True
    assert controller._root.after_calls[-1] == 0


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


def test_poll_interim_file_uses_configured_interval(tmp_path):
    interim_file = tmp_path / "interim.txt"
    interim_file.write_text("hello", encoding="utf-8")
    controller = _make_controller(interim_file)
    controller._poll_interim_file()

    assert controller._root.after_calls[-1] == INTERIM_POLL_INTERVAL_MS


def test_poll_interim_file_uses_stable_interval_after_repeated_unchanged_polls(tmp_path):
    interim_file = tmp_path / "interim.txt"
    interim_file.write_text("hello", encoding="utf-8")
    controller = _make_controller(interim_file)
    controller._last_interim_text = "hello"
    controller._last_interim_signature = (1, len("hello".encode("utf-8")))
    controller._stable_interim_polls = INTERIM_POLL_STABLE_THRESHOLD
    controller._handle_interim_text = lambda *_args: None

    import ui.overlay_windows as overlay_mod

    original_signature = overlay_mod.get_file_signature(interim_file)
    assert original_signature is not None
    controller._last_interim_signature = original_signature

    controller._poll_interim_file()

    assert controller._root.after_calls[-1] == INTERIM_POLL_STABLE_INTERVAL_MS


def test_render_bars_reuses_canvas_items_between_frames():
    controller = WindowsOverlayController.__new__(WindowsOverlayController)
    controller._canvas = _FakeCanvas()
    controller._state = "RECORDING"
    controller._bar_heights = [BAR_MIN_HEIGHT] * BAR_COUNT
    controller._bar_item_ids = []
    controller._anim = types.SimpleNamespace(
        calculate_bar_height=lambda *_args: BAR_MIN_HEIGHT + 4
    )

    controller._render_bars(0.1)
    created_first_pass = controller._canvas.create_calls
    coords_first_pass = controller._canvas.coords_calls

    controller._render_bars(0.2)

    assert created_first_pass == BAR_COUNT * 3
    assert controller._canvas.create_calls == created_first_pass
    assert controller._canvas.coords_calls > coords_first_pass
    assert controller._canvas.delete_calls == []


def test_render_bars_prefers_batch_height_api():
    controller = WindowsOverlayController.__new__(WindowsOverlayController)
    controller._canvas = _FakeCanvas()
    controller._state = "RECORDING"
    controller._bar_heights = [BAR_MIN_HEIGHT] * BAR_COUNT
    controller._bar_item_ids = []
    controller._anim = types.SimpleNamespace(
        calculate_frame_heights=lambda *_args, **_kwargs: (
            BAR_MIN_HEIGHT + 4,
        )
        * BAR_COUNT,
        calculate_bar_height=lambda *_args: (_ for _ in ()).throw(
            AssertionError("batch frame API should be used")
        ),
    )

    controller._render_bars(0.1)

    assert controller._canvas.create_calls == BAR_COUNT * 3


def test_draw_pill_bar_skips_subpixel_canvas_updates():
    controller = WindowsOverlayController.__new__(WindowsOverlayController)
    controller._canvas = _FakeCanvas()
    controller._bar_item_ids = [(1, 2, 3)]
    controller._drawn_bar_heights = [10.0] + [BAR_MIN_HEIGHT] * (BAR_COUNT - 1)

    controller._draw_pill_bar(
        0,
        x=0.0,
        center_y=20.0,
        width=4.0,
        height=10.0 + (BAR_HEIGHT_UPDATE_EPSILON / 2),
    )

    assert controller._canvas.coords_calls == 0

    controller._draw_pill_bar(
        0,
        x=0.0,
        center_y=20.0,
        width=4.0,
        height=10.0 + BAR_HEIGHT_UPDATE_EPSILON + 0.1,
    )

    assert controller._canvas.coords_calls == 3


def _content_controller() -> WindowsOverlayController:
    controller = WindowsOverlayController()
    controller._root = _FakeRoot()
    controller._label = _FakeLabel()
    controller._badge_label = _FakeLabel()
    return controller


def test_handle_state_change_shows_outcome_badges_next_to_muted_text():
    controller = _content_controller()

    controller._handle_state_change("DONE", "✓ (0.4x)")
    assert controller._badge_label.last_config["text"] == "Pasted"
    assert controller._badge_label.last_config["bg"] == BADGE_TONES["success"][0]
    assert controller._label.last_config["text"] == "0.4x"
    assert controller._label.placed["x"] == OVERLAY_PADDING_H + 70 + OVERLAY_GAP

    controller._handle_state_change("ERROR", "Boom")
    assert controller._badge_label.last_config["text"] == "Error"
    assert controller._label.last_config["text"] == "Boom"
    assert controller._label.last_config["fg"] == MUTED_TEXT_COLOR

    controller._handle_state_change("LOADING", None)
    assert controller._badge_label.placed is None
    assert controller._label.last_config["text"] == "Preparing…"
    assert controller._label.placed["x"] == TEXT_X_AFTER_BARS


def test_handle_interim_text_uses_muted_interim_color_and_restores_status():
    controller = _content_controller()
    controller._state = "RECORDING"

    controller._handle_interim_text("short text")
    assert controller._label.last_config["text"] == "short text"
    assert controller._label.last_config["fg"] == INTERIM_TEXT_COLOR

    controller._handle_interim_text("")
    assert controller._label.last_config["text"] == "Recording"
    assert controller._label.last_config["fg"] == TEXT_COLOR


def test_animate_runs_only_while_recording():
    controller = WindowsOverlayController.__new__(WindowsOverlayController)
    controller._running = True
    controller._root = _FakeRoot()
    controller._state = "TRANSCRIBING"
    controller._animation_running = True

    controller._animate()

    assert controller._animation_running is False
    assert controller._root.after_calls == []


def test_static_bar_modes_draw_once_with_mode_color():
    controller = WindowsOverlayController.__new__(WindowsOverlayController)
    controller._canvas = _FakeCanvas()
    controller._bar_item_ids = []
    controller._bar_color = None
    controller._line_item_id = None

    controller._show_bar_mode("low")
    assert {"fill": BAR_COLORS["low"], "outline": ""} in controller._canvas.item_configs
    assert controller._bar_heights == [float(h) for h in LISTENING_BAR_HEIGHTS]

    controller._show_bar_mode("line")
    assert {"state": "normal"} in controller._canvas.item_configs[-2:]
    assert controller._canvas.item_configs[-1] == {"state": "hidden"}


def test_errors_persist_through_idle_until_clicked():
    controller = _content_controller()
    controller._dismiss_label = _FakeLabel()
    withdrawn = []
    controller._root.withdraw = lambda: withdrawn.append(True)

    controller._handle_state_change("ERROR", "boom")
    assert controller._dismiss_label.placed is not None
    controller._handle_state_change("IDLE", None)
    assert withdrawn == []

    controller._dismiss()
    assert withdrawn == [True]
    controller._dismiss()
    assert withdrawn == [True]

    controller._handle_state_change("LISTENING", None)
    assert controller._dismiss_label.placed is None
    controller._handle_state_change("IDLE", None)
    assert withdrawn == [True, True]
