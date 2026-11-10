"""Streaming speech model for the live preview (Nemotron 3.5 via sherpa-onnx, CPU).

The model shows words about 0.6 s after they are spoken. It only feeds the
overlay; the transcript still comes from the transcription provider.

Usage:
    model = StreamingModel()
    if model.installed() and model.downloaded():
        model.load()
        session = model.open_session("de")
        session.feed(samples)          # float32 mono at 16 kHz, in any chunking
        print(session.text())
    model.release()                    # frees about 1.9 GB
"""

from __future__ import annotations

import importlib.util
import logging
import re
import threading
from pathlib import Path
from typing import Any

from config import WHISPER_SAMPLE_RATE
from utils.model_download import ProgressCallback, cached_snapshot, download_with_progress

logger = logging.getLogger("pulsescribe.providers.streaming_model")

MODEL_LABEL = "live preview model"
# sherpa-onnx export (560 ms chunks, int8) of nvidia/nemotron-3.5-asr-streaming-0.6b.
# The commit is pinned: the files come from a third-party account.
MODEL_REPO = "csukuangfj2/sherpa-onnx-nemotron-3.5-asr-streaming-0.6b-560ms-int8-2026-06-11"
MODEL_REVISION = "ab43d895f5985b1bbab8b6eac8607fcdc05343f3"
MODEL_FILES = ("encoder.int8.onnx", "decoder.int8.onnx", "joiner.int8.onnx", "tokens.txt")
# Measured on an M5: one 560 ms chunk takes 107 ms with 1 thread, 74 ms with 2
# and 61 ms with 4; with 2 threads speech uses 0.29 CPU cores.
THREADS = 2
_LANGUAGE_TAG = re.compile(r"^<([a-z]{2})-[A-Z]{2}> ", re.MULTILINE)


class StreamingSession:
    """One recording's stream: ``feed`` audio as it arrives, read ``text`` any time."""

    def __init__(self, recognizer: Any, language: str) -> None:
        self._recognizer = recognizer
        self._stream = recognizer.create_stream()
        self._stream.set_option("language", language)

    def feed(self, samples: Any) -> None:
        """Add float32 mono samples at 16 kHz and decode what is complete."""
        self._stream.accept_waveform(WHISPER_SAMPLE_RATE, samples)
        while self._recognizer.is_ready(self._stream):
            self._recognizer.decode_stream(self._stream)

    def text(self) -> str:
        """Everything recognized so far."""
        return self._recognizer.get_result(self._stream)


class StreamingModel:
    """Download, loading and release of the streaming model; thread-safe.

    ``installed`` tells whether sherpa-onnx is there at all (it is optional),
    ``downloaded`` whether the model files are in the Hugging Face cache.
    ``load`` needs both and keeps the model in memory until ``release``.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._recognizer: Any = None
        self._languages: frozenset[str] = frozenset()

    @staticmethod
    def installed() -> bool:
        return importlib.util.find_spec("sherpa_onnx") is not None

    @staticmethod
    def _folder() -> Path | None:
        return cached_snapshot(MODEL_REPO, revision=MODEL_REVISION, required_files=MODEL_FILES)

    def downloaded(self) -> bool:
        return self._folder() is not None

    def download(self, on_progress: ProgressCallback) -> bool:
        """Download the model (682 MB) unless it is cached; returns ``downloaded()``."""
        download_with_progress(
            MODEL_REPO,
            on_progress,
            allow_patterns=MODEL_FILES,
            required_files=MODEL_FILES,
            revision=MODEL_REVISION,
        )
        return self.downloaded()

    @property
    def loaded(self) -> bool:
        return self._recognizer is not None

    def load(self) -> None:
        """Load the model (about 0.5 s) unless it is loaded; raises if it cannot."""
        with self._lock:
            if self._recognizer is not None:
                return
            folder = self._folder()
            if folder is None:
                raise RuntimeError(f"{MODEL_LABEL} is not downloaded")
            import sherpa_onnx

            self._recognizer = sherpa_onnx.OnlineRecognizer.from_transducer(
                tokens=str(folder / "tokens.txt"),
                encoder=str(folder / "encoder.int8.onnx"),
                decoder=str(folder / "decoder.int8.onnx"),
                joiner=str(folder / "joiner.int8.onnx"),
                num_threads=THREADS,
                provider="cpu",
            )
            tokens = (folder / "tokens.txt").read_text(encoding="utf-8")
            self._languages = frozenset(_LANGUAGE_TAG.findall(tokens))
            logger.info(f"Live-Vorschau-Modell geladen ({len(self._languages)} Sprachen)")

    def release(self) -> bool:
        """Drop the loaded model; returns whether there was one."""
        with self._lock:
            had_model = self._recognizer is not None
            self._recognizer = None
            return had_model

    def open_session(self, language: str | None) -> StreamingSession:
        """Start the stream for one recording; loads the model first if needed.

        ``language`` is a code such as ``de``; anything the model does not know
        (and ``None`` or ``auto``) lets it detect the language.
        """
        self.load()
        code = (language or "").strip().lower()[:2]
        with self._lock:
            recognizer = self._recognizer
            known = code in self._languages
        if recognizer is None:  # released in between
            raise RuntimeError(f"{MODEL_LABEL} was released")
        return StreamingSession(recognizer, code if known else "auto")


__all__ = ["MODEL_LABEL", "StreamingModel", "StreamingSession"]
