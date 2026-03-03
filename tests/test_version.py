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
