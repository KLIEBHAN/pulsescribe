"""Diagnostics export for PulseScribe (no audio).

Creates a zip archive with:
- system/app info
- sanitized .env (API keys masked)
- preferences.json (if present)
- redacted log tail (no transcripts)

This is intended for user-support without leaking sensitive data.
"""

from __future__ import annotations

import json
import platform
import re
import subprocess
import sys
import time
import zipfile
from pathlib import Path

from utils.env import read_env_file_values
from utils.version import get_app_version

_REDACTED_LOG_MARKERS = (
    "Auto-Paste:",
    "✓ Text eingefügt:",
    "Transkript:",
    "Ergebnis:",
    "] Final:",
    "] Interim:",
    "] Output:",
    "Transcript saved to history:",
)


def _user_config_dir() -> Path:
    return Path.home() / ".pulsescribe"


def _read_text_safe(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _tail_lines(text: str, max_lines: int) -> str:
    if not text:
        return ""
    lines = text.splitlines()
    if len(lines) <= max_lines:
        return "\n".join(lines) + "\n"
    return "\n".join(lines[-max_lines:]) + "\n"


def _mask_secret(value: str) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    if len(raw) <= 8:
        return "********"
    return f"{raw[:2]}…{raw[-4:]}"


def _is_sensitive_key(key: str) -> bool:
    k = key.upper()
    return (
        k.endswith("_API_KEY")
        or "API_KEY" in k
        or "TOKEN" in k
        or "SECRET" in k
        or "PASSWORD" in k
    )


def _read_env_file(path: Path) -> dict[str, str]:
    return read_env_file_values(
        path,
        encoding="utf-8",
        errors="replace",
        first_wins=True,
    )


def _sanitize_env(env: dict[str, str]) -> dict[str, str]:
    sanitized: dict[str, str] = {}
    for k, v in env.items():
        sanitized[k] = _mask_secret(v) if _is_sensitive_key(k) else v
    return sanitized


def _redact_after_marker(line: str, marker: str) -> str:
    newline = "\n" if line.endswith("\n") else ""
    body = line[:-1] if newline else line
    prefix, sep, _rest = body.partition(marker)
    if not sep:
        return line
    return f"{prefix}{sep} <redacted>{newline}"


def _redact_log_line(line: str) -> str:
    # Remove transcript previews from logs.
    for marker in _REDACTED_LOG_MARKERS:
        if marker in line:
            return _redact_after_marker(line, marker)
    if " text='" in line:
        return re.sub(r"text='.*?'(?=\s|$)", "text='<redacted>'", line)
    return line


def _redact_log_text(text: str) -> str:
    return "".join(_redact_log_line(line + "\n") for line in text.splitlines())


def _get_app_version() -> str:
    return get_app_version(default="unknown")


def _load_preferences_payload(path: Path) -> dict:
    """Read preferences JSON defensively for diagnostics export."""
    if not path.exists():
        return {}

    try:
        prefs = json.loads(_read_text_safe(path) or "{}")
    except json.JSONDecodeError:
        return {}

    return prefs if isinstance(prefs, dict) else {}


def _build_report(
    *,
    config_dir: Path,
    log_path: Path,
    env_values: dict[str, str],
    prefs: dict,
) -> dict[str, object]:
    """Build the structured diagnostics report payload."""
    return {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "app": {
            "name": "PulseScribe",
            "version": _get_app_version(),
            "frozen": bool(getattr(sys, "frozen", False)),
        },
        "system": {
            "platform": platform.platform(),
            "macos": platform.mac_ver()[0],
            "machine": platform.machine(),
            "python": sys.version,
            "executable": sys.executable,
        },
        "settings": {
            "env_sanitized": env_values,
            "preferences": prefs,
        },
        "paths": {
            "config_dir": str(config_dir),
            "log_file": str(log_path),
        },
    }


def _read_redacted_log_tail(path: Path, *, max_lines: int) -> str:
    """Return a redacted tail for one optional log file."""
    if not path.exists():
        return ""
    return _tail_lines(_redact_log_text(_read_text_safe(path)), max_lines=max_lines)


def _dump_json(data: object) -> str:
    """Serialize diagnostics JSON payloads consistently."""
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def _iter_archive_entries(
    *,
    report: dict[str, object],
    env_values: dict[str, str],
    prefs: dict,
    log_tail: str,
    startup_tail: str,
):
    """Yield archive members while skipping empty optional payloads."""
    yield "report.json", _dump_json(report)
    if env_values:
        yield "env_sanitized.json", _dump_json(env_values)
    if prefs:
        yield "preferences.json", _dump_json(prefs)
    if log_tail:
        yield "logs/pulsescribe.log.tail.txt", log_tail
    if startup_tail:
        yield "logs/startup.log.tail.txt", startup_tail


def export_diagnostics_report() -> Path:
    """Create a diagnostics zip and reveal it in Finder (best-effort)."""
    cfg = _user_config_dir()
    cfg.mkdir(parents=True, exist_ok=True)
    out_dir = cfg / "diagnostics"
    out_dir.mkdir(parents=True, exist_ok=True)

    ts = time.strftime("%Y%m%d_%H%M%S")
    zip_path = out_dir / f"pulsescribe_diagnostics_{ts}.zip"

    env_path = cfg / ".env"
    prefs_path = cfg / "preferences.json"
    log_path = cfg / "logs" / "pulsescribe.log"
    startup_log_path = cfg / "startup.log"

    env_values = _sanitize_env(_read_env_file(env_path)) if env_path.exists() else {}
    prefs = _load_preferences_payload(prefs_path)
    report = _build_report(
        config_dir=cfg,
        log_path=log_path,
        env_values=env_values,
        prefs=prefs,
    )

    log_tail = _read_redacted_log_tail(log_path, max_lines=800)
    startup_tail = _read_redacted_log_tail(startup_log_path, max_lines=200)

    try:
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for archive_path, content in _iter_archive_entries(
                report=report,
                env_values=env_values,
                prefs=prefs,
                log_tail=log_tail,
                startup_tail=startup_tail,
            ):
                zf.writestr(archive_path, content)
    except OSError:
        # Remove broken/incomplete zip so the caller never receives a corrupt file.
        try:
            zip_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise

    # Reveal in Finder (best-effort)
    try:
        subprocess.Popen(["open", "-R", str(zip_path)])
    except Exception:
        pass

    return zip_path
