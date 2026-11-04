"""Progress for first-time model downloads from the Hugging Face Hub.

mlx-whisper and faster-whisper download a model through huggingface_hub the first time
it is loaded, without any feedback. Call ``download_with_progress(repo_id, on_progress)``
right before loading: if the repo is not cached, it downloads the same files the backend
would (``snapshot_download``) and reports ``on_progress(downloaded, total)`` whenever the
shown percentage changes; the backend then finds everything in the cache.

huggingface_hub >= 2 feeds the summed bytes of all files (HTTP and Xet) into the
byte bars ("Downloading bytes", "Reconstructing") of the ``tqdm_class`` passed to
``snapshot_download``; older versions only count files, which is scaled to the expected
repo size. The repo size is fetched once from the Hub to keep percentages stable. Cached repos,
missing huggingface_hub and download errors report nothing (the backend's own load then
downloads or fails as before).
"""

from __future__ import annotations

import fnmatch
import logging
import threading
from collections.abc import Callable, Sequence
from pathlib import Path

logger = logging.getLogger("pulsescribe.model_download")

ProgressCallback = Callable[[int, "int | None"], None]


def _is_repo_cached(
    repo_id: str,
    allow_patterns: Sequence[str] | None,
    required_files: Sequence[str] = ("config.json",),
) -> bool:
    """True when the repo can load offline; unknown states count as cached (no download).

    Older hubs return any existing snapshot folder offline, also after an interrupted
    download, so each ``required_files`` glob must match a file in it.
    """
    try:
        from huggingface_hub import snapshot_download
        from huggingface_hub.errors import LocalEntryNotFoundError
    except ImportError:
        return True
    try:
        folder = Path(snapshot_download(repo_id, allow_patterns=allow_patterns, local_files_only=True))
    except LocalEntryNotFoundError:
        return False
    except Exception:
        return True
    try:
        names = [entry.name for entry in folder.iterdir() if entry.exists()]
    except OSError:
        return True
    return all(any(fnmatch.fnmatch(name, pattern) for name in names) for pattern in required_files)


def _expected_repo_size(repo_id: str, allow_patterns: Sequence[str] | None) -> int | None:
    """Total bytes of the files to download, or None if the Hub cannot tell."""
    try:
        from huggingface_hub import HfApi

        info = HfApi().model_info(repo_id, files_metadata=True)
    except Exception:
        return None
    total = 0
    for sibling in info.siblings or []:
        if allow_patterns and not any(fnmatch.fnmatch(sibling.rfilename, p) for p in allow_patterns):
            continue
        total += sibling.size or 0
    return total or None


class _ProgressCollector:
    """Turns the progress bars snapshot_download creates into on_progress calls."""

    def __init__(self, on_progress: ProgressCallback, expected_total: Callable[[], int | None]):
        self._on_progress = on_progress
        self._expected_total = expected_total
        self._bars: list = []
        self._lock = threading.Lock()
        self._last_percent: int | None = None

    def bar_class(self) -> type:
        collector = self

        class _Bar:
            """Minimal tqdm stand-in: counts, never prints."""

            _lock = threading.RLock()

            # tqdm.contrib.concurrent.thread_map (used by older hubs) shares this lock.
            @classmethod
            def get_lock(cls):
                return cls._lock

            @classmethod
            def set_lock(cls, lock):
                cls._lock = lock

            def __init__(self, iterable=None, *args, total=None, initial=0, unit="it", desc="", **_kwargs):
                self.iterable = iterable
                self.total = total if total is not None else (
                    len(iterable) if hasattr(iterable, "__len__") else None
                )
                self.n = initial or 0
                self.unit = unit
                self.desc = desc or ""
                self.format_dict = {"rate": None, "elapsed": 0}
                with collector._lock:
                    collector._bars.append(self)

            def __iter__(self):
                for item in self.iterable or ():
                    yield item
                    self.update(1)

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def update(self, n=1):
                self.n += n or 0
                collector.report()

            def refresh(self, *args, **kwargs):
                collector.report()

            def set_description_str(self, desc="", refresh=True):
                self.desc = desc or ""

            set_description = set_description_str

            def set_postfix_str(self, *args, **kwargs):
                pass

            set_postfix = set_postfix_str

            def close(self):
                pass

            def clear(self, *args, **kwargs):
                pass

        return _Bar

    def report(self) -> None:
        with self._lock:
            bars = list(self._bars)
        # The repo size from the Hub keeps the denominator stable while files are still
        # being announced; bars only grow their totals file by file.
        expected = self._expected_total()
        byte_bars = [b for b in bars if b.unit == "B" and b.total]
        if byte_bars:
            # Network bytes ("Downloading bytes") move steadily with Xet, written bytes
            # ("Reconstructing") lag behind in bursts; show whichever is further.
            downloaded = int(max(b.n for b in byte_bars))
            total = max(expected or 0, *(int(b.total) for b in byte_bars))
        else:
            file_bar = next((b for b in bars if b.unit != "B" and b.total), None)
            if file_bar is None or not expected:
                return
            downloaded, total = int(expected * file_bar.n / file_bar.total), expected
        percent = min(100, downloaded * 100 // max(total, 1))
        if self._last_percent is not None and percent <= self._last_percent:
            return  # never repeat or move backwards
        self._last_percent = percent
        try:
            self._on_progress(downloaded, total)
        except Exception:
            logger.debug("Download-Fortschritt konnte nicht angezeigt werden", exc_info=True)


def download_with_progress(
    repo_id: str,
    on_progress: ProgressCallback | None,
    *,
    allow_patterns: Sequence[str] | None = None,
    required_files: Sequence[str] = ("config.json",),
) -> None:
    """Download ``repo_id`` with progress if it is not cached yet; never raises.

    ``required_files`` are globs that must exist for the cache to count as complete
    (e.g. the weight file), so an interrupted earlier download is resumed with progress.
    """
    if on_progress is None or "/" not in repo_id or Path(repo_id).exists():
        return
    if _is_repo_cached(repo_id, allow_patterns, required_files):
        return
    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        return

    expected: dict = {}

    def expected_total() -> int | None:
        if "value" not in expected:
            expected["value"] = _expected_repo_size(repo_id, allow_patterns)
        return expected["value"]

    collector = _ProgressCollector(on_progress, expected_total)
    logger.info("Lade Modell %s herunter (%s Bytes)", repo_id, expected_total() or "?")
    try:
        snapshot_download(repo_id, allow_patterns=allow_patterns, tqdm_class=collector.bar_class())
    except Exception as error:
        logger.warning("Download von %s mit Fortschritt fehlgeschlagen: %s", repo_id, error)


def format_download_status(model: str, downloaded: int, total: int | None) -> str:
    """Status text with the percentage first, so a short menu bar title keeps it."""
    def size(value: int) -> str:
        return f"{value / 1e9:.1f} GB" if value >= 1e9 else f"{value / 1e6:.0f} MB"

    if total:
        percent = min(99, int(downloaded * 100 / total))
        return f"{percent} % · downloading {model} ({size(total)})"
    return f"Downloading {model}: {size(downloaded)}"


__all__ = ["download_with_progress", "format_download_status"]
