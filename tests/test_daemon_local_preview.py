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
        "Live-Vorschau: aus (Modell large-v3 ist dafür zu langsam, turbo oder das "
        "Streaming-Modell zeigen Live-Text)"
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


@pytest.mark.parametrize(
    "setup,expected",
    [
        (lambda d, env: None, True),  # local preview allowed, MLX backend
        (lambda d, env: setattr(d, "model", "large-v3"), False),
        (lambda d, env: env("PULSESCRIBE_LOCAL_BACKEND", "faster"), False),
        (lambda d, env: setattr(d, "mode", "deepgram"), True),  # Deepgram streams
        (lambda d, env: (setattr(d, "mode", "deepgram"), env("PULSESCRIBE_STREAMING", "false")), False),
        (lambda d, env: setattr(d, "mode", "openai"), False),
    ],
    ids=["local-preview", "slow-model", "other-backend", "deepgram", "deepgram-rest", "cloud"],
)
def test_recording_start_sizes_the_overlay_for_live_text(daemon, monkeypatch, setup, expected):
    import pulsescribe_daemon

    events = []
    daemon._overlay = SimpleNamespace(
        set_live_text_expected=lambda value: events.append(("live text", value)),
        update_state=lambda state, _text=None: events.append(state),
    )
    monkeypatch.setenv("PULSESCRIBE_LOCAL_BACKEND", "mlx")
    monkeypatch.setattr(daemon, "_call_on_main", lambda fn: fn())
    monkeypatch.setattr(daemon, "_do_ui_update", lambda state, _text=None: events.append(state))
    monkeypatch.setattr(threading, "current_thread", lambda: SimpleNamespace(name="MainThread"))
    for name in ("_preload_local_model_async", "_start_result_polling", "_start_interim_polling"):
        monkeypatch.setattr(daemon, name, lambda *a: None)
    for name in ("_recording_worker", "_streaming_worker"):
        monkeypatch.setattr(daemon, name, lambda *a: None)
    monkeypatch.setattr(
        pulsescribe_daemon, "start_macos_latency_run",
        lambda **k: SimpleNamespace(mark=lambda *a: None, finish=lambda *a: None),
    )
    setup(daemon, monkeypatch.setenv)

    daemon._start_recording_locked()

    # Announced before the first state of the run, so the overlay opens at its size.
    assert events[:2] == [("live text", expected), AppState.LISTENING]


def test_no_overlay_means_nothing_to_size(daemon):
    daemon._overlay = None
    daemon._expect_live_text(True)


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


class _StreamingModel:
    """Stands in for providers.streaming_model.StreamingModel."""

    def __init__(self, *, installed: bool = True, downloadable: bool = True) -> None:
        self._installed = installed
        self._downloadable = downloadable
        self.loaded = False
        self.events: list[str] = []
        self.languages: list[str | None] = []

    def installed(self) -> bool:
        return self._installed

    def download(self, on_progress) -> bool:
        self.events.append("download")
        on_progress(341_000_000, 682_000_000)
        return self._downloadable

    def load(self) -> None:
        self.events.append("load")
        self.loaded = True

    def release(self) -> bool:
        had_model, self.loaded = self.loaded, False
        self.events.append("release")
        return had_model

    def open_session(self, language):
        self.languages.append(language)
        fed = []
        return SimpleNamespace(
            feed=lambda samples: fed.append(len(samples)),
            text=lambda: "Heute  ist ein" if fed else "",
        )


@pytest.fixture
def streaming(daemon, monkeypatch):
    """The daemon with the streaming preview chosen and its model downloaded."""
    monkeypatch.setenv("PULSESCRIBE_LOCAL_PREVIEW", "streaming")
    monkeypatch.setattr("audio.streaming_preview.POLL_S", 0.005)
    daemon._streaming_model = _StreamingModel()
    daemon._streaming_available = True
    return daemon


def test_streaming_preview_shows_words_and_whisper_writes_the_transcript(streaming, caplog):
    provider = streaming._provider_cache["local"]
    provider.backend = "faster"  # any backend ...
    streaming.model = "large-v3"  # ... and any model

    with caplog.at_level("INFO", logger="pulsescribe"):
        # Whisper does nothing until the stop, so the recording ends by time.
        interim, results = _record(
            streaming, _chunks(0.1, 1.5), preview_passes=99, timeout_s=0.3
        )

    assert interim == ["Heute ist ein"]
    assert results == [" Heute ist ein schöner Tag."]
    # Whisper ran only the final pass; the preview never replaces it.
    assert [(c["draft"], c["quiet"]) for c in provider.calls] == [(False, False)]
    assert streaming._streaming_model.languages == ["de"]
    line = next(m for m in (r.getMessage() for r in caplog.records) if m.startswith("Live-Vorschau"))
    assert line.startswith("Live-Vorschau (Streaming): 1 Aktualisierungen, Rechenanteil ")


