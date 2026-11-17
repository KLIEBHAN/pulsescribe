"""NVIDIA's Parakeet v3 and Canary-1B-v2 as local models.

On Apple Silicon with mlx-audio installed they run on the GPU through MLX,
elsewhere on the CPU through sherpa-onnx. Both cover 25 European languages and
write punctuation. Parakeet detects the language itself; Canary needs it and
translates speech of another language, so ``auto`` uses the system language
(see ``canary_language``).

Usage:
    models = NemoModels()
    models.download("parakeet-v3", on_progress)       # once, into the Hugging Face cache
    text = models.transcribe("parakeet-v3", samples, "de")   # float32 mono 16 kHz or a path
    models.release()                                  # frees the loaded model
"""

from __future__ import annotations

import importlib.util
import locale
import logging
import sys
import threading
import types
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from config import WHISPER_SAMPLE_RATE
from utils.local_backend import nemo_runs_on_gpu as mlx_available
from utils.model_download import ProgressCallback, cached_snapshot, download_with_progress

from ._language import is_auto_language

logger = logging.getLogger("pulsescribe.providers.nemo_model")

# sherpa-onnx threads. Measured on an M5 (4 performance cores) with 11 s of German
# speech: Parakeet takes 0.33 s with 2 threads, 0.22 s with 4 and 0.24 s with 6 or 8;
# Canary 1.32 s, 1.09 s and 1.12 s.
THREADS = 4
# Canary returns no text for recordings longer than about 37 s (it was trained on
# pieces of at most 40 s) and Parakeet drops words on long ones, so longer audio is
# decoded in pieces of at most 15 s, each cut at the quietest 100 ms of its last 5 s.
# German, 306 s: WER Parakeet 29 % whole, 0.1 % in pieces; Canary 100 % (no text),
# 2.0 %. 15 s beat 10, 20 and 30 s in accuracy, and Canary is fastest with it.
SEGMENT_SECONDS = 15
_CUT_SEARCH_SECONDS = 5
# The languages Canary-1B-v2 was trained on; its vocabulary names many more.
CANARY_LANGUAGES = frozenset({
    "bg", "cs", "da", "de", "el", "en", "es", "et", "fi", "fr", "hr", "hu", "it",
    "lt", "lv", "mt", "nl", "pl", "pt", "ro", "ru", "sk", "sl", "sv", "uk",
})


@dataclass(frozen=True)
class Weights:
    """Files of one model for one engine; commits are pinned (third-party accounts)."""

    repo: str
    revision: str
    files: tuple[str, ...]


@dataclass(frozen=True)
class NemoModel:
    onnx: Weights
    mlx: Weights
    canary: bool = False


NEMO_MODEL_FILES = {
    "parakeet-v3": NemoModel(
        # int8 export by the sherpa-onnx author; 670 MB, about 2.1 GB of memory loaded.
        onnx=Weights(
            repo="csukuangfj/sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8",
            revision="ed5d745623472f97cfc2e6c2916ee248798fc5a8",
            files=("encoder.int8.onnx", "decoder.int8.onnx", "joiner.int8.onnx", "tokens.txt"),
        ),
        # 8-bit; 744 MB, about 1.1 GB of memory loaded. Same text as the fp32 weights
        # (2.5 GB) on our German, English and French clips, 0.055 s instead of 0.045 s
        # for 7 s of speech on an M5.
        mlx=Weights(
            repo="transcriber-app/parakeet-tdt-0.6b-v3-mlx-8bit",
            revision="b56210e2fad422736810524963c24b5e2a1985ab",
            files=("config.json", "model.safetensors", "tokenizer.model", "tokenizer.vocab", "vocab.txt"),
        ),
    ),
    "canary-1b-v2": NemoModel(
        # int8 export with a quantized decoder: same text as the export with an fp32
        # decoder in our tests, 40 % faster and 0.8 GB less memory. 1.0 GB, about
        # 2.8 GB of memory loaded. The repo's encoder.int8.onnx.data is not needed.
        onnx=Weights(
            repo="YaroslavGor/sherpa-onnx-nemo-canary-1b-v2-int",
            revision="8e135081466c7cfe955d1b858544e4bdeeffeb9e",
            files=("encoder.int8.onnx", "decoder.int8.onnx", "tokens.txt"),
        ),
        # 8-bit with the tokenizer inside config.json; 1.1 GB, about 1.5 GB of memory
        # loaded. Same text as the fp32 weights, 3.5 times faster (M6).
        mlx=Weights(
            repo="Mediform/canary-1b-v2-mlx-q8",
            revision="0b6b32ee10f30c89e3ead7249bb636445e3019ee",
            files=("config.json", "model.safetensors"),
        ),
        canary=True,
    ),
}


