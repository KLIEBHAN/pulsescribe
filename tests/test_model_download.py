"""Tests for first-time model download progress (utils.model_download)."""

from __future__ import annotations

import sys
import types

import pytest

import utils.model_download as md


def test_format_download_status_puts_the_percentage_first():
    assert md.format_download_status("turbo", 650_000_000, 1_500_000_000) == (
        "43 % · downloading turbo (1.5 GB)"
    )
    assert md.format_download_status("small", 470_000_000, 470_000_000).startswith("99 %")
    assert md.format_download_status("turbo", 120_000_000, None) == "Downloading turbo: 120 MB"


@pytest.fixture
def fake_hub(monkeypatch):
    """A huggingface_hub stand-in whose snapshot_download drives the given tqdm_class."""
    calls = []
    script = {"run": None}

    def snapshot_download(repo_id, allow_patterns=None, local_files_only=False, tqdm_class=None):
        calls.append({"repo": repo_id, "patterns": allow_patterns,
                      "offline": local_files_only, "tqdm": tqdm_class})
        if script["run"]:
            script["run"](tqdm_class)

    module = types.ModuleType("huggingface_hub")
    module.snapshot_download = snapshot_download
    monkeypatch.setitem(sys.modules, "huggingface_hub", module)
    monkeypatch.setattr(md, "_is_repo_cached", lambda repo, patterns, required=(): False)
    monkeypatch.setattr(md, "_expected_repo_size", lambda repo, patterns: None)
    module.calls, module.script = calls, script
    return module


@pytest.mark.parametrize(
    ("repo", "callback"),
    [("mlx-community/whisper-large-v3-turbo", None), ("turbo", lambda d, t: None)],
)
def test_without_callback_or_for_plain_names_nothing_happens(fake_hub, repo, callback):
    md.download_with_progress(repo, callback)
    assert fake_hub.calls == []


def test_cached_repo_is_not_downloaded(fake_hub, monkeypatch):
    monkeypatch.setattr(md, "_is_repo_cached", lambda repo, patterns, required=(): True)
    md.download_with_progress("org/model", lambda d, t: pytest.fail("no progress"))
    assert fake_hub.calls == []


def test_expected_size_keeps_early_small_files_from_jumping_to_99(fake_hub, monkeypatch):
    monkeypatch.setattr(md, "_expected_repo_size", lambda repo, patterns: 1_000_000)
    reported = []

    def run(bar_cls):
        rebuild = bar_cls(total=0, unit="B", desc="Reconstructing")
        rebuild.total += 1_000  # config.json announced first
        rebuild.update(1_000)
        rebuild.total += 999_000  # the weights
        for _ in range(4):
            rebuild.update(249_750)

    fake_hub.script["run"] = run
    md.download_with_progress("org/model", lambda d, t: reported.append(d * 100 // t))
    assert reported == [0, 25, 50, 75, 100]


def test_byte_progress_from_the_reconstruct_bar(fake_hub):
    reported = []

    def run(bar_cls):
        transfer = bar_cls(total=0, initial=0, unit="B", desc="Downloading bytes")
        rebuild = bar_cls(total=0, initial=0, unit="B", desc="Reconstructing (incomplete total...)")
        for size in (600, 400):  # two files announce their sizes
            rebuild.total += size
            transfer.total += size
            rebuild.refresh()
        for _ in range(10):
            rebuild.update(100)
            transfer.update(50)

    fake_hub.script["run"] = run
    md.download_with_progress("org/model", lambda d, t: reported.append((d, t)),
                              allow_patterns=["*.bin"])
    assert fake_hub.calls[0]["patterns"] == ["*.bin"]
    assert reported[-1] == (1000, 1000)
    assert [d for d, _ in reported] == sorted(d for d, _ in reported)
    assert len(reported) == len({d * 100 // t for d, t in reported})  # one call per percent


def test_older_hubs_report_file_progress_scaled_to_the_expected_size(fake_hub, monkeypatch):
    monkeypatch.setattr(md, "_expected_repo_size", lambda repo, patterns: 2_000)
    reported = []

    def run(bar_cls):
        for _ in bar_cls(["a", "b", "c", "d"], desc="Fetching 4 files"):
            pass

    fake_hub.script["run"] = run
    md.download_with_progress("org/model", lambda d, t: reported.append((d, t)))
    assert reported == [(500, 2000), (1000, 2000), (1500, 2000), (2000, 2000)]


def test_download_and_callback_errors_never_raise(fake_hub):
    def run(bar_cls):
        bar = bar_cls(total=10, unit="B", desc="Reconstructing")
        bar.update(5)
        raise OSError("network down")

    fake_hub.script["run"] = run
    md.download_with_progress("org/model", lambda d, t: (_ for _ in ()).throw(RuntimeError("UI")))


def test_cache_counts_only_with_required_files(monkeypatch, tmp_path):
    """Older hubs return an interrupted snapshot offline; the weights must be there."""
    errors = types.ModuleType("huggingface_hub.errors")

    class LocalEntryNotFoundError(Exception):
        pass

    errors.LocalEntryNotFoundError = LocalEntryNotFoundError
    hub = types.ModuleType("huggingface_hub")
    hub.snapshot_download = lambda repo_id, allow_patterns=None, local_files_only=False: str(tmp_path)
    monkeypatch.setitem(sys.modules, "huggingface_hub", hub)
    monkeypatch.setitem(sys.modules, "huggingface_hub.errors", errors)
    (tmp_path / "config.json").write_text("{}")

    assert md._is_repo_cached("org/m", None, ("config.json", "weights.*")) is False
    (tmp_path / "weights.npz").write_bytes(b"w")
    assert md._is_repo_cached("org/m", None, ("config.json", "weights.*")) is True

    def missing(*args, **kwargs):
        raise LocalEntryNotFoundError("not cached")

    hub.snapshot_download = missing
    assert md._is_repo_cached("org/m", None) is False


def test_bar_class_works_with_tqdms_thread_map():
    """Older hubs drive tqdm_class through tqdm.contrib.concurrent.thread_map."""
    concurrent = pytest.importorskip("tqdm.contrib.concurrent")
    reported = []
    collector = md._ProgressCollector(lambda d, t: reported.append((d, t)), lambda: 400)
    results = concurrent.thread_map(lambda x: x * 2, [1, 2, 3, 4], tqdm_class=collector.bar_class())
    assert results == [2, 4, 6, 8]
    assert reported[-1] == (400, 400)
