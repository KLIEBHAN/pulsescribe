"""Live preview from a streaming model (audio/streaming_preview.py, providers/streaming_model.py)."""

import sys
import threading
import types

import numpy as np
import pytest

from audio import streaming_preview
from audio.streaming_preview import StreamingPreview
from providers import streaming_model
from providers.streaming_model import StreamingModel

RATE = 16000


def _chunks(seconds: float) -> list[np.ndarray]:
    return [np.zeros((320, 1), dtype=np.float32) for _ in range(round(seconds * RATE / 320))]


class _Session:
    """A model that "recognizes" one word per second of audio it was fed."""

    def __init__(self, *, fail_after: int | None = None, step_s: float = 0.0) -> None:
        self.samples = 0
        self.feeds = 0
        self.shapes: list[tuple] = []
        self._fail_after = fail_after
        self._step_s = step_s
        self.fed = threading.Semaphore(0)

    def feed(self, samples) -> None:
        self.feeds += 1
        self.shapes.append(samples.shape)
        if self._fail_after is not None and self.feeds > self._fail_after:
            self.fed.release()
            raise RuntimeError("model gone")
        threading.Event().wait(self._step_s)
        self.samples += len(samples)
        self.fed.release()

    def text(self) -> str:
        return "  ".join(f"wort{i}" for i in range(self.samples // RATE))


@pytest.fixture(autouse=True)
def _fast_poll(monkeypatch):
    monkeypatch.setattr(streaming_preview, "POLL_S", 0.005)


def _preview(session, published):
    return StreamingPreview(
        open_session=lambda: session, publish=published.append, sample_rate=RATE
    )


def test_publishes_the_text_as_the_recording_grows():
    session, published = _Session(), []
    chunks = _chunks(1.0)
    stop = threading.Event()
    preview = _preview(session, published)

    preview.start(chunks, [], stop)
    assert session.fed.acquire(timeout=5)
    chunks.extend(_chunks(2.0))
    assert session.fed.acquire(timeout=5)
    stop.set()
    summary = preview.finish()

    assert published == ["wort0", "wort0 wort1 wort2"]  # whitespace normalized
    assert all(len(shape) == 1 for shape in session.shapes)  # mono samples
    assert session.samples == 3 * RATE  # every sample exactly once
    assert (summary.updates, summary.too_slow, summary.failed) == (2, False, False)
    assert summary.step_max_s >= 0 and 0 <= summary.compute_share < 1
    assert preview.final_text_for(np.zeros(3)) is None  # never the transcript


def test_nothing_is_fed_after_the_stop():
    session, published = _Session(), []
    stop = threading.Event()
    stop.set()
    preview = _preview(session, published)

    preview.start(_chunks(1.0), [], stop)
    summary = preview.finish()

    assert session.feeds == 0 and published == []
    assert summary.updates == 0


def test_model_error_ends_the_preview_and_removes_its_text(caplog):
    session, published = _Session(fail_after=1), []
    chunks = _chunks(1.0)
    stop = threading.Event()
    preview = _preview(session, published)

    with caplog.at_level("ERROR", logger="pulsescribe.audio"):
        preview.start(chunks, [], stop)
        assert session.fed.acquire(timeout=5)
        chunks.extend(_chunks(1.0))
        assert session.fed.acquire(timeout=5)
        thread = preview._thread
        thread.join(timeout=5)
        ended = not thread.is_alive()
        summary = preview.finish()

    assert ended
    assert published == ["wort0", ""]
    assert summary.failed and not summary.too_slow
    assert [record.getMessage() for record in caplog.records] == [
        "Live-Vorschau (Streaming) abgebrochen"
    ]


def test_session_that_cannot_open_fails_quietly(caplog):
    def no_session():
        raise RuntimeError("live preview model is not downloaded")

    published: list[str] = []
    preview = StreamingPreview(open_session=no_session, publish=published.append, sample_rate=RATE)

    with caplog.at_level("ERROR", logger="pulsescribe.audio"):
        preview.start(_chunks(1.0), [], threading.Event())
        summary = preview.finish()

    assert summary.failed and published == []  # nothing was shown, nothing to remove


def test_model_that_falls_behind_ends_the_preview(monkeypatch):
    monkeypatch.setattr(streaming_preview, "MAX_BACKLOG_S", 0.5)
    entered, proceed = threading.Event(), threading.Event()

    class SlowSession(_Session):
        def feed(self, samples) -> None:
            entered.set()
            proceed.wait(5)  # the step takes a while ...
            super().feed(samples)

    session, published = SlowSession(), []
    chunks = _chunks(1.0)
    preview = _preview(session, published)

    preview.start(chunks, [], threading.Event())
    assert entered.wait(5)
    chunks.extend(_chunks(1.0))  # ... and a second of new audio piles up meanwhile
    proceed.set()
    thread = preview._thread
    thread.join(timeout=5)
    ended = not thread.is_alive()
    summary = preview.finish()

    assert ended and summary.too_slow and not summary.failed
    assert published == []  # its text would be a second late: none is shown
    assert session.feeds == 1


def test_long_text_is_published_as_its_tail():
    words = " ".join(f"wort{i}" for i in range(200))
    tail = streaming_preview._tail(words)
    assert len(tail) <= streaming_preview.MAX_TEXT_CHARS
    assert words.endswith(tail) and tail.startswith("wort")
    unspaced = "今" * 1000
    assert streaming_preview._tail(unspaced) == unspaced[-streaming_preview.MAX_TEXT_CHARS :]
    assert streaming_preview._tail("kurz") == "kurz"


class _FakeStream:
    def __init__(self) -> None:
        self.options: dict[str, str] = {}
        self.waveforms: list[tuple[int, int]] = []

    def set_option(self, key: str, value: str) -> None:
        self.options[key] = value

    def accept_waveform(self, rate: int, samples) -> None:
        self.waveforms.append((rate, len(samples)))


class _FakeRecognizer:
    created: list[dict] = []

    def __init__(self) -> None:
        self.streams: list[_FakeStream] = []
        self.pending = 0
        self.decoded = 0

    @classmethod
    def from_transducer(cls, **kwargs):
        cls.created.append(kwargs)
        return cls()

    def create_stream(self) -> _FakeStream:
        self.streams.append(_FakeStream())
        return self.streams[-1]

    def is_ready(self, _stream) -> bool:
        return self.pending > 0

    def decode_stream(self, _stream) -> None:
        self.pending -= 1
        self.decoded += 1

    def get_result(self, _stream) -> str:
        return f"{self.decoded} Abschnitte"


@pytest.fixture
def model_files(monkeypatch, tmp_path):
    """A downloaded model folder and a sherpa_onnx stand-in."""
    for name in streaming_model.MODEL_FILES:
        (tmp_path / name).write_bytes(b"x")
    (tmp_path / "tokens.txt").write_text("<unk> 0\n<de-DE> 1\n<en-US> 2\n<en-GB> 3\n▁ 4\n", encoding="utf-8")
    folder = {"path": tmp_path}
    seen = []

    def cached_snapshot(repo_id, *, revision, required_files):
        seen.append((repo_id, revision, tuple(required_files)))
        return folder["path"]

    monkeypatch.setattr(streaming_model, "cached_snapshot", cached_snapshot)
    _FakeRecognizer.created = []
    sherpa = types.ModuleType("sherpa_onnx")
    sherpa.OnlineRecognizer = _FakeRecognizer
    monkeypatch.setitem(sys.modules, "sherpa_onnx", sherpa)
    return types.SimpleNamespace(folder=folder, seen=seen, path=tmp_path)


def test_model_loads_once_from_the_pinned_snapshot_and_releases(model_files):
    model = StreamingModel()
    assert model.downloaded() and not model.loaded

    model.load()
    model.load()

    assert len(_FakeRecognizer.created) == 1
    created = _FakeRecognizer.created[0]
    assert created["encoder"] == str(model_files.path / "encoder.int8.onnx")
    assert created["tokens"] == str(model_files.path / "tokens.txt")
    assert (created["num_threads"], created["provider"]) == (streaming_model.THREADS, "cpu")
    assert model_files.seen[0] == (
        streaming_model.MODEL_REPO,
        streaming_model.MODEL_REVISION,
        streaming_model.MODEL_FILES,
    )
    assert model.loaded
    assert model.release() is True and not model.loaded
    assert model.release() is False


def test_model_that_is_not_downloaded_cannot_load(model_files):
    model_files.folder["path"] = None
    model = StreamingModel()

    assert not model.downloaded()
    with pytest.raises(RuntimeError, match="not downloaded"):
        model.open_session("de")


@pytest.mark.parametrize(
    "language,expected",
    [("de", "de"), ("DE", "de"), ("en-US", "en"), (None, "auto"), ("auto", "auto"), ("pl", "auto")],
)
def test_session_passes_only_languages_the_model_knows(model_files, language, expected):
    session = StreamingModel().open_session(language)
    assert session._stream.options == {"language": expected}


def test_session_feeds_16_khz_audio_and_decodes_what_is_ready(model_files):
    model = StreamingModel()
    session = model.open_session("de")
    recognizer = model._recognizer
    recognizer.pending = 2

    session.feed(np.zeros(8000, dtype=np.float32))

    assert session._stream.waveforms == [(16000, 8000)]
    assert recognizer.decoded == 2 and session.text() == "2 Abschnitte"


def test_download_requests_the_pinned_files(monkeypatch, model_files):
    calls = []
    monkeypatch.setattr(
        streaming_model,
        "download_with_progress",
        lambda repo, on_progress, **kwargs: calls.append((repo, kwargs)),
    )

    assert StreamingModel().download(lambda done, total: None) is True

    repo, kwargs = calls[0]
    assert repo == streaming_model.MODEL_REPO
    assert kwargs == {
        "allow_patterns": streaming_model.MODEL_FILES,
        "required_files": streaming_model.MODEL_FILES,
        "revision": streaming_model.MODEL_REVISION,
    }


def test_installed_reflects_the_optional_dependency(monkeypatch):
    monkeypatch.setattr(streaming_model.importlib.util, "find_spec", lambda name: None)
    assert StreamingModel.installed() is False
    monkeypatch.setattr(streaming_model.importlib.util, "find_spec", lambda name: object())
    assert StreamingModel.installed() is True