def _mlx_audio_load():
    """mlx-audio's speech-to-text loader, without importing every model it knows.

    ``mlx_audio.stt.models`` imports all of its architectures on import, some of
    them needing transformers, which the app does not carry. A bare package
    module in its place lets the loader import just the model it loads.
    """
    if "mlx_audio.stt.models" not in sys.modules:
        stt = importlib.util.find_spec("mlx_audio.stt")
        if stt is None or not stt.submodule_search_locations:
            raise ImportError("mlx-audio is not installed")
        package = types.ModuleType("mlx_audio.stt.models")
        package.__path__ = [str(Path(next(iter(stt.submodule_search_locations))) / "models")]
        sys.modules["mlx_audio.stt.models"] = package
    from mlx_audio.stt.utils import load

    return load


def _system_language() -> str | None:
    """Two-letter code of the user's preferred language, None if unknown."""
    if sys.platform == "darwin":
        try:
            from Foundation import NSLocale  # type: ignore[import-not-found]

            preferred = NSLocale.preferredLanguages()
            if preferred:
                return str(preferred[0])[:2].lower()
        except Exception:
            logger.debug("Systemsprache nicht lesbar", exc_info=True)
    code = locale.getlocale()[0]
    return code[:2].lower() if code else None


def canary_language(language: str | None) -> str:
    """Language Canary transcribes for a ``language`` setting.

    ``auto`` and languages Canary does not know use the system language if
    Canary knows it, else English.
    """
    code = "" if is_auto_language(language) else (language or "").strip().lower()[:2]
    if code in CANARY_LANGUAGES:
        return code
    system = _system_language()
    chosen = system if system in CANARY_LANGUAGES else "en"
    if code:
        logger.warning(f"Canary kennt die Sprache '{code}' nicht, nutze '{chosen}'")
    return chosen


def _read_audio_file(path: str) -> tuple[Any, int]:
    """Mono float32 samples and their rate; formats libsndfile lacks (M4A, MP4) via PyAV."""
    import soundfile as sf

    try:
        samples, sample_rate = sf.read(path, dtype="float32", always_2d=True)
    except Exception as error:
        try:
            from faster_whisper.audio import decode_audio  # PyAV, bundled with faster-whisper
        except ImportError:
            raise error from None
        return decode_audio(path, sampling_rate=WHISPER_SAMPLE_RATE), WHISPER_SAMPLE_RATE
    return samples.mean(axis=1), sample_rate


