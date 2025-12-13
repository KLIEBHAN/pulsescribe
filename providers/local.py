"""Lokaler Whisper Provider.

Standardmäßig nutzt er openai-whisper (PyTorch). Optional kann über
`WHISPER_GO_LOCAL_BACKEND=faster` der deutlich schnellere faster-whisper
(CTranslate2) genutzt werden. Auf Apple Silicon kann optional auch
`WHISPER_GO_LOCAL_BACKEND=mlx` genutzt werden (mlx-whisper / Metal).
"""

import logging
import os
import sys
import threading
from pathlib import Path

from config import DEFAULT_LOCAL_MODEL, WHISPER_SAMPLE_RATE
from utils.env import get_env_bool, get_env_int
from utils.logging import log
from utils.vocabulary import load_vocabulary

logger = logging.getLogger("whisper_go.providers.local")


def _mlx_whisper_import_hint() -> str:
    """Hinweistext für fehlgeschlagene mlx-whisper Imports."""
    if getattr(sys, "frozen", False):
        return (
            "Du nutzt vermutlich die macOS-App. Bitte lade die neueste DMG neu herunter "
            "oder setze WHISPER_GO_LOCAL_BACKEND=whisper."
        )
    return "Installiere es mit `pip install mlx-whisper` oder setze WHISPER_GO_LOCAL_BACKEND=whisper."


def _import_mlx_whisper():
    """Importiert mlx-whisper mit hilfreicher Fehlermeldung bei Problemen."""
    try:
        import mlx_whisper  # type: ignore[import-not-found]
    except ModuleNotFoundError as e:
        missing = e.name or "unknown"
        raise ImportError(
            "mlx-whisper konnte nicht geladen werden (fehlende Abhängigkeit: "
            f"{missing}). {_mlx_whisper_import_hint()}"
        ) from e
    except ImportError as e:
        raise ImportError(
            f"mlx-whisper konnte nicht geladen werden. {_mlx_whisper_import_hint()} Ursache: {e}"
        ) from e
    return mlx_whisper

def _select_device() -> str:
    """Wählt ein sinnvolles Torch-Device für lokales Whisper.

    Priorität:
      1) WHISPER_GO_DEVICE Env-Override (z.B. "cpu", "mps", "cuda")
      2) Apple Silicon GPU via MPS
      3) CUDA (falls verfügbar)
      4) CPU
    """
    env_device = (os.getenv("WHISPER_GO_DEVICE") or "").strip().lower()
    if env_device and env_device != "auto":
        return env_device
    try:
        import torch

        if getattr(torch.backends, "mps", None) is not None:
            if torch.backends.mps.is_available() and torch.backends.mps.is_built():
                return "mps"
        if torch.cuda.is_available():
            return "cuda"
    except Exception as e:
        logger.debug(f"Device-Detection fehlgeschlagen, fallback CPU: {e}")
    return "cpu"


