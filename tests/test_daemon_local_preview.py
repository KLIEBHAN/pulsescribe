"""Live text preview of local recordings in the macOS daemon."""

import queue
import sys
import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from audio import live_preview
from pulsescribe_daemon import LOCAL_TAIL_S, PulseScribeDaemon
from utils.macos_latency_diagnostics import start_macos_latency_run
from utils.state import AppState, DaemonMessage, MessageType

RATE = 16000
CHUNK = 320  # 20 ms


def _chunks(level: float, seconds: float) -> list[np.ndarray]:
    rng = np.random.default_rng(int(level * 1e6))
    return [
        (level * rng.standard_normal((CHUNK, 1))).astype(np.float32)
        for _ in range(round(seconds * RATE / CHUNK))
    ]


class _Provider:
    """Stands in for LocalProvider and records every transcription call."""

    default_model = "turbo"

    def __init__(self, backend: str = "mlx") -> None:
        self.backend = backend
        self.calls: list[dict] = []

    def get_runtime_info(self) -> dict:
        return {"backend": self.backend, "device": "metal"}

    def transcribe_audio(self, audio, model=None, language=None, *, draft=False, quiet=False):
        self.calls.append({"samples": len(audio), "draft": draft, "quiet": quiet})
        return " Heute ist ein schöner Tag."


@pytest.fixture
def daemon(monkeypatch):
    monkeypatch.delenv("PULSESCRIBE_LOCAL_PREVIEW", raising=False)
    monkeypatch.delenv("PULSESCRIBE_LOCAL_MODEL", raising=False)
    monkeypatch.setattr(live_preview, "POLL_S", 0.005)
    d = PulseScribeDaemon(mode="local", language="de")
    d._run_mode = "local"
    d._local_preload_complete.set()
    d._provider_cache["local"] = _Provider()
    monkeypatch.setattr(d, "_do_ui_update", lambda *_a: None)
    return d


def _record(
    daemon: PulseScribeDaemon,
    chunks: list[np.ndarray],
    *,
    preview_passes: int,
    timeout_s: float = 5.0,
):
    """Run the recording worker on ``chunks``.

    Stops once ``preview_passes`` passes ran, at the latest after ``timeout_s``.
    """
    provider = daemon._provider_cache["local"]
    result_queue: queue.Queue = queue.Queue()
    stop_event = threading.Event()
    stream = {}

    def input_stream(**kwargs):
        stream.update(kwargs)

        def start():
            for chunk in chunks:
                kwargs["callback"](chunk, len(chunk), None, None)

        return SimpleNamespace(start=start, close=lambda: None, abort=lambda: None)

    deadline = time.monotonic() + timeout_s

    def sleep(_ms):
        time.sleep(0.005)
        if len(provider.calls) >= preview_passes or time.monotonic() > deadline:
            stop_event.set()
            stream["finished_callback"]()

    sounddevice = SimpleNamespace(
        InputStream=input_stream, CallbackAbort=RuntimeError, sleep=sleep
    )
    with (
        patch.dict(sys.modules, {"sounddevice": sounddevice}),
        patch("pulsescribe_daemon.get_sound_player", return_value=MagicMock()),
        patch("audio.device_refresh.open_input_stream", lambda _sd, create: create()),
        patch("audio.device_refresh.release_input_stream", lambda _stream: None),
        patch("config.get_input_device", lambda: (None, RATE)),
    ):
        daemon._recording_worker(
            1, result_queue, stop_event, start_macos_latency_run(enabled=False)
        )

    messages = []
    while not result_queue.empty():
        messages.append(result_queue.get_nowait())
    interim = [m.payload for m in messages if m.type == MessageType.INTERIM_TEXT]
    results = [m.payload for m in messages if m.type == MessageType.TRANSCRIPT_RESULT]
    return interim, results


def test_pause_before_stop_reuses_the_preview_pass_as_transcript(daemon):
    provider = daemon._provider_cache["local"]

    interim, results = _record(
        daemon, _chunks(0.1, 1.5) + _chunks(0.0002, 0.5), preview_passes=1
    )

    assert interim == ["Heute ist ein schöner Tag."]
    assert results == [" Heute ist ein schöner Tag."]
    assert [(c["draft"], c["quiet"]) for c in provider.calls] == [(False, True)]
    assert daemon._last_transcribe_seconds == 0.0  # shown as the transcription time


def test_each_recording_logs_what_the_preview_did(daemon, caplog):
    with caplog.at_level("INFO", logger="pulsescribe"):
        _record(daemon, _chunks(0.1, 1.5) + _chunks(0.0002, 0.5), preview_passes=1)
        # Too quiet to count as speech: no pass, and the log says why.
        _record(daemon, _chunks(0.012, 0.2), preview_passes=99, timeout_s=0.1)

    lines = [
        message
        for message in (record.getMessage() for record in caplog.records)
        if message.startswith("Live-Vorschau:")
    ]
    assert lines[0].startswith("Live-Vorschau: 0 Entwürfe, 1 nach Pausen, längster Durchlauf ")
    assert lines[1].startswith("Live-Vorschau: kein Durchlauf (erkannte Sprache 0.2s, max_rms=0.01")
    assert len(lines) == 2


