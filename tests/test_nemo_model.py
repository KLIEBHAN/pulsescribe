"""Tests for the NeMo models Parakeet v3 and Canary-1B-v2 (providers/nemo_model.py)."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np
import pytest

import providers.nemo_model as nemo_mod
from providers.local import LocalProvider
from providers.nemo_model import NEMO_MODEL_FILES, NemoModels, canary_language
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
    assert canary_language(language, frozenset({"en", "de", "fr"})) == expected


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

    spec = NEMO_MODEL_FILES["parakeet-v3"]
    assert download.call_args.args[0] == spec.repo
    assert download.call_args.kwargs["revision"] == spec.revision
    assert sherpa == []


def test_local_provider_routes_nemo_models_past_whisper(monkeypatch, sherpa):
    monkeypatch.setenv("PULSESCRIBE_LOCAL_MODEL", "parakeet-v3")
    provider = LocalProvider()
    monkeypatch.setattr(
        provider, "_ensure_runtime_config", MagicMock(side_effect=AssertionError("Whisper"))
    )
    monkeypatch.setattr(provider, "_build_options", MagicMock(side_effect=AssertionError))
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
