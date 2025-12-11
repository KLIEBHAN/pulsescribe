"""Persistente Einstellungen für WhisperGo.

Speichert User-Preferences in ~/.whisper_go/preferences.json.
API-Keys werden in ~/.whisper_go/.env gespeichert.
"""

import json
from pathlib import Path

# User-Verzeichnis direkt definieren, um Circular Import mit config.py zu vermeiden
USER_CONFIG_DIR = Path.home() / ".whisper_go"
PREFS_FILE = USER_CONFIG_DIR / "preferences.json"


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
    env_path = USER_CONFIG_DIR / ".env"

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


def get_api_key(key_name: str) -> str | None:
    """Liest einen API-Key aus der .env Datei.

    Args:
        key_name: Name des Keys (z.B. "DEEPGRAM_API_KEY")

    Returns:
        Der API-Key Wert oder None wenn nicht gefunden
    """
    env_path = USER_CONFIG_DIR / ".env"

    if not env_path.exists():
        return None

    for line in env_path.read_text().splitlines():
        if line.startswith(f"{key_name}="):
            return line.split("=", 1)[1].strip()

    return None


def get_env_setting(key_name: str) -> str | None:
    """Liest eine Einstellung aus der .env Datei.

    Args:
        key_name: Name der Einstellung (z.B. "WHISPER_GO_MODE")

    Returns:
        Der Wert oder None wenn nicht gefunden
    """
    return get_api_key(key_name)  # Gleiche Logik wie bei API-Keys


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
    env_path = USER_CONFIG_DIR / ".env"

    if not env_path.exists():
        return

    lines = env_path.read_text().splitlines()
    new_lines = [line for line in lines if not line.startswith(f"{key_name}=")]

    env_path.write_text("\n".join(new_lines) + "\n" if new_lines else "")