def test_failed_preview_is_named_in_the_log(daemon, caplog):
    provider = daemon._provider_cache["local"]
    final_pass = provider.transcribe_audio

    def transcribe_audio(audio, model=None, language=None, *, draft=False, quiet=False):
        if quiet:  # preview passes
            raise RuntimeError("model gone")
        return final_pass(audio, model, language, draft=draft, quiet=quiet)

    provider.transcribe_audio = transcribe_audio
    with caplog.at_level("INFO", logger="pulsescribe"):
        interim, results = _record(daemon, _chunks(0.1, 1.5), preview_passes=99, timeout_s=0.3)

    assert interim == [] and results == [" Heute ist ein schöner Tag."]
    messages = [record.getMessage() for record in caplog.records]
    assert "Live-Vorschau: nach einem Fehler beendet (siehe Fehlermeldung davor)" in messages


def test_quiet_microphone_still_previews(daemon):
    # Loudest chunk 0.012 like a MacBook microphone at low input volume.
    speech = _chunks(0.004, 1.2) + _chunks(0.012, 0.3)

    interim, results = _record(daemon, speech, preview_passes=1)

    assert interim == ["Heute ist ein schöner"]
    assert len(results) == 1


def test_stop_while_speaking_runs_the_normal_final_pass(daemon):
    provider = daemon._provider_cache["local"]

    interim, results = _record(daemon, _chunks(0.1, 1.5), preview_passes=1)

    assert interim == ["Heute ist ein schöner"]  # draft: last word held back
    assert results == [" Heute ist ein schöner Tag."]
    assert [(c["draft"], c["quiet"]) for c in provider.calls] == [
        (True, True),
        (False, False),
    ]
    # Both passes see the same audio shape: trimmed, plus the local tail.
    assert provider.calls[0]["samples"] == provider.calls[1]["samples"]
    assert provider.calls[1]["samples"] == int((1.5 + LOCAL_TAIL_S) * RATE)


@pytest.mark.parametrize(
    "setup",
    [
        lambda d, env: env("PULSESCRIBE_LOCAL_PREVIEW", "false"),
        lambda d, env: setattr(d._provider_cache["local"], "backend", "faster"),
        lambda d, env: setattr(d, "_run_mode", "openai"),
        # Local test dictation of a cloud setup: no model is preloaded.
        lambda d, env: setattr(d, "mode", "deepgram"),
        lambda d, env: setattr(
            d._provider_cache["local"], "get_runtime_info", MagicMock(side_effect=OSError)
        ),
    ],
    ids=["disabled", "not-mlx", "not-local", "cloud-setup", "backend-lookup-fails"],
)
def test_no_preview_when_it_does_not_apply(daemon, monkeypatch, setup):
    setup(daemon, monkeypatch.setenv)

    assert daemon._create_local_preview(queue.Queue()) is None


@pytest.mark.parametrize("configure", ["argument", "environment"])
def test_slow_model_gets_no_preview_and_the_log_says_why(daemon, monkeypatch, caplog, configure):
    if configure == "argument":
        daemon.model = "large-v3"
    else:
        monkeypatch.setenv("PULSESCRIBE_LOCAL_MODEL", "large-v3")

    with caplog.at_level("INFO", logger="pulsescribe"):
        preview = daemon._create_local_preview(queue.Queue())

    assert preview is None
    assert [record.getMessage() for record in caplog.records] == [
        "Live-Vorschau: aus (Modell large-v3 ist dafür zu langsam, turbo zeigt Live-Text)"
    ]

    # On another backend turbo would show no live text either: no such advice.
    caplog.clear()
    daemon._provider_cache["local"].backend = "faster"
    with caplog.at_level("INFO", logger="pulsescribe"):
        assert daemon._create_local_preview(queue.Queue()) is None
    assert caplog.records == []


