"""Lokaler Whisper Provider.

Standardmäßig nutzt er openai-whisper (PyTorch). Optional kann über
`PULSESCRIBE_LOCAL_BACKEND=faster` der deutlich schnellere faster-whisper
(CTranslate2) genutzt werden. Auf Apple Silicon kann optional auch
`PULSESCRIBE_LOCAL_BACKEND=mlx` (mlx-whisper / Metal) oder
`PULSESCRIBE_LOCAL_BACKEND=lightning` (lightning-whisper-mlx, ~4x schneller) genutzt werden.
"""

import logging
import os
import platform
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Generator

from config import DEFAULT_LOCAL_MODEL, USER_CONFIG_DIR, WHISPER_SAMPLE_RATE
from utils.env import get_env_bool, get_env_int
from utils.logging import log
from utils.timing import timed_operation
from utils.vocabulary import load_vocabulary

logger = logging.getLogger("pulsescribe.providers.local")

# Command for missing cuDNN libraries
CUDNN_INSTALL_COMMAND = "pip install nvidia-cudnn-cu12"

# Default compute type for CPU (float16 is not supported efficiently on CPU)
CPU_COMPUTE_TYPE = "int8"

# Timeout for CUDA model loading (seconds) - prevents hanging on cuDNN issues
# 120s allows for first-time model downloads (~1.5GB for medium)
CUDA_MODEL_LOAD_TIMEOUT = 120


def _register_nvidia_dll_directories() -> None:
    """Register NVIDIA DLL directories for Windows.

    nvidia-cudnn-cu12 and nvidia-cublas-cu12 install DLLs into
    site-packages/nvidia/*/bin, which are not automatically found.
    This function must be called BEFORE importing ctranslate2/faster_whisper.
    """
    if sys.platform != "win32":
        return

    import site

    # Collect all possible site-packages locations
    site_packages: list[str] = []
    try:
        site_packages.extend(site.getsitepackages())
    except AttributeError:
        pass  # May not exist in some environments
    try:
        user_site = site.getusersitepackages()
        if user_site:
            site_packages.append(user_site)
    except AttributeError:
        pass  # May not exist in some environments

    for sp in site_packages:
        nvidia_dir = Path(sp) / "nvidia"
        if not nvidia_dir.is_dir():
            continue
        # Register all nvidia/*/bin directories (cudnn, cublas, cuda_runtime, etc.)
        try:
            subdirs = list(nvidia_dir.iterdir())
        except OSError as e:
            logger.debug(f"Cannot iterate {nvidia_dir}: {e}")
            continue
        for subdir in subdirs:
            if not subdir.is_dir():
                continue
            dll_path = subdir / "bin"
            if dll_path.is_dir():
                try:
                    os.add_dll_directory(str(dll_path))
                    logger.debug(f"NVIDIA DLL directory registered: {dll_path}")
                except OSError as e:
                    logger.warning(f"Failed to register NVIDIA DLL directory {dll_path}: {e}")


# Register NVIDIA DLLs early (before faster_whisper import)
_register_nvidia_dll_directories()


def _is_apple_silicon() -> bool:
    """Prüft ob wir auf Apple Silicon (arm64 macOS) laufen."""
    return sys.platform == "darwin" and platform.machine() == "arm64"


def _mlx_whisper_import_hint() -> str:
    """Hinweistext für fehlgeschlagene mlx-whisper Imports."""
    if getattr(sys, "frozen", False):
        return (
            "Du nutzt vermutlich die macOS-App. Bitte lade die neueste DMG neu herunter "
            "oder setze PULSESCRIBE_LOCAL_BACKEND=whisper."
        )
    return "Installiere es mit `pip install mlx-whisper` oder setze PULSESCRIBE_LOCAL_BACKEND=whisper."


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