class LocalProvider:
    """Lokaler Whisper Provider.

    Nutzt openai-whisper für Offline-Transkription. Optional kann faster-whisper
    oder mlx-whisper genutzt werden (Backend-abhängig).

    Unterstützte Modelle:
        - tiny: 39M Parameter, ~1GB VRAM, sehr schnell
        - base: 74M Parameter, ~1GB VRAM, schnell
        - small: 244M Parameter, ~2GB VRAM, mittel
        - medium: 769M Parameter, ~5GB VRAM, langsam
        - large: 1550M Parameter, ~10GB VRAM, sehr langsam
        - turbo: 809M Parameter, ~6GB VRAM, schnell & gut (empfohlen)
    """

    name = "local"
    default_model = DEFAULT_LOCAL_MODEL

    def __init__(self) -> None:
        self._model_cache: dict = {}
        self._device: str | None = None
        self._fp16_override: bool | None = None
        self._fast_mode: bool | None = None
        self._backend: str | None = None
        self._compute_type: str | None = None
        self._load_lock = threading.Lock()
        self._transcribe_lock = threading.Lock()

    def invalidate_runtime_config(self) -> None:
        """Invalidiert ENV-basierte Runtime-Konfiguration ohne Model-Cache zu löschen.

        Wichtig: python-dotenv kann beim Reload Werte überschreiben, entfernt Keys
        aber nicht automatisch. Diese Methode erlaubt es dem Daemon, nach einem
        Settings-Reload die aktuellen ENV-Werte neu zu evaluieren, ohne teure
        Modelle erneut laden zu müssen.
        """
        self._backend = None
        self._device = None
        self._fp16_override = None
        self._fast_mode = None
        self._compute_type = None

    def _ensure_runtime_config(self) -> None:
        if self._backend is None:
            backend_env = (
                os.getenv("WHISPER_GO_LOCAL_BACKEND") or "whisper"
            ).strip().lower()
            if backend_env in {"faster", "faster-whisper"}:
                self._backend = "faster"
            elif backend_env in {"mlx", "mlx-whisper"}:
                self._backend = "mlx"
            elif backend_env == "auto":
                try:
                    import faster_whisper  # noqa: F401

                    self._backend = "faster"
                except Exception:
                    self._backend = "whisper"
            elif backend_env in {"whisper", "openai-whisper"}:
                self._backend = "whisper"
            else:
                logger.warning(
                    f"Unbekannter WHISPER_GO_LOCAL_BACKEND='{backend_env}', nutze whisper"
                )
                self._backend = "whisper"
            log(f"Lokales Whisper Backend: {self._backend}")

        if self._device is None:
            self._device = _select_device()
            log(f"Lokales Whisper Device: {self._device}")

        if self._fp16_override is None:
            self._fp16_override = get_env_bool("WHISPER_GO_FP16")

        if self._fast_mode is None:
            fast_env = get_env_bool("WHISPER_GO_LOCAL_FAST")
            if fast_env is None:
                # Default to fast decoding on faster-whisper unless user opts out.
                self._fast_mode = self._backend == "faster"
            else:
                self._fast_mode = fast_env

        if self._compute_type is None:
            compute_env = os.getenv("WHISPER_GO_LOCAL_COMPUTE_TYPE")
            if compute_env:
                self._compute_type = compute_env.strip()

    def _map_faster_model_name(self, model_name: str) -> str:
        """Mappt openai-whisper Namen auf faster-whisper Konventionen."""
        mapping = {
            "turbo": "large-v3-turbo",
            "large": "large-v3",
        }
        return mapping.get(model_name, model_name)

    def _map_mlx_model_name(self, model_name: str) -> str:
        """Mappt Modellnamen auf mlx-whisper HF-Repos (optional)."""
        if "/" in model_name:
            return model_name
        mapping = {
            "tiny": "mlx-community/whisper-tiny",
            # Manche MLX-Konvertierungen tragen das Suffix "-mlx" im Repo-Namen.
            "base": "mlx-community/whisper-base-mlx",
            "small": "mlx-community/whisper-small-mlx",
            "medium": "mlx-community/whisper-medium",
            "large": "mlx-community/whisper-large-v3-mlx",
            "turbo": "mlx-community/whisper-large-v3-turbo",
            "large-v3": "mlx-community/whisper-large-v3-mlx",
            "large-v2": "mlx-community/whisper-large-v2-mlx",
        }
        if model_name in mapping:
            return mapping[model_name]
        if model_name.startswith("whisper-"):
            return f"mlx-community/{model_name}"
        if model_name.startswith("large-v3"):
            return f"mlx-community/whisper-{model_name}"
        return model_name

    def _get_whisper_model(self, model_name: str):
        """Lädt openai-whisper Modell auf dem richtigen Device (mit Caching)."""
        import whisper

        self._ensure_runtime_config()

        cache_key = f"whisper:{model_name}:{self._device}"
        if cache_key in self._model_cache:
            return self._model_cache[cache_key]

        with self._load_lock:
            # Double-check nach Lock (Race vermeiden)
            if cache_key in self._model_cache:
                return self._model_cache[cache_key]

            log(f"Lade Modell '{model_name}' ({self._device})...")
            try:
                if self._device == "mps":
                    # MPS kann sparse alignment_heads nicht bewegen → CPU load,
                    # sparse Buffer temporär entfernen, dann Model auf MPS schieben.
                    cpu_model = whisper.load_model(model_name, device="cpu")
                    heads = None
                    if (
                        getattr(cpu_model, "alignment_heads", None) is not None
                        and getattr(cpu_model.alignment_heads, "is_sparse", False)
                    ):
                        heads = cpu_model.alignment_heads
                        cpu_model._buffers["alignment_heads"] = None
                    try:
                        cpu_model = cpu_model.to("mps")
                    finally:
                        if heads is not None:
                            cpu_model._buffers["alignment_heads"] = heads
                    self._model_cache[cache_key] = cpu_model
                else:
                    self._model_cache[cache_key] = whisper.load_model(
                        model_name, device=self._device
                    )
            except Exception as e:
                if self._device != "cpu":
                    logger.warning(
                        f"Whisper Load auf {self._device} fehlgeschlagen, fallback CPU: {e}"
                    )
                    self._device = "cpu"
                    cache_key = f"whisper:{model_name}:cpu"
                    if cache_key not in self._model_cache:
                        self._model_cache[cache_key] = whisper.load_model(
                            model_name, device="cpu"
                        )
                else:
                    raise

            return self._model_cache[cache_key]

    def _get_faster_model(self, model_name: str):
        """Lädt faster-whisper Modell (CTranslate2) mit Caching."""
        self._ensure_runtime_config()
        try:
            from faster_whisper import WhisperModel
        except ImportError as e:
            raise ImportError(
                "faster-whisper ist nicht installiert. Installiere es mit "
                "`pip install faster-whisper` oder setze WHISPER_GO_LOCAL_BACKEND=whisper."
            ) from e

        faster_name = self._map_faster_model_name(model_name)
        device = "cuda" if self._device == "cuda" else "cpu"
        compute_type = self._compute_type or ("float16" if device == "cuda" else "int8")

        cpu_threads = get_env_int("WHISPER_GO_LOCAL_CPU_THREADS") or 0
        num_workers = get_env_int("WHISPER_GO_LOCAL_NUM_WORKERS") or 1

        cache_key = f"faster:{faster_name}:{device}:{compute_type}:{cpu_threads}:{num_workers}"
        if cache_key in self._model_cache:
            return self._model_cache[cache_key]

        with self._load_lock:
            if cache_key in self._model_cache:
                return self._model_cache[cache_key]
            log(
                f"Lade faster-whisper Modell '{faster_name}' ({device}, {compute_type}, "
                f"threads={cpu_threads}, workers={num_workers})..."
            )
            self._model_cache[cache_key] = WhisperModel(
                faster_name,
                device=device,
                compute_type=compute_type,
                cpu_threads=cpu_threads,
                num_workers=num_workers,
            )
            return self._model_cache[cache_key]

    def _build_options(self, language: str | None) -> dict:
        """Baut Options inkl. Vocabulary und Speed-Overrides.

        Rückgabe ist kompatibel zu openai-whisper; für faster-whisper/mlx-whisper wird
        je nach Backend ein Subset genutzt.
        """
        self._ensure_runtime_config()

        options: dict = {}
        if language:
            options["language"] = language

        # Custom Vocabulary als initial_prompt für bessere Erkennung
        MAX_KEYWORDS = 50
        vocab = load_vocabulary()
        keywords = vocab.get("keywords", [])[:MAX_KEYWORDS]
        if keywords:
            options["initial_prompt"] = f"Fachbegriffe: {', '.join(keywords)}"
            logger.debug(f"Lokales Whisper mit {len(keywords)} Keywords")

        if self._backend == "whisper":
            # FP16: auf CPU nicht verfügbar; auf MPS derzeit oft instabil → default FP32.
            # Override via WHISPER_GO_FP16=true möglich.
            if self._device == "cpu":
                options["fp16"] = False
            elif self._device == "mps":
                options["fp16"] = (
                    self._fp16_override if self._fp16_override is not None else False
                )
            else:
                options["fp16"] = (
                    self._fp16_override if self._fp16_override is not None else True
                )
        elif self._backend == "faster":
            # faster-whisper: standardmäßig keine Timestamps berechnen (spart Zeit)
            wt_env = get_env_bool("WHISPER_GO_LOCAL_WITHOUT_TIMESTAMPS")
            options["without_timestamps"] = True if wt_env is None else wt_env

            vad_env = get_env_bool("WHISPER_GO_LOCAL_VAD_FILTER")
            if vad_env:
                options["vad_filter"] = True
        elif self._backend == "mlx":
            # mlx-whisper nutzt fp16 per Default; Override via WHISPER_GO_FP16 möglich.
            if self._fp16_override is not None:
                options["fp16"] = self._fp16_override

        # Fast-Mode: schnellere Decoding Defaults (kann via ENV überschrieben werden)
        if self._fast_mode:
            options.setdefault("temperature", 0.0)
            # mlx-whisper hat (Stand heute) keinen Beam-Search Decoder.
            # Deshalb darf beam_size nicht gesetzt werden, sonst crasht es mit
            # "Beam search decoder is not yet implemented".
            if self._backend != "mlx":
                options.setdefault("beam_size", 1)
                options.setdefault("best_of", 1)
            options.setdefault("condition_on_previous_text", False)

        # Explizite Decode-Overrides
        beam_size = get_env_int("WHISPER_GO_LOCAL_BEAM_SIZE")
        if beam_size is not None and self._backend == "mlx":
            logger.warning(
                "WHISPER_GO_LOCAL_BEAM_SIZE wird ignoriert (mlx backend unterstützt kein Beam Search)."
            )
        elif beam_size is not None:
            options["beam_size"] = beam_size

        best_of = get_env_int("WHISPER_GO_LOCAL_BEST_OF")
        if best_of is not None:
            options["best_of"] = best_of

        temp_env = os.getenv("WHISPER_GO_LOCAL_TEMPERATURE")
        if temp_env:
            try:
                if "," in temp_env:
                    options["temperature"] = tuple(
                        float(t.strip()) for t in temp_env.split(",") if t.strip()
                    )
                else:
                    options["temperature"] = float(temp_env.strip())
            except ValueError:
                logger.warning(f"Ungültiger WHISPER_GO_LOCAL_TEMPERATURE: {temp_env}")

        return options

    def _resolve_model_name(self, model: str | None) -> str:
        """Ermittelt Modellname aus Arg > WHISPER_GO_LOCAL_MODEL > Default."""
        if model:
            return model
        env_model = os.getenv("WHISPER_GO_LOCAL_MODEL")
        if env_model:
            return env_model.strip()
        return self.default_model

    def preload(self, model: str | None = None) -> None:
        """Lädt ein Modell vorab in den Cache."""
        model_name = self._resolve_model_name(model)
        self._ensure_runtime_config()
        if self._backend == "faster":
            self._get_faster_model(model_name)
        elif self._backend == "mlx":
            with self._transcribe_lock:
                mlx_whisper = _import_mlx_whisper()

                import numpy as np

                repo = self._map_mlx_model_name(model_name)
                warmup_s = 0.2
                warmup_audio = np.zeros(
                    int(WHISPER_SAMPLE_RATE * warmup_s), dtype=np.float32
                )
                warmup_language = os.getenv("WHISPER_GO_LANGUAGE") or "en"
                if warmup_language.strip().lower() == "auto":
                    warmup_language = "en"
                warmup_opts: dict = {
                    "language": warmup_language,
                    "temperature": 0.0,
                    "condition_on_previous_text": False,
                }
                if self._fp16_override is not None:
                    warmup_opts["fp16"] = self._fp16_override
                mlx_whisper.transcribe(
                    warmup_audio,
                    path_or_hf_repo=repo,
                    verbose=None,
                    **warmup_opts,
                )
        else:
            self._get_whisper_model(model_name)

    def transcribe_audio(
        self,
        audio,
        model: str | None = None,
        language: str | None = None,
    ) -> str:
        """Transkribiert ein Audio-Array lokal (ohne Dateischreibzugriff)."""
        model_name = self._resolve_model_name(model)
        options = self._build_options(language)
        log("Transkribiere audio-buffer...")
        with self._transcribe_lock:
            if self._backend == "faster":
                return self._transcribe_faster(audio, model_name, options)
            if self._backend == "mlx":
                return self._transcribe_mlx(audio, model_name, options)
            whisper_model = self._get_whisper_model(model_name)
            result = whisper_model.transcribe(audio, **options)
            return result["text"]

    def _transcribe_faster(self, audio, model_name: str, options: dict) -> str:
        model = self._get_faster_model(model_name)
        faster_opts = {k: v for k, v in options.items() if k != "fp16"}
        # faster-whisper erwartet float temperature; Tuple → erstes Element
        temp = faster_opts.get("temperature")
        if isinstance(temp, tuple):
            faster_opts["temperature"] = float(temp[0])
        segments, _info = model.transcribe(audio, **faster_opts)
        return "".join(seg.text for seg in segments)

    def _transcribe_mlx(self, audio, model_name: str, options: dict) -> str:
        """Transkription via mlx-whisper (Apple Silicon / Metal)."""
        mlx_whisper = _import_mlx_whisper()

        repo = self._map_mlx_model_name(model_name)
        mlx_opts = dict(options)
        beam_size = mlx_opts.pop("beam_size", None)
        if beam_size is not None:
            logger.warning(
                "beam_size wird ignoriert (mlx backend unterstützt kein Beam Search)."
            )
        result = mlx_whisper.transcribe(audio, path_or_hf_repo=repo, **mlx_opts)
        if isinstance(result, dict):
            return str(result.get("text", ""))
        return str(result)

    def transcribe(
        self,
        audio_path: Path,
        model: str | None = None,
        language: str | None = None,
    ) -> str:
        """Transkribiert Audio lokal (whisper/faster/mlx).

        Args:
            audio_path: Pfad zur Audio-Datei
            model: Modell-Name (default: turbo)
            language: Sprachcode oder None für Auto-Detection

        Returns:
            Transkribierter Text
        """
        model_name = self._resolve_model_name(model)
        log(f"Transkribiere {audio_path.name}...")
        options = self._build_options(language)
        with self._transcribe_lock:
            if self._backend == "faster":
                return self._transcribe_faster(str(audio_path), model_name, options)
            if self._backend == "mlx":
                return self._transcribe_mlx(str(audio_path), model_name, options)
            whisper_model = self._get_whisper_model(model_name)
            result = whisper_model.transcribe(str(audio_path), **options)
            return result["text"]

    def supports_streaming(self) -> bool:
        """Lokales Whisper unterstützt kein Streaming."""
        return False


__all__ = ["LocalProvider"]