def test_preview_too_slow_from_the_start_pauses_for_this_model(daemon, monkeypatch, caplog):
    monkeypatch.setattr(live_preview, "SLOW_DRAFT_S", 0.0)  # every draft is slow
    monkeypatch.setattr(live_preview, "SLOW_DRAFTS_BEFORE_END", 1)
    provider = daemon._provider_cache["local"]

    with caplog.at_level("INFO", logger="pulsescribe"):
        interim, results = _record(daemon, _chunks(0.1, 1.5), preview_passes=1)
        assert interim == [] and len(results) == 1  # nothing was shown, nothing to clear
        assert [c["draft"] for c in provider.calls] == [True, False]

        # The next recordings of this model run no preview pass at all ...
        assert daemon._create_local_preview(queue.Queue()) is None

    lines = [m for m in (r.getMessage() for r in caplog.records) if m.startswith("Live-Vorschau")]
    assert lines[0].endswith(", beendet: Entwürfe zu langsam")
    assert lines[1] == (
        "Live-Vorschau: aus (Entwürfe waren mit diesem Modell auf diesem Mac zu langsam, "
        "nächster Versuch in 30 min)"
    )

    # ... until the model changes or half an hour passed.
    signature, _retry_at = daemon._preview_off
    daemon.model = "small"
    assert daemon._create_local_preview(queue.Queue()) is not None
    daemon.model = None
    daemon._preview_off = (signature, time.monotonic() - 1)
    assert daemon._create_local_preview(queue.Queue()) is not None


def test_slow_drafts_later_in_a_recording_do_not_pause_the_next_one(daemon, monkeypatch):
    # One fast draft, then slow ones: a long recording, not a Mac that is too slow.
    preview = daemon._create_local_preview(queue.Queue())
    monkeypatch.setattr(
        preview,
        "finish",
        lambda: live_preview.PreviewSummary(
            drafts=5, fast_drafts=3, settled=0, draft_median_s=0.9, slowest_s=1.2,
            too_slow=True, speech_s=20.0, max_level=0.1, model_ready=True, failed=False,
        ),
    )

    daemon._finish_local_preview(preview)

    assert daemon._preview_off is None


def test_obstacle_tells_whether_settings_allow_the_preview(daemon, monkeypatch):
    assert daemon._local_preview_obstacle("local") is None
    assert daemon._local_preview_obstacle("deepgram") == ""  # does not apply, nothing to log
    daemon.model = "large-v3"
    assert "large-v3" in daemon._local_preview_obstacle("local")
    daemon.model = None
    monkeypatch.setenv("PULSESCRIBE_LOCAL_PREVIEW", "false")
    assert daemon._local_preview_obstacle("local") == ""


def test_recording_start_sizes_the_overlay_for_live_text(daemon, monkeypatch):
    announced = []
    daemon._overlay = SimpleNamespace(set_live_text_expected=announced.append)
    monkeypatch.setattr(daemon, "_call_on_main", lambda fn: fn())

    daemon._expect_live_text(True)
    daemon._expect_live_text(False)
    daemon._overlay = None
    daemon._expect_live_text(True)  # no overlay: nothing to size

    assert announced == [True, False]


def test_preview_waits_for_a_loading_model_and_never_runs_on_other_backends(daemon):
    provider = daemon._provider_cache["local"]
    provider.backend = "faster"
    daemon._local_preload_complete.clear()

    # Backend unknown while loading: the preview exists but stays idle.
    interim, results = _record(
        daemon, _chunks(0.1, 1.5), preview_passes=99, timeout_s=0.1
    )

    assert interim == []
    assert [(c["draft"], c["quiet"]) for c in provider.calls] == [(False, False)]
    assert len(results) == 1


def test_interim_text_shows_only_while_recording(daemon):
    shown = []
    daemon._update_state = lambda state, text=None: (
        shown.append((state, text)),
        setattr(daemon, "_current_state", state),
    )
    message = DaemonMessage(type=MessageType.INTERIM_TEXT, payload="Heute ist")
    batch = SimpleNamespace(
        latest_audio_level=None, saw_vad_trigger=False, reset_audio_level=lambda: None
    )

    daemon._current_state = AppState.LISTENING
    assert daemon._handle_result_queue_item(message, batch) is False
    # The preview ended early: its text disappears, the recording goes on.
    cleared = DaemonMessage(type=MessageType.INTERIM_TEXT, payload="")
    assert daemon._handle_result_queue_item(cleared, batch) is False
    daemon._current_state = AppState.TRANSCRIBING  # a pass finished after the stop
    assert daemon._handle_result_queue_item(message, batch) is False

    assert shown == [(AppState.RECORDING, "Heute ist"), (AppState.RECORDING, None)]


def test_shape_recorded_audio_trims_and_adds_the_tail_only_for_local():
    audio = np.concatenate(_chunks(0.0002, 1.0) + _chunks(0.1, 1.0) + _chunks(0.0002, 1.0))

    cloud = PulseScribeDaemon._shape_recorded_audio(audio, 0.1, local=False)
    local = PulseScribeDaemon._shape_recorded_audio(audio, 0.1, local=True)

    assert 1.4 * RATE < len(cloud) < 1.6 * RATE  # speech plus 0.25 s padding per side
    assert len(local) == len(cloud) + int(LOCAL_TAIL_S * RATE)
    assert not local[len(cloud) :].any()