def _import_lightning_whisper():
    """Importiert lightning-whisper-mlx mit hilfreicher Fehlermeldung."""
    try:
        from lightning_whisper_mlx import LightningWhisperMLX  # type: ignore[import-not-found]
    except ModuleNotFoundError as e:
        missing = e.name or "unknown"
        raise ImportError(
            f"lightning-whisper-mlx nicht gefunden ({missing}). "
            "Installiere mit `pip install lightning-whisper-mlx` "
            "oder setze PULSESCRIBE_LOCAL_BACKEND=mlx."
        ) from e
    except ImportError as e:
        raise ImportError(
            f"lightning-whisper-mlx konnte nicht geladen werden. Ursache: {e}"
        ) from e
    return LightningWhisperMLX


@contextmanager
def _lightning_workdir() -> Generator[Path, None, None]:
    """Context-Manager für schreibbares Lightning-Arbeitsverzeichnis.

    Lightning-whisper-mlx verwendet hardcodierte relative Pfade (./mlx_models/)
    für Model-Download und -Zugriff. Bei App-Bundles oder DMGs ist das CWD
    oft read-only, was zu "[Errno 30] Read-only file system" führt.

    Dieser Context-Manager:
    1. Erstellt ~/.pulsescribe/lightning_models falls nötig
    2. Wechselt temporär dorthin
    3. Stellt das ursprüngliche CWD nach Abschluss wieder her
    """
    lightning_dir = USER_CONFIG_DIR / "lightning_models"
    lightning_dir.mkdir(parents=True, exist_ok=True)

    old_cwd = os.getcwd()
    try:
        os.chdir(lightning_dir)
        yield lightning_dir
    finally:
        os.chdir(old_cwd)


def _validate_cuda_runtime() -> bool:
    """Prüft ob CUDA-Runtime grundlegend funktioniert.

    torch.cuda.is_available() prüft nur ob CUDA-Treiber vorhanden sind.
    Diese Funktion testet mit einer Dummy-Operation, ob Tensor-Allokation
    auf GPU funktioniert.

    Hinweis: cuDNN-Fehler (z.B. fehlende cudnn_ops64_9.dll) können erst bei
    komplexeren Operationen (Convolutions) auftreten. Dafür gibt es Fallbacks
    in _get_faster_model() und _transcribe_faster().
    """
    try:
        import torch

        if not torch.cuda.is_available():
            return False
        # Einfache GPU-Operation - testet CUDA Runtime (nicht cuDNN)
        test_tensor = torch.zeros(1, device="cuda")
        _ = test_tensor + 1
        del test_tensor
        torch.cuda.empty_cache()
        return True
    except Exception as e:
        logger.warning(f"CUDA-Validierung fehlgeschlagen: {e}")
        return False


