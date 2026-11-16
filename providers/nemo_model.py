"""NVIDIA's Parakeet v3 and Canary-1B-v2 as local models (sherpa-onnx, CPU).

Both cover 25 European languages and write punctuation. Parakeet detects the
language itself; Canary needs it and translates speech of another language,
so ``auto`` uses the system language (see ``canary_language``).

Usage:
    models = NemoModels()
    models.download("parakeet-v3", on_progress)       # once, into the Hugging Face cache
    text = models.transcribe("parakeet-v3", samples, "de")   # float32 mono 16 kHz or a path
    models.release()                                  # frees the loaded model
"""

from __future__ import annotations

import locale
import logging
import re
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from config import WHISPER_SAMPLE_RATE
from utils.model_download import ProgressCallback, cached_snapshot, download_with_progress

from ._language import is_auto_language

logger = logging.getLogger("pulsescribe.providers.nemo_model")

# Measured on an M5 (4 performance cores) with 11 s of German speech: Parakeet
# takes 0.33 s with 2 threads, 0.22 s with 4 and 0.24 s with 6 or 8; Canary
# 1.32 s, 1.09 s and 1.12 s.
THREADS = 4
_CANARY_LANGUAGE = re.compile(r"^<\|([a-z]{2})\|> ", re.MULTILINE)


@dataclass(frozen=True)
class NemoModel:
    """sherpa-onnx export of one model; commits are pinned (third-party accounts)."""

    repo: str
    revision: str
    files: tuple[str, ...]
    canary: bool = False


NEMO_MODEL_FILES = {
    # int8 export by the sherpa-onnx author; 670 MB, about 2.1 GB of memory loaded.
    "parakeet-v3": NemoModel(
        repo="csukuangfj/sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8",
        revision="ed5d745623472f97cfc2e6c2916ee248798fc5a8",
        files=("encoder.int8.onnx", "decoder.int8.onnx", "joiner.int8.onnx", "tokens.txt"),
    ),
    # int8 export with a quantized decoder: same text as the export with an fp32
    # decoder in our tests, 40 % faster and 0.8 GB less memory. 1.0 GB, about
    # 2.8 GB of memory loaded. The repo's encoder.int8.onnx.data is not needed.
    "canary-1b-v2": NemoModel(
        repo="YaroslavGor/sherpa-onnx-nemo-canary-1b-v2-int",
        revision="8e135081466c7cfe955d1b858544e4bdeeffeb9e",
        files=("encoder.int8.onnx", "decoder.int8.onnx", "tokens.txt"),
        canary=True,
    ),
}


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


def canary_language(language: str | None, offered: frozenset[str]) -> str:
    """Language Canary transcribes for a ``language`` setting.

    ``auto`` and languages Canary does not offer use the system language if
    Canary offers it, else English.
    """
    code = "" if is_auto_language(language) else (language or "").strip().lower()[:2]
    if code in offered:
        return code
    system = _system_language()
    chosen = system if system in offered else "en"
    if code:
        logger.warning(f"Canary kennt die Sprache '{code}' nicht, nutze '{chosen}'")
    return chosen


class NemoModels:
    """Download, loading, transcription and release of the NeMo models; thread-safe.

    Keeps one model loaded: transcribing with the other one replaces it.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._name: str | None = None
        self._recognizer: Any = None
        self._languages: frozenset[str] = frozenset()

    @staticmethod
    def _folder(name: str) -> Path | None:
        spec = NEMO_MODEL_FILES[name]
        return cached_snapshot(spec.repo, revision=spec.revision, required_files=spec.files)

    @staticmethod
    def download(name: str, on_progress: ProgressCallback | None) -> None:
        """Download model ``name`` unless it is cached; reports progress if a callback is set."""
        spec = NEMO_MODEL_FILES[name]
        download_with_progress(
            spec.repo,
            on_progress or (lambda _done, _total: None),
            allow_patterns=spec.files,
            required_files=spec.files,
            revision=spec.revision,
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
            import sherpa_onnx

            spec = NEMO_MODEL_FILES[name]
            if spec.canary:
                tokens = folder / "tokens.txt"
                self._recognizer = sherpa_onnx.OfflineRecognizer.from_nemo_canary(
                    encoder=str(folder / "encoder.int8.onnx"),
                    decoder=str(folder / "decoder.int8.onnx"),
                    tokens=str(tokens),
                    num_threads=THREADS,
                    provider="cpu",
                )
                self._languages = frozenset(
                    _CANARY_LANGUAGE.findall(tokens.read_text(encoding="utf-8"))
                )
            else:
                self._recognizer = sherpa_onnx.OfflineRecognizer.from_transducer(
                    encoder=str(folder / "encoder.int8.onnx"),
                    decoder=str(folder / "decoder.int8.onnx"),
                    joiner=str(folder / "joiner.int8.onnx"),
                    tokens=str(folder / "tokens.txt"),
                    model_type="nemo_transducer",
                    num_threads=THREADS,
                    provider="cpu",
                )
            self._name = name
            logger.info(f"NeMo-Modell {name} geladen")

    def release(self) -> bool:
        """Drop the loaded model; returns whether there was one."""
        with self._lock:
            had_model = self._recognizer is not None
            self._recognizer = None
            self._name = None
            self._languages = frozenset()
            return had_model

    def transcribe(self, name: str, audio: Any, language: str | None) -> str:
        """Transcribe ``audio`` (float32 mono at 16 kHz, or a file path) with model ``name``."""
        if isinstance(audio, (str, Path)):
            import soundfile as sf

            samples, sample_rate = sf.read(str(audio), dtype="float32", always_2d=True)
            samples = samples.mean(axis=1)
        else:
            samples, sample_rate = audio, WHISPER_SAMPLE_RATE
        with self._lock:
            self.load(name)
            stream = self._recognizer.create_stream()
            if NEMO_MODEL_FILES[name].canary:
                code = canary_language(language, self._languages)
                stream.set_option("src_lang", code)
                stream.set_option("tgt_lang", code)
            stream.accept_waveform(sample_rate, samples)
            self._recognizer.decode_stream(stream)
            return stream.result.text.strip()


__all__ = ["NEMO_MODEL_FILES", "NemoModels", "canary_language"]
