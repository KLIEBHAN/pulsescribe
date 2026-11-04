"""Bundled release history: numeric order, bounded reads, and offline fallback."""

from pathlib import Path

import pytest

from utils import release_notes


@pytest.fixture
def notes_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(release_notes, "get_resource_path", lambda _: str(tmp_path))
    return tmp_path


def test_latest_five_releases_are_sorted_numerically(notes_dir, monkeypatch):
    for version in ("1.2.0", "1.9.0", "1.10.0", "1.16.0", "1.16.1", "2.0.0"):
        (notes_dir / f"v{version}.md").write_text(
            f"**Release Date:** 2026-11-04\n\nPulseScribe {version}\n\nChanges.\n"
            "<!-- install-notes -->\nInstall this app.", encoding="utf-8"
        )
    for name in ("README.md", "v3.0.0-rc.1.md", "vUnreleased.md"):
        (notes_dir / name).write_text("Ignore me", encoding="utf-8")
    (notes_dir / "v4.0.0.md").mkdir()
    read_text = Path.read_text
    reads = []

    def tracked_read(path, *args, **kwargs):
        reads.append(path.name)
        return read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", tracked_read)
    text = release_notes.load_recent_release_notes()
    assert reads == ["v2.0.0.md", "v1.16.1.md", "v1.16.0.md", "v1.10.0.md", "v1.9.0.md"]
    assert text.index("2.0.0") < text.index("1.16.1") < text.index("1.10.0")
    assert text.count("Release Date:") == 5
    assert "1.2.0" not in text
    assert "install-notes" not in text
    assert "Install this app" not in text


def test_empty_and_unreadable_notes_are_skipped(notes_dir, monkeypatch):
    (notes_dir / "v3.0.0.md").write_bytes(b"\xff")
    (notes_dir / "v2.0.0.md").write_text("<!-- install-notes -->", encoding="utf-8")
    (notes_dir / "v1.0.0.md").write_text("Changes.", encoding="utf-8")
    assert release_notes.load_recent_release_notes(limit=1) == "Changes."


@pytest.mark.parametrize("limit", [0, -1])
def test_nonpositive_limit_reads_nothing(notes_dir, limit):
    assert release_notes.load_recent_release_notes(limit) == ""


def test_missing_directory_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(release_notes, "get_resource_path", lambda _: str(tmp_path / "missing"))
    assert release_notes.load_recent_release_notes() == ""


def test_resolves_notes_inside_the_app_bundle(tmp_path, monkeypatch):
    import sys

    directory = tmp_path / "docs" / "releases"
    directory.mkdir(parents=True)
    (directory / "v1.0.0.md").write_text("Bundled notes.", encoding="utf-8")
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert release_notes.load_recent_release_notes() == "Bundled notes."


@pytest.mark.parametrize("spec", ["build_app.spec", "build_windows.spec", "build_windows_light.spec"])
def test_all_app_bundles_include_the_release_history(spec):
    root = Path(__file__).resolve().parent.parent
    assert "('docs/releases', 'docs/releases')" in (root / spec).read_text(encoding="utf-8")