def segments(samples: Any, sample_rate: int) -> list[Any]:
    """``samples`` cut into pieces of at most SEGMENT_SECONDS, each ending in its quietest 100 ms."""
    import numpy as np

    limit = SEGMENT_SECONDS * sample_rate
    search = _CUT_SEARCH_SECONDS * sample_rate
    frame = sample_rate // 10
    pieces, start = [], 0
    while len(samples) - start > limit:
        window = np.asarray(samples[start + limit - search:start + limit], dtype=np.float32)
        energy = np.square(window[: len(window) // frame * frame]).reshape(-1, frame).mean(axis=1)
        cut = start + limit - search + int(np.argmin(energy)) * frame + frame // 2
        pieces.append(samples[start:cut])
        start = cut
    pieces.append(samples[start:])
    return pieces


def _load_onnx(folder: Path, canary: bool) -> Any:
    """sherpa-onnx recognizer whose ``transcribe(samples, rate, language)`` decodes one piece."""
    import sherpa_onnx

    if canary:
        recognizer = sherpa_onnx.OfflineRecognizer.from_nemo_canary(
            encoder=str(folder / "encoder.int8.onnx"),
            decoder=str(folder / "decoder.int8.onnx"),
            tokens=str(folder / "tokens.txt"),
            num_threads=THREADS,
            provider="cpu",
        )
    else:
        recognizer = sherpa_onnx.OfflineRecognizer.from_transducer(
            encoder=str(folder / "encoder.int8.onnx"),
            decoder=str(folder / "decoder.int8.onnx"),
            joiner=str(folder / "joiner.int8.onnx"),
            tokens=str(folder / "tokens.txt"),
            model_type="nemo_transducer",
            num_threads=THREADS,
            provider="cpu",
        )

    def transcribe(samples: Any, sample_rate: int, language: str | None) -> str:
        stream = recognizer.create_stream()
        if language:
            stream.set_option("src_lang", language)
            stream.set_option("tgt_lang", language)
        stream.accept_waveform(sample_rate, samples)
        recognizer.decode_stream(stream)
        return stream.result.text

    return transcribe


def _load_mlx(folder: Path, canary: bool) -> Any:
    """MLX model whose ``transcribe(samples, rate, language)`` decodes one piece at 16 kHz."""
    import mlx.core as mx

    model = _mlx_audio_load()(folder, lazy=True, model_type="canary" if canary else "parakeet")
    mx.eval(model.parameters())

    def transcribe(samples: Any, sample_rate: int, language: str | None) -> str:
        if sample_rate != WHISPER_SAMPLE_RATE:
            from scipy.signal import resample_poly

            samples = resample_poly(samples, WHISPER_SAMPLE_RATE, sample_rate).astype("float32")
        options = {"source_lang": language, "target_lang": language} if language else {}
        return model.generate(mx.array(samples), **options).text

    return transcribe


class NemoModels:
    """Download, loading, transcription and release of the NeMo models; thread-safe.

    Keeps one model loaded: transcribing with the other one replaces it.
    ``engine`` is ``mlx`` (GPU) or ``onnx`` (CPU), fixed per process.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._name: str | None = None
        self._transcribe: Any = None
        self.engine = "mlx" if mlx_available() else "onnx"

    def _weights(self, name: str) -> Weights:
        model = NEMO_MODEL_FILES[name]
        return model.mlx if self.engine == "mlx" else model.onnx

    def _folder(self, name: str) -> Path | None:
        weights = self._weights(name)
        return cached_snapshot(weights.repo, revision=weights.revision, required_files=weights.files)

    def download(self, name: str, on_progress: ProgressCallback | None) -> None:
        """Download model ``name`` unless it is cached; reports progress if a callback is set."""
        weights = self._weights(name)
        download_with_progress(
            weights.repo,
            on_progress or (lambda _done, _total: None),
            allow_patterns=weights.files,
            required_files=weights.files,
            revision=weights.revision,
        )

    def load(self, name: str) -> None:
        """Load model ``name`` (about 1 s), downloading it first if needed; raises if it cannot."""
        with self._lock:
            if self._name == name:
                return
            folder = self._folder(name)
            if folder is None:
                self.download(name, None)
                folder = self._folder(name)
            if folder is None:
                raise RuntimeError(f"Modell {name} konnte nicht heruntergeladen werden")
            self.release()
            loader = _load_mlx if self.engine == "mlx" else _load_onnx
            self._transcribe = loader(folder, NEMO_MODEL_FILES[name].canary)
            self._name = name
            logger.info(f"NeMo-Modell {name} geladen ({self.engine})")

    def release(self) -> bool:
        """Drop the loaded model; returns whether there was one."""
        with self._lock:
            had_model = self._transcribe is not None
            self._transcribe = None
            self._name = None
            return had_model

    def transcribe(self, name: str, audio: Any, language: str | None) -> str:
        """Transcribe ``audio`` (float32 mono at 16 kHz, or a file path) with model ``name``."""
        if isinstance(audio, (str, Path)):
            samples, sample_rate = _read_audio_file(str(audio))
        else:
            samples, sample_rate = audio, WHISPER_SAMPLE_RATE
        with self._lock:
            self.load(name)
            code = canary_language(language) if NEMO_MODEL_FILES[name].canary else None
            texts = [
                self._transcribe(piece, sample_rate, code).strip()
                for piece in segments(samples, sample_rate)
            ]
            return " ".join(text for text in texts if text)


__all__ = ["CANARY_LANGUAGES", "NEMO_MODEL_FILES", "NemoModels", "canary_language", "mlx_available"]
