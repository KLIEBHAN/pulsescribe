"""Helpers for resolving the current PulseScribe app version."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

_VERSION_ENV_KEYS = ("PULSESCRIBE_VERSION", "WHISPERGO_VERSION", "VERSION")


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


def get_app_version(*, default: str = "unknown", project_root: Path | None = None) -> str:
    """Resolve app version from environment, bundle metadata, or repo files."""
    env_version = _version_from_env()
    if env_version:
        return env_version

    bundle_version = _version_from_bundle()
    if bundle_version:
        return bundle_version

    root = project_root or Path(__file__).resolve().parent.parent
    pyproject_version = _version_from_pyproject(root / "pyproject.toml")
    if pyproject_version:
        return pyproject_version

    changelog_version = _version_from_changelog(root / "CHANGELOG.md")
    if changelog_version:
        return changelog_version

    return default
