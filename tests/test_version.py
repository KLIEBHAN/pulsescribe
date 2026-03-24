import sys

from utils.version import get_app_version


def test_get_app_version_prefers_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("PULSESCRIBE_VERSION", "9.9.9")
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nversion = "1.2.3"\n',
        encoding="utf-8",
    )
    assert get_app_version(project_root=tmp_path) == "9.9.9"


def test_get_app_version_uses_pyproject(tmp_path, monkeypatch):
    monkeypatch.delenv("PULSESCRIBE_VERSION", raising=False)
    monkeypatch.delenv("WHISPERGO_VERSION", raising=False)
    monkeypatch.delenv("VERSION", raising=False)

    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "pulsescribe"\nversion = "1.2.3"\n',
        encoding="utf-8",
    )
    assert get_app_version(project_root=tmp_path) == "1.2.3"


def test_get_app_version_falls_back_to_changelog(tmp_path, monkeypatch):
    for key in ("PULSESCRIBE_VERSION", "WHISPERGO_VERSION", "VERSION"):
        monkeypatch.delenv(key, raising=False)

    (tmp_path / "CHANGELOG.md").write_text(
        "## [1.2.4] - 2026-03-01\n",
        encoding="utf-8",
    )
    assert get_app_version(project_root=tmp_path) == "1.2.4"


def test_get_app_version_returns_default_when_not_found(tmp_path, monkeypatch):
    for key in ("PULSESCRIBE_VERSION", "WHISPERGO_VERSION", "VERSION"):
        monkeypatch.delenv(key, raising=False)

    assert get_app_version(project_root=tmp_path, default="unknown") == "unknown"


def test_get_app_version_uses_bundle_root_for_frozen_windows(tmp_path, monkeypatch):
    import utils.version as version_mod

    for key in ("PULSESCRIBE_VERSION", "WHISPERGO_VERSION", "VERSION"):
        monkeypatch.delenv(key, raising=False)

    bundle_root = tmp_path / "PulseScribe"
    internal_dir = bundle_root / "_internal" / "utils"
    internal_dir.mkdir(parents=True)
    (bundle_root / "pyproject.toml").write_text(
        '[project]\nname = "pulsescribe"\nversion = "2.3.4"\n',
        encoding="utf-8",
    )

    monkeypatch.setattr(version_mod, "__file__", str(internal_dir / "version.py"))
    monkeypatch.setattr(version_mod.sys, "platform", "win32")
    monkeypatch.setattr(version_mod.sys, "frozen", True, raising=False)
    monkeypatch.setattr(
        version_mod.sys,
        "executable",
        str(bundle_root / "PulseScribe.exe"),
        raising=False,
    )

    assert version_mod.get_app_version(default="unknown") == "2.3.4"


def test_get_app_version_uses_importlib_metadata_when_repo_files_missing(
    tmp_path, monkeypatch
):
    import utils.version as version_mod

    for key in ("PULSESCRIBE_VERSION", "WHISPERGO_VERSION", "VERSION"):
        monkeypatch.delenv(key, raising=False)

    monkeypatch.setattr(version_mod, "_version_from_importlib_metadata", lambda: "7.8.9")
    monkeypatch.setattr(version_mod, "_version_from_windows_executable", lambda: None)

    assert version_mod.get_app_version(project_root=tmp_path) == "7.8.9"


def test_get_app_version_uses_windows_executable_version_when_frozen(
    tmp_path, monkeypatch
):
    import types
    import utils.version as version_mod

    for key in ("PULSESCRIBE_VERSION", "WHISPERGO_VERSION", "VERSION"):
        monkeypatch.delenv(key, raising=False)

    executable = tmp_path / "PulseScribe.exe"
    executable.write_bytes(b"")

    monkeypatch.setattr(version_mod, "_version_from_importlib_metadata", lambda: None)
    monkeypatch.setattr(version_mod.sys, "platform", "win32")
    monkeypatch.setattr(version_mod.sys, "frozen", True, raising=False)
    monkeypatch.setattr(
        version_mod.sys,
        "executable",
        str(executable),
        raising=False,
    )
    monkeypatch.setitem(
        sys.modules,
        "win32api",
        types.SimpleNamespace(
            GetFileVersionInfo=lambda _path, _query: {
                "FileVersionMS": (3 << 16) | 4,
                "FileVersionLS": (5 << 16) | 0,
            }
        ),
    )

    assert version_mod.get_app_version(project_root=tmp_path, default="unknown") == "3.4.5"
