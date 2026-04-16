"""Helpers for resolving the current PulseScribe app version."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

_VERSION_ENV_KEYS = ("PULSESCRIBE_VERSION", "WHISPERGO_VERSION", "VERSION")
_DIST_VERSION_NAMES = ("pulsescribe", "PulseScribe")


def _read_text_safe(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _version_from_env() -> str | None:
    for key in _VERSION_ENV_KEYS:
        value = (os.getenv(key) or "").strip()
        if value:
            return value
    return None


def _version_from_bundle() -> str | None:
    if not getattr(sys, "frozen", False):
        return None
    if sys.platform != "darwin":
        return None

    try:
        from Foundation import NSBundle  # type: ignore[import-not-found]

        version = NSBundle.mainBundle().objectForInfoDictionaryKey_(
            "CFBundleShortVersionString"
        )
        if version:
            return str(version)
    except Exception:
        return None
    return None


def _default_project_root() -> Path:
    if getattr(sys, "frozen", False):
        executable = getattr(sys, "executable", "")
        if executable:
            return Path(executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _version_from_pyproject(pyproject_path: Path) -> str | None:
    text = _read_text_safe(pyproject_path)
    if not text:
        return None
    match = re.search(r'^\s*version\s*=\s*"([^"]+)"\s*$', text, flags=re.MULTILINE)
    return match.group(1) if match else None


def _version_from_changelog(changelog_path: Path) -> str | None:
    text = _read_text_safe(changelog_path)
    if not text:
        return None
    match = re.search(r"^##\s+\[([^\]]+)\]", text, flags=re.MULTILINE)
    return match.group(1) if match else None


def _version_from_importlib_metadata() -> str | None:
    try:
        from importlib import metadata
    except Exception:
        return None

    for dist_name in _DIST_VERSION_NAMES:
        try:
            version = metadata.version(dist_name)
        except metadata.PackageNotFoundError:
            continue
        except Exception:
            return None
        if version:
            return str(version)
    return None


def _version_from_windows_executable() -> str | None:
    if sys.platform != "win32":
        return None
    if not getattr(sys, "frozen", False):
        return None

    executable = getattr(sys, "executable", "")
    if not executable:
        return None

    try:
        import win32api  # type: ignore[import-not-found]
    except Exception:
        return None

    try:
        info = win32api.GetFileVersionInfo(executable, "\\")
        ms = int(info["FileVersionMS"])
        ls = int(info["FileVersionLS"])
    except Exception:
        return None

    parts = [
        (ms >> 16) & 0xFFFF,
        ms & 0xFFFF,
        (ls >> 16) & 0xFFFF,
        ls & 0xFFFF,
    ]
    while len(parts) > 3 and parts[-1] == 0:
        parts.pop()
    return ".".join(str(part) for part in parts)


def get_app_version(*, default: str = "unknown", project_root: Path | None = None) -> str:
    """Resolve app version from environment, bundle metadata, or repo files."""
    env_version = _version_from_env()
    if env_version:
        return env_version

    bundle_version = _version_from_bundle()
    if bundle_version:
        return bundle_version

    windows_exe_version = _version_from_windows_executable()
    if windows_exe_version:
        return windows_exe_version

    explicit_project_root = project_root is not None
    root = project_root or _default_project_root()
    pyproject_version = _version_from_pyproject(root / "pyproject.toml")
    if pyproject_version:
        return pyproject_version

    changelog_version = _version_from_changelog(root / "CHANGELOG.md")
    if changelog_version:
        return changelog_version

    # When callers explicitly point us at another project root, falling back to
    # the current interpreter's installed package metadata would leak an
    # unrelated version and make tests/build checks depend on local artifacts.
    if explicit_project_root:
        return default

    metadata_version = _version_from_importlib_metadata()
    if metadata_version:
        return metadata_version

    return default
