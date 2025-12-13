"""Persistente Einstellungen für WhisperGo.

Speichert User-Preferences in ~/.whisper_go/preferences.json.
API-Keys werden in ~/.whisper_go/.env gespeichert.
"""

import json
from pathlib import Path

from config import USER_CONFIG_DIR

PREFS_FILE = USER_CONFIG_DIR / "preferences.json"
ENV_FILE = USER_CONFIG_DIR / ".env"

# Cache: (mtime, values)
_env_cache: tuple[float, dict[str, str]] | None = None


def read_env_file(path: Path | None = None) -> dict[str, str]:
    """Liest eine .env Datei und gibt ein Key→Value Dict zurück.

    - Ignoriert leere Zeilen und Kommentare (# …)
    - Behält die erste Definition eines Keys (entspricht get_api_key/get_env_setting Verhalten)
    - Cached anhand mtime, damit UI-Reads nicht ständig die Datei neu parsen
    """
    global _env_cache

    env_path = path or ENV_FILE
    try:
        mtime = env_path.stat().st_mtime
    except FileNotFoundError:
        _env_cache = (0.0, {})
        return {}
    except OSError:
        _env_cache = (0.0, {})
        return {}

    if path is None and _env_cache is not None and _env_cache[0] == mtime:
        return dict(_env_cache[1])

    values: dict[str, str] = {}
    try:
        for raw_line in env_path.read_text().splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            if not key or key in values:
                continue
            values[key] = value.strip()
    except OSError:
        values = {}

    if path is None:
        _env_cache = (mtime, values)
    return dict(values)


def _invalidate_env_cache() -> None:
    global _env_cache
    _env_cache = None


def load_preferences() -> dict:
    """Lädt Preferences aus JSON."""
    if PREFS_FILE.exists():
        try:
            return json.loads(PREFS_FILE.read_text())
        except json.JSONDecodeError:
            return {}
    return {}


def save_preferences(prefs: dict) -> None:
    """Speichert Preferences als JSON."""
    PREFS_FILE.parent.mkdir(parents=True, exist_ok=True)
    PREFS_FILE.write_text(json.dumps(prefs, indent=2))


def has_seen_onboarding() -> bool:
    """Prüft ob User das Onboarding bereits gesehen hat."""
    return load_preferences().get("has_seen_onboarding", False)


def set_onboarding_seen(seen: bool = True) -> None:
    """Markiert Onboarding als gesehen."""
    prefs = load_preferences()
    prefs["has_seen_onboarding"] = seen
    save_preferences(prefs)


def get_show_welcome_on_startup() -> bool:
    """Prüft ob Welcome-Window bei jedem Start gezeigt werden soll."""
    return load_preferences().get("show_welcome_on_startup", True)


def set_show_welcome_on_startup(show: bool) -> None:
    """Setzt ob Welcome-Window bei jedem Start gezeigt werden soll."""
    prefs = load_preferences()
    prefs["show_welcome_on_startup"] = show
    save_preferences(prefs)


def save_api_key(key_name: str, value: str) -> None:
    """Speichert/aktualisiert einen API-Key in der .env Datei.

    Args:
        key_name: Name des Keys (z.B. "DEEPGRAM_API_KEY")
        value: Der API-Key Wert
    """
    env_path = ENV_FILE

    lines = []
    if env_path.exists():
        lines = env_path.read_text().splitlines()

    # Key aktualisieren oder hinzufügen
    found = False
    for i, line in enumerate(lines):
        if line.startswith(f"{key_name}="):
            lines[i] = f"{key_name}={value}"
            found = True
            break

    if not found:
        lines.append(f"{key_name}={value}")

    env_path.write_text("\n".join(lines) + "\n")
    _invalidate_env_cache()


def get_api_key(key_name: str) -> str | None:
    """Liest einen API-Key aus der .env Datei.

    Args:
        key_name: Name des Keys (z.B. "DEEPGRAM_API_KEY")

    Returns:
        Der API-Key Wert oder None wenn nicht gefunden
    """
    return read_env_file().get(key_name)


def get_env_setting(key_name: str) -> str | None:
    """Liest eine Einstellung aus der .env Datei.

    Args:
        key_name: Name der Einstellung (z.B. "WHISPER_GO_MODE")

    Returns:
        Der Wert oder None wenn nicht gefunden
    """
    return read_env_file().get(key_name)


def save_env_setting(key_name: str, value: str) -> None:
    """Speichert/aktualisiert eine Einstellung in der .env Datei.

    Args:
        key_name: Name der Einstellung (z.B. "WHISPER_GO_MODE")
        value: Der Wert
    """
    save_api_key(key_name, value)  # Gleiche Logik wie bei API-Keys


def remove_env_setting(key_name: str) -> None:
    """Entfernt eine Einstellung aus der .env Datei.

    Args:
        key_name: Name der Einstellung (z.B. "WHISPER_GO_REFINE")
    """
    env_path = ENV_FILE

    if not env_path.exists():
        return

    lines = env_path.read_text().splitlines()
    new_lines = [line for line in lines if not line.startswith(f"{key_name}=")]

    env_path.write_text("\n".join(new_lines) + "\n" if new_lines else "")
    _invalidate_env_cache()