@pytest.mark.parametrize(
    "setup",
    [
        lambda d, env: env("PULSESCRIBE_LOCAL_PREVIEW", "true"),
        lambda d, env: setattr(d, "_streaming_available", False),  # still downloading
        lambda d, env: setattr(d, "_streaming_off_until", time.monotonic() + 60),
    ],
    ids=["whisper-chosen", "not-downloaded", "paused"],
)
def test_without_the_streaming_model_whisper_previews(streaming, monkeypatch, setup):
    setup(streaming, monkeypatch.setenv)

    assert not streaming._streaming_preview_applies("local")
    assert isinstance(streaming._create_local_preview(queue.Queue()), live_preview.LivePreview)


def test_streaming_preview_is_for_local_recordings_only(streaming):
    assert streaming._streaming_preview_applies("local")
    assert not streaming._streaming_preview_applies("deepgram")
    streaming.mode = "deepgram"  # local test dictation of a cloud setup
    assert not streaming._streaming_preview_applies("local")


def test_streaming_preview_that_falls_behind_pauses(streaming, caplog):
    from audio.streaming_preview import StreamingSummary

    preview = SimpleNamespace(
        finish=lambda: StreamingSummary(
            updates=3, compute_share=1.4, step_max_s=2.6, too_slow=True, failed=False
        )
    )
    with caplog.at_level("INFO", logger="pulsescribe"):
        streaming._finish_local_preview(preview)

    assert not streaming._streaming_preview_applies("local")
    assert streaming._streaming_off_until > time.monotonic() + 29 * 60
    assert caplog.records[-1].getMessage() == (
        "Live-Vorschau (Streaming): 3 Aktualisierungen, Rechenanteil 140%, längster Schritt "
        "2.60s, beendet: Modell kam nicht hinterher, nächster Versuch in 30 min"
    )


def _sync(daemon) -> None:
    daemon._sync_streaming_model()
    thread = daemon._streaming_setup_thread
    if thread is not None:
        thread.join(timeout=5)


def test_sync_downloads_and_loads_the_model_once(streaming):
    model = streaming._streaming_model
    streaming._streaming_available = False
    shown = []
    streaming._update_state = lambda state, text=None: (
        shown.append((state, text)),
        setattr(streaming, "_current_state", state),
    )

    _sync(streaming)
    _sync(streaming)  # loaded: nothing to do

    assert model.events == ["download", "load"]
    assert streaming._streaming_available
    # Progress shows while idle and goes away again.
    assert shown == [
        (AppState.LOADING, "50 % · downloading live preview model (682 MB)"),
        (AppState.IDLE, None),
    ]


def test_sync_leaves_the_status_alone_while_whisper_loads_or_a_recording_runs(streaming):
    streaming._streaming_available = False
    shown = []
    streaming._update_state = lambda state, text=None: shown.append(state)

    streaming._local_preload_complete.clear()  # Whisper owns the loading status
    _sync(streaming)
    streaming._streaming_model = _StreamingModel()
    streaming._local_preload_complete.set()
    streaming._current_state = AppState.RECORDING
    _sync(streaming)

    assert shown == []
    assert streaming._streaming_model.loaded


def test_sync_does_not_end_a_loading_status_whisper_took_over(streaming):
    streaming._streaming_available = False
    shown = []

    def update_state(state, text=None):
        shown.append(state)
        streaming._current_state = state
        # Whisper starts loading (e.g. the model setting changed) and owns the status now.
        streaming._local_preload_complete.clear()

    streaming._update_state = update_state
    _sync(streaming)

    assert shown == [AppState.LOADING]  # no IDLE from the download while Whisper loads


def test_sync_frees_the_model_when_it_is_no_longer_wanted(streaming, monkeypatch):
    model = streaming._streaming_model
    model.loaded = True

    monkeypatch.setenv("PULSESCRIBE_LOCAL_PREVIEW", "true")
    streaming._sync_streaming_model()
    assert model.events == ["release"] and not model.loaded

    monkeypatch.setenv("PULSESCRIBE_LOCAL_PREVIEW", "streaming")
    streaming.mode = "deepgram"  # only local mode uses it
    model.loaded = True
    streaming._sync_streaming_model()
    assert not model.loaded


@pytest.mark.parametrize(
    "model,available",
    [(_StreamingModel(installed=False), False), (_StreamingModel(downloadable=False), False)],
    ids=["sherpa-missing", "download-failed"],
)
def test_sync_without_a_usable_model_keeps_whisper_previewing(streaming, caplog, model, available):
    streaming._streaming_model = model
    streaming._streaming_available = False

    with caplog.at_level("WARNING", logger="pulsescribe"):
        _sync(streaming)

    assert streaming._streaming_available is available and not model.loaded
    assert len(caplog.records) == 1
    assert not streaming._streaming_preview_applies("local")


def test_idle_unload_frees_the_streaming_model_too(streaming, monkeypatch):
    model = streaming._streaming_model
    model.loaded = True
    monkeypatch.setattr(streaming, "_cancel_local_warm_lifecycle", lambda **_k: None)
    monkeypatch.setattr(streaming, "_release_local_provider_model_cache", lambda: None)
    with streaming._local_warm_lock:
        generation = streaming._local_warm_generation

    streaming._unload_idle_local_model(generation, 15)

    assert not model.loaded
    # The next recording still gets the preview; opening its session loads the model.
    assert streaming._streaming_preview_applies("local")