def _select_device() -> str:
    """Wählt ein sinnvolles Torch-Device für lokales Whisper.

    Priorität:
      1) PULSESCRIBE_DEVICE Env-Override (z.B. "cpu", "mps", "cuda")
      2) Apple Silicon GPU via MPS
      3) CUDA (falls verfügbar UND funktional)
      4) CPU
    """
    env_device = (os.getenv("PULSESCRIBE_DEVICE") or "").strip().lower()
    if env_device and env_device != "auto":
        return env_device
    try:
        import torch

        if getattr(torch.backends, "mps", None) is not None:
            if torch.backends.mps.is_available() and torch.backends.mps.is_built():
                return "mps"
        if torch.cuda.is_available():
            if _validate_cuda_runtime():
                return "cuda"
            logger.warning(
                "CUDA sichtbar aber nicht funktional (cuDNN fehlt?), nutze CPU"
            )
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
            # Default: "lightning" auf Apple Silicon (schnellstes Backend)
            # Fallback: "auto" versucht faster-whisper, dann openai-whisper
            default_backend = "lightning" if _is_apple_silicon() else "auto"
            backend_env = (
                (os.getenv("PULSESCRIBE_LOCAL_BACKEND") or default_backend)
                .strip()
                .lower()
            )
            if backend_env in {"faster", "faster-whisper"}:
                self._backend = "faster"
            elif backend_env in {"mlx", "mlx-whisper"}:
                self._backend = "mlx"
            elif backend_env in {"lightning", "lightning-whisper-mlx"}:
                self._backend = "lightning"
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
                    f"Unbekannter PULSESCRIBE_LOCAL_BACKEND='{backend_env}', nutze whisper"
                )
                self._backend = "whisper"
            log(f"Lokales Whisper Backend: {self._backend}")

        if self._device is None:
            self._device = _select_device()
            log(f"Lokales Whisper Device: {self._device}")

        if self._fp16_override is None:
            self._fp16_override = get_env_bool("PULSESCRIBE_FP16")

        if self._fast_mode is None:
            fast_env = get_env_bool("PULSESCRIBE_LOCAL_FAST")
            if fast_env is None:
                # Default to fast decoding on faster-whisper unless user opts out.
                self._fast_mode = self._backend == "faster"
            else:
                self._fast_mode = fast_env

        if self._compute_type is None:
            compute_env = os.getenv("PULSESCRIBE_LOCAL_COMPUTE_TYPE")
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
        """Mappt Modellnamen auf mlx-whisper HF-Repos (optional).

        Unterstützte Aliase:
            - Standard (multilingual): tiny, base, small, medium, large, turbo
            - Englisch-only (distilliert, schneller): large-en, medium-en, small-en

        Hinweis: Die distil-whisper Modelle unterstützen NUR Englisch!
        Für Deutsch/multilingual: turbo ist der beste Speed/Quality Trade-off.
        """
        if "/" in model_name:
            return model_name
        mapping = {
            # Standard-Modelle (multilingual)
            "tiny": "mlx-community/whisper-tiny",
            "base": "mlx-community/whisper-base-mlx",
            "small": "mlx-community/whisper-small-mlx",
            "medium": "mlx-community/whisper-medium",
            "large": "mlx-community/whisper-large-v3-mlx",
            "turbo": "mlx-community/whisper-large-v3-turbo",
            "large-v3": "mlx-community/whisper-large-v3-mlx",
            "large-v2": "mlx-community/whisper-large-v2-mlx",
            # Englisch-only (distilliert, 30-40% schneller, NUR ENGLISCH!)
            "large-en": "mlx-community/distil-whisper-large-v3",
            "medium-en": "mlx-community/distil-whisper-medium.en",
            "small-en": "mlx-community/distil-whisper-small.en",
        }
        if model_name in mapping:
            return mapping[model_name]
        if model_name.startswith("whisper-"):
            return f"mlx-community/{model_name}"
        if model_name.startswith("large-v3"):
            return f"mlx-community/whisper-{model_name}"
        return model_name

    def _map_lightning_model_name(self, model_name: str) -> str:
        """Mappt Modellnamen auf lightning-whisper-mlx Namen.

        Unterstützte Modelle: tiny, base, small, medium, large, large-v2, large-v3
        NICHT unterstützt: distil-*.en (English-only), turbo (existiert nicht)
        """
        # Lightning akzeptiert direkte Namen ohne HF-Repo
        mapping = {
            "turbo": "large-v3",  # Turbo existiert nicht in Lightning → Fallback
            "large": "large-v3",
        }
        if model_name in mapping:
            logger.info(f"Lightning: Modell '{model_name}' → '{mapping[model_name]}'")
            return mapping[model_name]

        # distil/English-only Modelle werden nicht unterstützt
        if "distil" in model_name or model_name.endswith("-en"):
            logger.warning(
                f"Lightning unterstützt '{model_name}' nicht (English-only) → large-v3"
            )
            return "large-v3"

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
                    if getattr(
                        cpu_model, "alignment_heads", None
                    ) is not None and getattr(
                        cpu_model.alignment_heads, "is_sparse", False
                    ):
                        heads = cpu_model.alignment_heads
                        cpu_model._buffers["alignment_heads"] = None
                    try:
                        cpu_model = cpu_model.to("mps")
                    finally:
                        if heads is not None:
                            # Restore original tensor (pyright can't infer this is always Tensor)
                            cpu_model._buffers["alignment_heads"] = heads  # type: ignore[arg-type]
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
                "`pip install faster-whisper` oder setze PULSESCRIBE_LOCAL_BACKEND=whisper."
            ) from e

        faster_name = self._map_faster_model_name(model_name)
        device = "cuda" if self._device == "cuda" else "cpu"
        compute_type = self._compute_type or ("float16" if device == "cuda" else CPU_COMPUTE_TYPE)

        cpu_threads = get_env_int("PULSESCRIBE_LOCAL_CPU_THREADS") or 0
        num_workers = get_env_int("PULSESCRIBE_LOCAL_NUM_WORKERS") or 1

        cache_key = (
            f"faster:{faster_name}:{device}:{compute_type}:{cpu_threads}:{num_workers}"
        )
        if cache_key in self._model_cache:
            return self._model_cache[cache_key]

        with self._load_lock:
            if cache_key in self._model_cache:
                return self._model_cache[cache_key]
            log(
                f"Lade faster-whisper Modell '{faster_name}' "
                f"(Device: {device.upper()}, Compute: {compute_type}, "
                f"threads={cpu_threads}, workers={num_workers})..."
            )

            def _load_model(dev: str, comp: str):
                return WhisperModel(
                    faster_name,
                    device=dev,
                    compute_type=comp,
                    cpu_threads=cpu_threads,
                    num_workers=num_workers,
                )

            try:
                if device == "cuda":
                    # CUDA-Loading mit Timeout (kann bei cuDNN-Problemen hängen)
                    with ThreadPoolExecutor(max_workers=1) as executor:
                        future = executor.submit(_load_model, device, compute_type)
                        try:
                            self._model_cache[cache_key] = future.result(
                                timeout=CUDA_MODEL_LOAD_TIMEOUT
                            )
                        except FuturesTimeoutError:
                            logger.warning(
                                f"CUDA Model-Loading Timeout ({CUDA_MODEL_LOAD_TIMEOUT}s) - "
                                f"cuDNN hängt vermutlich. Fallback auf CPU."
                            )
                            raise RuntimeError("CUDA model loading timeout")
                else:
                    self._model_cache[cache_key] = _load_model(device, compute_type)
                logger.info(f"Modell '{faster_name}' geladen ({device.upper()})")
            except Exception as e:
                error_msg = str(e).lower()
                # Fallback auf CPU wenn CUDA/cuDNN fehlschlägt oder Timeout
                if device == "cuda" and (
                    "cudnn" in error_msg
                    or "cuda" in error_msg
                    or "timeout" in error_msg
                ):
                    logger.warning(
                        f"CUDA/cuDNN nicht verfügbar ({e}), "
                        f"Fallback auf CPU mit {CPU_COMPUTE_TYPE}. "
                        f"Für GPU-Unterstützung: {CUDNN_INSTALL_COMMAND}"
                    )
                    # Device und Compute-Type global umstellen (verhindert wiederholte Fallback-Versuche)
                    self._device = "cpu"
                    self._compute_type = CPU_COMPUTE_TYPE
                    cpu_compute = CPU_COMPUTE_TYPE
                    cpu_cache_key = (
                        f"faster:{faster_name}:cpu:{cpu_compute}:{cpu_threads}:{num_workers}"
                    )
                    log(
                        f"Lade faster-whisper Modell '{faster_name}' "
                        f"(Device: CPU [Fallback], Compute: {cpu_compute})..."
                    )
                    self._model_cache[cpu_cache_key] = _load_model("cpu", cpu_compute)
                    logger.info(f"Modell '{faster_name}' geladen (CPU Fallback)")
                    return self._model_cache[cpu_cache_key]
                raise
            return self._model_cache[cache_key]

    def _get_lightning_model(self, model_name: str) -> Any:
        """Lädt lightning-whisper-mlx Modell (mit Caching).

        Lightning Whisper MLX lädt das Modell bei Instanziierung, daher
        cachen wir die Instanz analog zu faster-whisper.

        WICHTIG: Lightning verwendet hardcodierte relative Pfade (./mlx_models/).
        Wir nutzen _lightning_workdir() um in ein schreibbares Verzeichnis zu wechseln.
        """
        self._ensure_runtime_config()
        LightningWhisperMLX = _import_lightning_whisper()

        lightning_name = self._map_lightning_model_name(model_name)

        # Batch-Size und Quantisierung aus ENV
        batch_size = get_env_int("PULSESCRIBE_LIGHTNING_BATCH_SIZE") or 12
        quant_env = os.getenv("PULSESCRIBE_LIGHTNING_QUANT", "").strip().lower()
        quant = None if quant_env in ("", "none", "false") else quant_env

        cache_key = f"lightning:{lightning_name}:{batch_size}:{quant}"
        if cache_key in self._model_cache:
            return self._model_cache[cache_key]

        with self._load_lock:
            if cache_key in self._model_cache:
                return self._model_cache[cache_key]

            log(
                f"Lade lightning-whisper-mlx '{lightning_name}' "
                f"(batch_size={batch_size}, quant={quant})..."
            )
            # Model-Download in schreibbarem Verzeichnis
            with _lightning_workdir():
                self._model_cache[cache_key] = LightningWhisperMLX(
                    model=lightning_name,
                    batch_size=batch_size,
                    quant=quant,
                )
            return self._model_cache[cache_key]

    def _build_options(self, language: str | None) -> dict:
        """Baut Options inkl. Vocabulary und Speed-Overrides.

        Rückgabe ist kompatibel zu openai-whisper; für faster-whisper/mlx-whisper wird
        je nach Backend ein Subset genutzt.
        """
        self._ensure_runtime_config()

        options: dict = {}
        # "auto" bedeutet Auto-Detection → nicht setzen (None/leer = Auto)
        if language and language.strip().lower() != "auto":
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
            # Override via PULSESCRIBE_FP16=true möglich.
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
            wt_env = get_env_bool("PULSESCRIBE_LOCAL_WITHOUT_TIMESTAMPS")
            options["without_timestamps"] = True if wt_env is None else wt_env

            vad_env = get_env_bool("PULSESCRIBE_LOCAL_VAD_FILTER")
            if vad_env:
                options["vad_filter"] = True
        elif self._backend == "mlx":
            # mlx-whisper nutzt fp16 per Default; Override via PULSESCRIBE_FP16 möglich.
            if self._fp16_override is not None:
                options["fp16"] = self._fp16_override

        # Fast-Mode: schnellere Decoding Defaults (kann via ENV überschrieben werden)
        if self._fast_mode:
            options.setdefault("temperature", 0.0)
            # mlx-whisper und lightning-whisper-mlx haben keinen Beam-Search Decoder.
            # Deshalb darf beam_size nicht gesetzt werden.
            if self._backend not in ("mlx", "lightning"):
                options.setdefault("beam_size", 1)
                options.setdefault("best_of", 1)
            options.setdefault("condition_on_previous_text", False)

        # Explizite Decode-Overrides
        beam_size = get_env_int("PULSESCRIBE_LOCAL_BEAM_SIZE")
        if beam_size is not None and self._backend in ("mlx", "lightning"):
            logger.warning(
                f"PULSESCRIBE_LOCAL_BEAM_SIZE wird ignoriert ({self._backend} backend unterstützt kein Beam Search)."
            )
        elif beam_size is not None:
            options["beam_size"] = beam_size

        best_of = get_env_int("PULSESCRIBE_LOCAL_BEST_OF")
        if best_of is not None:
            options["best_of"] = best_of

        temp_env = os.getenv("PULSESCRIBE_LOCAL_TEMPERATURE")
        if temp_env:
            try:
                if "," in temp_env:
                    options["temperature"] = tuple(
                        float(t.strip()) for t in temp_env.split(",") if t.strip()
                    )
                else:
                    options["temperature"] = float(temp_env.strip())
            except ValueError:
                logger.warning(f"Ungültiger PULSESCRIBE_LOCAL_TEMPERATURE: {temp_env}")

        return options

    def _resolve_model_name(self, model: str | None) -> str:
        """Ermittelt Modellname aus Arg > PULSESCRIBE_LOCAL_MODEL > Default."""
        if model:
            return model
        env_model = os.getenv("PULSESCRIBE_LOCAL_MODEL")
        if env_model:
            return env_model.strip()
        return self.default_model

    def get_runtime_info(self) -> dict[str, str | None]:
        """Gibt aktuelle Runtime-Konfiguration zurück (nach preload/transcribe).

        Returns:
            Dict mit backend, device, compute_type (compute_type nur bei faster-whisper).
        """
        self._ensure_runtime_config()
        info: dict[str, str | None] = {
            "backend": self._backend,
            "device": self._device,
        }
        # compute_type nur bei faster-whisper relevant
        if self._backend == "faster":
            device = "cuda" if self._device == "cuda" else "cpu"
            info["compute_type"] = self._compute_type or (
                "float16" if device == "cuda" else CPU_COMPUTE_TYPE
            )
        return info

    def preload(self, model: str | None = None) -> None:
        """Lädt ein Modell vorab in den Cache."""
        model_name = self._resolve_model_name(model)
        self._ensure_runtime_config()
        if self._backend == "faster":
            self._get_faster_model(model_name)
        elif self._backend == "mlx":
            with self._transcribe_lock:
                t0 = time.perf_counter()
                mlx_whisper = _import_mlx_whisper()
                t_import = time.perf_counter() - t0

                import numpy as np

                repo = self._map_mlx_model_name(model_name)
                logger.debug(f"MLX preload: repo={repo}, import={t_import*1000:.0f}ms")

                warmup_s = 0.2
                warmup_audio = np.zeros(
                    int(WHISPER_SAMPLE_RATE * warmup_s), dtype=np.float32
                )
                warmup_language = os.getenv("PULSESCRIBE_LANGUAGE") or "en"
                if warmup_language.strip().lower() == "auto":
                    warmup_language = "en"
                warmup_opts: dict = {
                    "language": warmup_language,
                    "temperature": 0.0,
                    "condition_on_previous_text": False,
                }
                if self._fp16_override is not None:
                    warmup_opts["fp16"] = self._fp16_override

                t1 = time.perf_counter()
                mlx_whisper.transcribe(
                    warmup_audio,
                    path_or_hf_repo=repo,
                    verbose=None,
                    **warmup_opts,
                )
                t_warmup = time.perf_counter() - t1
                logger.debug(
                    f"MLX preload complete: warmup={t_warmup:.2f}s (model loaded & compiled)"
                )
        elif self._backend == "lightning":
            with self._transcribe_lock:
                import numpy as np

                t0 = time.perf_counter()
                model = self._get_lightning_model(model_name)
                t_load = time.perf_counter() - t0

                # Warmup mit kurzem Dummy-Audio
                warmup_s = 0.2
                warmup_audio = np.zeros(
                    int(WHISPER_SAMPLE_RATE * warmup_s), dtype=np.float32
                )
                warmup_language = os.getenv("PULSESCRIBE_LANGUAGE") or "en"
                if warmup_language.strip().lower() == "auto":
                    warmup_language = "en"

                t1 = time.perf_counter()
                # Warmup im selben Verzeichnis wie Model-Download
                with _lightning_workdir():
                    model.transcribe(warmup_audio, language=warmup_language)  # type: ignore[union-attr]
                t_warmup = time.perf_counter() - t1

                logger.debug(
                    f"Lightning preload complete: load={t_load:.2f}s, warmup={t_warmup:.2f}s"
                )
        else:
            self._get_whisper_model(model_name)

    def _log_transcription_start(self, model_name: str, language: str | None) -> None:
        """Loggt Transkriptions-Start mit Provider/Backend/Modell/Device/Compute-Type."""
        self._ensure_runtime_config()
        parts = [f"Local ({self._backend}): {model_name}"]
        # Device nur bei whisper/faster relevant (mlx/lightning nutzen immer Metal)
        if self._backend in ("whisper", "faster"):
            parts.append(f"device={self._device}")
        if self._backend == "faster":
            device = "cuda" if self._device == "cuda" else "cpu"
            compute = self._compute_type or ("float16" if device == "cuda" else CPU_COMPUTE_TYPE)
            parts.append(f"compute={compute}")
        parts.append(f"lang={language or 'auto'}")
        logger.info(", ".join(parts))

    def transcribe_audio(
        self,
        audio,
        model: str | None = None,
        language: str | None = None,
    ) -> str:
        """Transkribiert ein Audio-Array lokal (ohne Dateischreibzugriff)."""
        model_name = self._resolve_model_name(model)
        self._log_transcription_start(model_name, language)
        options = self._build_options(language)
        with timed_operation("Local-Transkription", logger=logger, include_session=False):
            with self._transcribe_lock:
                if self._backend == "faster":
                    return self._transcribe_faster(audio, model_name, options)
                if self._backend == "mlx":
                    return self._transcribe_mlx(audio, model_name, options)
                if self._backend == "lightning":
                    return self._transcribe_lightning(audio, model_name, options)
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

        try:
            segments, _info = model.transcribe(audio, **faster_opts)
            return "".join(seg.text for seg in segments)
        except Exception as e:
            error_msg = str(e).lower()
            if faster_opts.get("vad_filter") and "silero_vad_v6.onnx" in error_msg:
                logger.warning(
                    "VAD-Modell fehlt (silero_vad_v6.onnx). "
                    "Deaktiviere VAD-Filter und versuche erneut."
                )
                retry_opts = dict(faster_opts)
                retry_opts["vad_filter"] = False
                try:
                    segments, _info = model.transcribe(audio, **retry_opts)
                    return "".join(seg.text for seg in segments)
                except Exception as retry_error:
                    faster_opts = retry_opts
                    e = retry_error
                    error_msg = str(e).lower()
            # CUDA/cuDNN-Fehler bei Transkription → Fallback auf CPU
            if self._device == "cuda" and ("cudnn" in error_msg or "cuda" in error_msg):
                logger.warning(
                    f"CUDA/cuDNN-Fehler bei Transkription: {e}. "
                    "Wechsle zu CPU-Modus..."
                )
                # Device und Compute-Type auf CPU-kompatible Werte umstellen
                self._device = "cpu"
                self._compute_type = CPU_COMPUTE_TYPE
                # Alle CUDA-Modelle aus Cache entfernen
                cuda_keys = [k for k in self._model_cache if ":cuda:" in k]
                for k in cuda_keys:
                    del self._model_cache[k]
                # Neu laden mit CPU und erneut versuchen
                model = self._get_faster_model(model_name)
                segments, _info = model.transcribe(audio, **faster_opts)
                return "".join(seg.text for seg in segments)
            raise

    def _transcribe_mlx(self, audio, model_name: str, options: dict) -> str:
        """Transkription via mlx-whisper (Apple Silicon / Metal)."""
        t0 = time.perf_counter()
        mlx_whisper = _import_mlx_whisper()
        t_import = time.perf_counter() - t0

        repo = self._map_mlx_model_name(model_name)
        mlx_opts = dict(options)

        # Warnung bei Verwendung von English-only Modellen mit nicht-englischer Sprache
        language = mlx_opts.get("language", "").lower()
        is_distil_model = "distil" in repo
        if is_distil_model and language and language not in ("en", "english"):
            logger.warning(
                f"⚠️ Modell '{model_name}' (distil) unterstützt NUR Englisch! "
                f"Für '{language}' wird 'turbo' oder 'large' empfohlen."
            )

        beam_size = mlx_opts.pop("beam_size", None)
        if beam_size is not None:
            logger.warning(
                "beam_size wird ignoriert (mlx backend unterstützt kein Beam Search)."
            )

        # Berechne Audio-Länge für RTF-Logging
        audio_duration = 0.0
        if hasattr(audio, "__len__"):
            audio_duration = len(audio) / WHISPER_SAMPLE_RATE

        logger.debug(
            f"MLX transcribe: repo={repo}, audio={audio_duration:.2f}s, "
            f"opts={{{', '.join(f'{k}={v}' for k, v in mlx_opts.items())}}}"
        )

        t1 = time.perf_counter()
        result = mlx_whisper.transcribe(audio, path_or_hf_repo=repo, **mlx_opts)
        t_transcribe = time.perf_counter() - t1

        # Performance-Breakdown
        rtf = t_transcribe / audio_duration if audio_duration > 0 else 0
        logger.debug(
            f"MLX timing: import={t_import*1000:.0f}ms, "
            f"transcribe={t_transcribe:.2f}s (RTF={rtf:.2f}x)"
        )

        if isinstance(result, dict):
            return str(result.get("text", ""))
        return str(result)

    def _transcribe_lightning(self, audio, model_name: str, options: dict) -> str:
        """Transkription via lightning-whisper-mlx (Apple Silicon, ~4x faster).

        Bei Fehlern wird automatisch auf MLX zurückgefallen.
        """
        try:
            return self._transcribe_lightning_core(audio, model_name, options)
        except Exception as e:
            # Deutliche Kennzeichnung im Log
            logger.warning(
                f"⚠️ FALLBACK: Lightning-Transkription fehlgeschlagen, "
                f"wechsle zu MLX. Fehler: {e}"
            )
            log(f"⚠️ Lightning → MLX Fallback (Grund: {type(e).__name__})")
            return self._transcribe_mlx(audio, model_name, options)

    def _transcribe_lightning_core(self, audio, model_name: str, options: dict) -> str:
        """Kern-Transkription via lightning-whisper-mlx (ohne Fallback).

        WICHTIG: Lightning verwendet hardcodierte relative Pfade (./mlx_models/).
        Sowohl Model-Loading als auch Transcribe müssen im selben Verzeichnis erfolgen.
        """
        t0 = time.perf_counter()
        model = self._get_lightning_model(model_name)
        t_load = time.perf_counter() - t0

        # Lightning nutzt nur 'language' aus options
        language = options.get("language")

        # Berechne Audio-Länge für RTF-Logging
        audio_duration = 0.0
        if hasattr(audio, "__len__"):
            audio_duration = len(audio) / WHISPER_SAMPLE_RATE

        logger.debug(
            f"Lightning transcribe: model={model_name}, audio={audio_duration:.2f}s, "
            f"language={language}"
        )

        t1 = time.perf_counter()
        # Transcribe im selben Verzeichnis wie Model-Download
        with _lightning_workdir():
            result = model.transcribe(audio, language=language)
        t_transcribe = time.perf_counter() - t1

        # Performance-Breakdown
        rtf = t_transcribe / audio_duration if audio_duration > 0 else 0
        logger.debug(
            f"Lightning timing: load={t_load*1000:.0f}ms, "
            f"transcribe={t_transcribe:.2f}s (RTF={rtf:.2f}x)"
        )

        if isinstance(result, dict):
            return str(result.get("text", ""))
        return str(result)

    def transcribe(
        self,
        audio_path: Path,
        model: str | None = None,
        language: str | None = None,
    ) -> str:
        """Transkribiert Audio lokal (whisper/faster/mlx/lightning).

        Args:
            audio_path: Pfad zur Audio-Datei
            model: Modell-Name (default: turbo)
            language: Sprachcode oder None für Auto-Detection

        Returns:
            Transkribierter Text
        """
        model_name = self._resolve_model_name(model)
        self._log_transcription_start(model_name, language)
        options = self._build_options(language)
        with timed_operation("Local-Transkription", logger=logger, include_session=False):
            with self._transcribe_lock:
                if self._backend == "faster":
                    return self._transcribe_faster(str(audio_path), model_name, options)
                if self._backend == "mlx":
                    return self._transcribe_mlx(str(audio_path), model_name, options)
                if self._backend == "lightning":
                    return self._transcribe_lightning(str(audio_path), model_name, options)
                whisper_model = self._get_whisper_model(model_name)
                result = whisper_model.transcribe(str(audio_path), **options)
                return result["text"]

    def supports_streaming(self) -> bool:
        """Lokales Whisper unterstützt kein Streaming."""
        return False


__all__ = ["LocalProvider"]
