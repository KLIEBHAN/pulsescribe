"""Persistente Einstellungen für PulseScribe.

Speichert User-Preferences in ~/.pulsescribe/preferences.json.
API-Keys werden in ~/.pulsescribe/.env gespeichert.
"""

import json
import logging
from pathlib import Path

from config import USER_CONFIG_DIR

logger = logging.getLogger("pulsescribe")
from utils.onboarding import (
    OnboardingChoice,
    OnboardingStep,
    coerce_onboarding_choice,
    coerce_onboarding_step,
)

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


def get_onboarding_step() -> OnboardingStep:
    """Aktueller Wizard-Step (persistiert).

    Backwards compat:
      - Wenn `onboarding_step` noch nicht existiert, aber `has_seen_onboarding=True`,
        behandeln wir den Wizard als abgeschlossen, damit bestehende Nutzer nicht
        plötzlich wieder im Wizard landen.
    """
    prefs = load_preferences()
    raw = prefs.get("onboarding_step")
    step = coerce_onboarding_step(str(raw)) if raw is not None else None
    if step is not None:
        return step
    if prefs.get("has_seen_onboarding", False):
        return OnboardingStep.DONE
    return OnboardingStep.CHOOSE_GOAL


def set_onboarding_step(step: OnboardingStep | str) -> None:
    """Setzt den aktuellen Wizard-Step."""
    raw = step.value if isinstance(step, OnboardingStep) else str(step)
    normalized = coerce_onboarding_step(raw) or OnboardingStep.DONE
    prefs = load_preferences()
    prefs["onboarding_step"] = normalized.value
    # Completion implies "seen".
    if normalized == OnboardingStep.DONE:
        prefs["has_seen_onboarding"] = True
    save_preferences(prefs)


def get_onboarding_choice() -> OnboardingChoice | None:
    """Letzte Wizard-Auswahl (fast/private/advanced)."""
    raw = load_preferences().get("onboarding_choice")
    return coerce_onboarding_choice(str(raw)) if raw is not None else None


def set_onboarding_choice(choice: OnboardingChoice | str | None) -> None:
    """Speichert die Wizard-Auswahl oder löscht sie."""
    prefs = load_preferences()
    if choice is None:
        prefs.pop("onboarding_choice", None)
        save_preferences(prefs)
        return
    normalized = (
        choice
        if isinstance(choice, OnboardingChoice)
        else coerce_onboarding_choice(str(choice))
    )
    if normalized is None:
        prefs.pop("onboarding_choice", None)
    else:
        prefs["onboarding_choice"] = normalized.value
    save_preferences(prefs)


def is_onboarding_complete() -> bool:
    """True wenn Wizard abgeschlossen UND .env existiert.

    Wenn .env fehlt (User hat es gelöscht oder Fresh Install),
    behandeln wir das Onboarding als nicht abgeschlossen - auch wenn
    preferences.json 'done' sagt. So startet der Wizard erneut.
    """
    if not env_file_exists():
        return False
    return get_onboarding_step() == OnboardingStep.DONE


def env_file_exists() -> bool:
    """True wenn die .env Datei existiert."""
    return ENV_FILE.exists()


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

    Raises:
        OSError: Bei Schreibfehlern (Disk voll, keine Berechtigung)
    """
    env_path = ENV_FILE

    lines = []
    try:
        if env_path.exists():
            lines = env_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        logger.exception("Konnte .env nicht lesen")
        raise

    # Key aktualisieren oder hinzufügen
    found = False
    for i, line in enumerate(lines):
        if line.startswith(f"{key_name}="):
            lines[i] = f"{key_name}={value}"
            found = True
            break

    if not found:
        lines.append(f"{key_name}={value}")

    try:
        env_path.parent.mkdir(parents=True, exist_ok=True)
        env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        # Sichere Permissions: Nur Owner lesen/schreiben (enthält API-Keys)
        try:
            env_path.chmod(0o600)
        except OSError:
            pass  # Windows unterstützt chmod nicht vollständig
        # Cache erst nach erfolgreichem Schreiben invalidieren
        _invalidate_env_cache()
    except OSError:
        logger.exception("Konnte .env nicht schreiben")
        raise


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
        key_name: Name der Einstellung (z.B. "PULSESCRIBE_MODE")

    Returns:
        Der Wert oder None wenn nicht gefunden
    """
    return read_env_file().get(key_name)


def save_env_setting(key_name: str, value: str) -> None:
    """Speichert/aktualisiert eine Einstellung in der .env Datei.

    Args:
        key_name: Name der Einstellung (z.B. "PULSESCRIBE_MODE")
        value: Der Wert
    """
    save_api_key(key_name, value)  # Gleiche Logik wie bei API-Keys


def remove_env_setting(key_name: str) -> None:
    """Entfernt eine Einstellung aus der .env Datei.

    Args:
        key_name: Name der Einstellung (z.B. "PULSESCRIBE_REFINE")
    """
    env_path = ENV_FILE

    if not env_path.exists():
        return

    try:
        lines = env_path.read_text(encoding="utf-8").splitlines()
        new_lines = [line for line in lines if not line.startswith(f"{key_name}=")]
        env_path.write_text("\n".join(new_lines) + "\n" if new_lines else "", encoding="utf-8")
        # Cache erst nach erfolgreichem Schreiben invalidieren
        _invalidate_env_cache()
    except OSError:
        logger.warning("Konnte .env nicht aktualisieren", exc_info=True)


def apply_hotkey_setting(kind: str, hotkey_str: str) -> None:
    """Speichert Toggle/Hold Hotkey und entfernt Legacy Keys.

    `kind` ist "toggle" oder "hold". Die jeweils andere Konfiguration bleibt unverändert.
    """
    value = (hotkey_str or "").strip().lower()
    if not value:
        return

    if kind == "hold":
        save_env_setting("PULSESCRIBE_HOLD_HOTKEY", value)
    else:
        save_env_setting("PULSESCRIBE_TOGGLE_HOTKEY", value)

    # Remove legacy single-hotkey keys if present.
    remove_env_setting("PULSESCRIBE_HOTKEY")
    remove_env_setting("PULSESCRIBE_HOTKEY_MODE")
