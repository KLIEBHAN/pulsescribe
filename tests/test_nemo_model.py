"""Tests for the NeMo models Parakeet v3 and Canary-1B-v2 (providers/nemo_model.py)."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np
import pytest

import providers.nemo_model as nemo_mod
from providers.local import LocalProvider
from providers.nemo_model import NEMO_MODEL_FILES, NemoModels, canary_language, segments
from utils.local_backend import NEMO_MODELS, effective_local_backend, is_nemo_model


class FakeStream:
    def __init__(self) -> None:
        self.options: dict[str, str] = {}
        self.waveform = None
        self.result = SimpleNamespace(text="")

    def set_option(self, key: str, value: str) -> None:
        self.options[key] = value

    def accept_waveform(self, sample_rate: int, samples) -> None:
        self.waveform = (sample_rate, samples)


class FakeRecognizer:
    def __init__(self, kind: str, **config) -> None:
        self.kind, self.config = kind, config
        self.streams: list[FakeStream] = []

    def create_stream(self) -> FakeStream:
        self.streams.append(FakeStream())
        return self.streams[-1]

    def decode_stream(self, stream: FakeStream) -> None:
        stream.result.text = f" {self.kind} text "


@pytest.fixture
def sherpa(monkeypatch, tmp_path):
    """Fake sherpa-onnx with every model cached in tmp_path."""
    (tmp_path / "tokens.txt").write_text("<|en|> 1\n<|de|> 2\n<|pnc|> 3\n", encoding="utf-8")
    created: list[FakeRecognizer] = []

    def factory(kind):
        def create(**config):
            created.append(FakeRecognizer(kind, **config))
            return created[-1]
        return create

    offline = SimpleNamespace(
        from_transducer=factory("transducer"), from_nemo_canary=factory("canary")
    )
    monkeypatch.setitem(__import__("sys").modules, "sherpa_onnx",
                        SimpleNamespace(OfflineRecognizer=offline))
    monkeypatch.setattr(nemo_mod, "cached_snapshot", lambda *a, **k: tmp_path)
    monkeypatch.setattr(nemo_mod, "_system_language", lambda: "de")
    monkeypatch.setattr(nemo_mod, "mlx_available", lambda: False)
    return created


def test_model_names_match_their_files():
    assert set(NEMO_MODELS) == set(NEMO_MODEL_FILES)


def test_nemo_models_run_on_nemo_whatever_backend_is_selected():
    assert is_nemo_model(" Parakeet-V3 ")
    assert not is_nemo_model("turbo") and not is_nemo_model(None)
    assert effective_local_backend("mlx", model="canary-1b-v2") == "nemo"
    assert effective_local_backend("faster", model="turbo") == "faster"


@pytest.mark.parametrize(
    ("language", "system", "expected"),
    [
        ("de", "fr", "de"),  # offered: used as set
        ("auto", "de", "de"),  # auto: the system language
        (None, "zh", "en"),  # system language not offered: English
        ("zh", "de", "de"),  # setting not offered: the system language
    ],
)
def test_canary_language(monkeypatch, language, system, expected):
    monkeypatch.setattr(nemo_mod, "_system_language", lambda: system)
    assert canary_language(language) == expected


def test_parakeet_transcribes_without_a_language(sherpa):
    models = NemoModels()
    samples = np.zeros(1600, dtype=np.float32)

    assert models.transcribe("parakeet-v3", samples, "de") == "transducer text"

    recognizer = sherpa[0]
    assert recognizer.config["model_type"] == "nemo_transducer"
    assert recognizer.streams[0].options == {}
    assert recognizer.streams[0].waveform[0] == 16000


def test_canary_transcribes_in_the_resolved_language(sherpa):
    models = NemoModels()

    assert models.transcribe("canary-1b-v2", np.zeros(1600, dtype=np.float32), "auto") \
        == "canary text"

    assert sherpa[0].streams[0].options == {"src_lang": "de", "tgt_lang": "de"}


def test_long_audio_is_cut_at_the_quietest_moment():
    rate = 1000
    audio = np.ones(40 * rate, dtype=np.float32)
    audio[12_300:12_400] = 0  # a pause 12.3 s in, inside the last 5 s of the first 15 s

    pieces = segments(audio, rate)

    assert [len(p) for p in pieces[:1]] == [12_350]  # cut in the middle of the pause
    assert all(len(p) <= 15 * rate for p in pieces)
    assert sum(len(p) for p in pieces) == len(audio)
    short = np.ones(15 * rate, dtype=np.float32)
    assert [len(p) for p in segments(short, rate)] == [15 * rate]


def test_canary_transcribes_long_audio_piece_by_piece(sherpa):
    audio = np.zeros(40 * 16000, dtype=np.float32)

    text = NemoModels().transcribe("canary-1b-v2", audio, "de")

    streams = sherpa[0].streams
    assert len(streams) == len(segments(audio, 16000)) >= 3
    assert all(s.options == {"src_lang": "de", "tgt_lang": "de"} for s in streams)
    assert sum(len(s.waveform[1]) for s in streams) == len(audio)
    assert text == " ".join(["canary text"] * len(streams))


def test_switching_models_replaces_the_loaded_one(sherpa):
    models = NemoModels()
    audio = np.zeros(1600, dtype=np.float32)
    models.transcribe("parakeet-v3", audio, None)
    models.transcribe("parakeet-v3", audio, None)
    models.transcribe("canary-1b-v2", audio, None)

    assert [r.kind for r in sherpa] == ["transducer", "canary"]
    assert models.release() is True
    assert models.release() is False


def test_load_downloads_a_missing_model_and_fails_clearly(monkeypatch, sherpa):
    download = MagicMock()
    monkeypatch.setattr(nemo_mod, "download_with_progress", download)
    monkeypatch.setattr(nemo_mod, "cached_snapshot", lambda *a, **k: None)

    with pytest.raises(RuntimeError, match="parakeet-v3"):
        NemoModels().load("parakeet-v3")

    spec = NEMO_MODEL_FILES["parakeet-v3"].onnx
    assert download.call_args.args[0] == spec.repo
    assert download.call_args.kwargs["revision"] == spec.revision
    assert sherpa == []


def test_files_libsndfile_cannot_read_are_decoded_with_pyav(monkeypatch, sherpa):
    import sys

    def unreadable(*_args, **_kwargs):
        raise RuntimeError("Format not recognised")

    decoded = np.ones(800, dtype=np.float32)
    monkeypatch.setattr("soundfile.read", unreadable)
    monkeypatch.setitem(sys.modules, "faster_whisper.audio",
                        SimpleNamespace(decode_audio=lambda path, sampling_rate: decoded))

    NemoModels().transcribe("parakeet-v3", "/recordings/memo.m4a", None)

    rate, samples = sherpa[0].streams[0].waveform
    assert rate == 16000 and np.array_equal(samples, decoded)


def test_local_provider_routes_nemo_models_past_whisper(monkeypatch, sherpa):
    monkeypatch.setenv("PULSESCRIBE_LOCAL_MODEL", "parakeet-v3")
    provider = LocalProvider()
    monkeypatch.setattr(
        provider, "_ensure_runtime_config", MagicMock(side_effect=AssertionError("Whisper"))
    )
    monkeypatch.setattr(provider, "_build_options", MagicMock(side_effect=AssertionError))
    # torch.mps.empty_cache() crashes on CI runners without a GPU
    monkeypatch.setattr(LocalProvider, "_release_backend_allocator_caches",
                        staticmethod(lambda: None))
    progress = MagicMock()
    provider.download_progress_callback = progress
    download = MagicMock()
    monkeypatch.setattr(nemo_mod, "download_with_progress", download)

    provider.preload()
    provider.keepalive()
    text = provider.transcribe_audio(np.zeros(1600, dtype=np.float32), language="de")

    assert text == "transducer text"
    assert download.call_args.args[1] is progress
    assert provider.get_runtime_info() == {"backend": "nemo", "device": "cpu"}
    assert provider.clear_model_cache() == 1


@pytest.fixture
def mlx(monkeypatch, tmp_path):
    """Fake MLX and mlx-audio loader on Apple Silicon, every model cached in tmp_path."""
    import sys

    loaded: list[dict] = []

    def load(folder, **options):
        model = MagicMock()
        model.generate.side_effect = lambda samples, **kw: SimpleNamespace(
            text=f" {options['model_type']} {kw.get('source_lang', '-')} ")
        loaded.append({"folder": folder, **options, "model": model})
        return model

    core = SimpleNamespace(array=lambda samples: samples, eval=lambda *_: None)
    monkeypatch.setitem(sys.modules, "mlx", SimpleNamespace(core=core))
    monkeypatch.setitem(sys.modules, "mlx.core", core)
    monkeypatch.setattr(nemo_mod, "mlx_available", lambda: True)
    monkeypatch.setattr(nemo_mod, "_mlx_audio_load", lambda: load)
    monkeypatch.setattr(nemo_mod, "cached_snapshot", lambda *a, **k: tmp_path)
    monkeypatch.setattr(nemo_mod, "_system_language", lambda: "de")
    return loaded


def test_apple_silicon_runs_the_models_on_mlx(mlx, monkeypatch):
    download = MagicMock()
    monkeypatch.setattr(nemo_mod, "download_with_progress", download)
    models = NemoModels()
    audio = np.zeros(1600, dtype=np.float32)

    assert models.engine == "mlx"
    models.download("canary-1b-v2", None)
    assert download.call_args.args[0] == NEMO_MODEL_FILES["canary-1b-v2"].mlx.repo
    assert models.transcribe("parakeet-v3", audio, "fr") == "parakeet -"
    assert models.transcribe("canary-1b-v2", audio, "auto") == "canary de"
    assert [entry["model_type"] for entry in mlx] == ["parakeet", "canary"]
    assert all(entry["lazy"] for entry in mlx)


def test_mlx_resamples_files_to_16_khz(mlx, tmp_path):
    import soundfile as sf

    path = tmp_path / "memo.wav"
    sf.write(path, np.zeros(4410, dtype=np.float32), 44100)

    NemoModels().transcribe("parakeet-v3", path, None)

    samples = mlx[0]["model"].generate.call_args.args[0]
    assert len(samples) == 1600


def test_mlx_audio_loads_parakeet_and_canary_without_transformers(monkeypatch):
    """The app carries no transformers; the loader must reach both models without it."""
    import importlib
    import sys

    pytest.importorskip("mlx_audio")
    for name in [n for n in sys.modules if n.startswith("mlx_audio.stt.models")]:
        monkeypatch.delitem(sys.modules, name)
    monkeypatch.setitem(sys.modules, "transformers", None)

    nemo_mod._mlx_audio_load()
    for name in ("parakeet", "canary"):
        assert importlib.import_module(f"mlx_audio.stt.models.{name}").Model


def test_local_provider_reports_the_gpu_for_mlx(mlx, monkeypatch):
    monkeypatch.setenv("PULSESCRIBE_LOCAL_MODEL", "canary-1b-v2")

    assert LocalProvider().get_runtime_info() == {"backend": "nemo", "device": "metal"}
