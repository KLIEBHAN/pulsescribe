"""Persistente Einstellungen für PulseScribe.

Speichert User-Preferences in ~/.pulsescribe/preferences.json.
API-Keys werden in ~/.pulsescribe/.env gespeichert.
"""

import json
import logging
import os
import tempfile
from io import StringIO
from pathlib import Path

from config import USER_CONFIG_DIR

from utils.env import (
    parse_env_line as _shared_parse_env_line,
    read_env_file_values,
)
from utils.onboarding import (
    OnboardingChoice,
    OnboardingStep,
    coerce_onboarding_choice,
    coerce_onboarding_step,
)

logger = logging.getLogger("pulsescribe")

PREFS_FILE = USER_CONFIG_DIR / "preferences.json"
ENV_FILE = USER_CONFIG_DIR / ".env"

# Cache: ((mtime_ns, size, ctime_ns), values)
_env_cache: tuple[tuple[int, int, int], dict[str, str]] | None = None
_prefs_cache: tuple[tuple[int, int, int], dict[str, object]] | None = None


def _build_file_signature(path: Path) -> tuple[int, int, int] | None:
    """Return a stable file signature for lightweight cache invalidation."""
    try:
        stat_result = path.stat()
    except FileNotFoundError:
        return None
    except OSError:
        return None

    return (
        int(
            getattr(
                stat_result,
                "st_mtime_ns",
                int(getattr(stat_result, "st_mtime", 0.0) * 1_000_000_000),
            )
        ),
        int(getattr(stat_result, "st_size", 0)),
        int(
            getattr(
                stat_result,
                "st_ctime_ns",
                int(getattr(stat_result, "st_ctime", 0.0) * 1_000_000_000),
            )
        ),
    )


def _parse_env_line(raw_line: str) -> tuple[str | None, str | None]:
    """Parst eine einzelne `.env`-Zeile möglichst dotenv-kompatibel.

    Beibehaltung der bestehenden Semantik:
    - leere Zeilen / Kommentare ignorieren
    - nur genau ein Key-Value-Paar pro Zeile
    - weitere Duplikate werden vom Aufrufer verworfen
    """
    line = raw_line.strip()
    if not line or line.startswith("#") or "=" not in line:
        return None, None

    try:
        from dotenv import dotenv_values  # type: ignore[import-not-found]

        parsed = dotenv_values(stream=StringIO(f"{raw_line}\n"))
    except Exception:
        return _shared_parse_env_line(raw_line)

    for key, value in parsed.items():
        normalized_key = str(key or "").strip()
        if not normalized_key:
            continue
        return normalized_key, "" if value is None else str(value).strip()

    return None, None


def read_env_file(path: Path | None = None) -> dict[str, str]:
    """Liest eine .env Datei und gibt ein Key→Value Dict zurück.

    - Ignoriert leere Zeilen und Kommentare (# …)
    - Behält die erste Definition eines Keys (entspricht get_api_key/get_env_setting Verhalten)
    - Cached anhand mtime, damit UI-Reads nicht ständig die Datei neu parsen
    """
    global _env_cache

    env_path = path or ENV_FILE
    signature = _build_file_signature(env_path)
    if signature is None:
        _env_cache = ((0, 0, 0), {})
        return {}

    if path is None and _env_cache is not None and _env_cache[0] == signature:
        return dict(_env_cache[1])

    values = read_env_file_values(
        env_path,
        encoding="utf-8",
        errors="replace",
        first_wins=True,
    )

    if path is None:
        _env_cache = (signature, values)
    return dict(values)


def _env_line_has_key(raw_line: str, key_name: str) -> tuple[bool, str | None]:
    """Return whether a raw `.env` line defines ``key_name`` and its parsed value."""
    key, value = _parse_env_line(raw_line)
    return key == key_name, value


def _set_first_env_line(
    lines: list[str],
    key_name: str,
    value: str,
) -> tuple[list[str], bool]:
    """Set the first matching assignment while preserving later duplicates."""
    for index, line in enumerate(lines):
        matches_key, existing_value = _env_line_has_key(line, key_name)
        if not matches_key:
            continue
        if existing_value == value:
            return list(lines), False
        new_lines = list(lines)
        new_lines[index] = f"{key_name}={value}"
        return new_lines, True

    return [*lines, f"{key_name}={value}"], True


def _apply_env_updates_to_lines(
    lines: list[str],
    updates: dict[str, str | None],
) -> tuple[list[str], bool]:
    """Apply batched `.env` updates while preserving unrelated lines."""
    handled_keys: set[str] = set()
    new_lines: list[str] = []
    changed = False

    for line in lines:
        key, _existing_value = _parse_env_line(line)
        if not key or key not in updates:
            new_lines.append(line)
            continue

        # Collapse duplicate entries for keys we are explicitly updating.
        if key in handled_keys:
            changed = True
            continue

        handled_keys.add(key)
        new_value = updates[key]
        if new_value is None:
            changed = True
            continue

        canonical_line = f"{key}={new_value}"
        if line != canonical_line:
            changed = True
        new_lines.append(canonical_line)

    for key, value in updates.items():
        if key in handled_keys or value is None:
            continue
        new_lines.append(f"{key}={value}")
        changed = True

    return new_lines, changed


def _invalidate_env_cache() -> None:
    global _env_cache
    _env_cache = None


def _invalidate_preferences_cache() -> None:
    global _prefs_cache
    _prefs_cache = None


def _read_existing_env_lines(env_path: Path) -> list[str]:
    """Read `.env` lines while tolerating a missing file."""
    if not env_path.exists():
        return []
    return env_path.read_text(encoding="utf-8").splitlines()


def _write_env_lines(env_path: Path, lines: list[str]) -> None:
    """Persist `.env` lines atomically and refresh the cache afterwards."""
    _write_text_atomic(
        env_path,
        "\n".join(lines) + "\n" if lines else "",
        encoding="utf-8",
    )
    try:
        env_path.chmod(0o600)
    except OSError:
        pass  # Windows unterstützt chmod nicht vollständig
    _invalidate_env_cache()


def _write_text_atomic(path: Path, content: str, *, encoding: str = "utf-8") -> None:
    """Schreibt Text atomar, um truncierte Config-Dateien zu vermeiden."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        tmp_path.write_text(content, encoding=encoding)
        tmp_path.replace(path)
    except Exception:
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise


def load_preferences() -> dict:
    """Lädt Preferences aus JSON."""
    global _prefs_cache

    signature = _build_file_signature(PREFS_FILE)
    if signature is None:
        _prefs_cache = ((0, 0, 0), {})
        return {}

    if _prefs_cache is not None and _prefs_cache[0] == signature:
        return dict(_prefs_cache[1])

    try:
        data = json.loads(PREFS_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        data = {}

    values = data if isinstance(data, dict) else {}
    _prefs_cache = (signature, values)
    return dict(values)


def save_preferences(prefs: dict) -> None:
    """Speichert Preferences als JSON."""
    _write_text_atomic(PREFS_FILE, json.dumps(prefs, indent=2), encoding="utf-8")
    _invalidate_preferences_cache()


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

    try:
        lines = _read_existing_env_lines(env_path)
    except OSError:
        logger.exception("Konnte .env nicht lesen")
        raise

    lines, changed = _set_first_env_line(lines, key_name, value)
    if not changed:
        # Externe Updates koennen bei grober FS-Metadatenauflösung den Cache
        # unverändert wirken lassen, obwohl Nachbar-Keys angepasst wurden.
        _invalidate_env_cache()
        return

    try:
        _write_env_lines(env_path, lines)
    except OSError:
        logger.exception("Konnte .env nicht schreiben")
        raise


def set_api_key(key_name: str, value: str | None) -> bool:
    """Setzt oder entfernt einen API-Key.

    Returns:
        True wenn ein nicht-leerer Key gespeichert wurde, sonst False (Key entfernt).
    """
    normalized = (value or "").strip()
    if not normalized:
        remove_env_setting(key_name)
        return False

    save_api_key(key_name, normalized)
    return True


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


def update_env_settings(updates: dict[str, str | None]) -> None:
    """Apply multiple `.env` mutations in a single read/write pass.

    ``None`` removes the key, any other value stores ``KEY=value`` in canonical form.
    Unrelated lines are preserved, while duplicate definitions of updated keys are
    collapsed to a single canonical line.
    """

    if not updates:
        return

    env_path = ENV_FILE
    try:
        lines = _read_existing_env_lines(env_path)
    except OSError:
        logger.exception("Konnte .env nicht lesen")
        raise

    new_lines, changed = _apply_env_updates_to_lines(lines, updates)

    if not changed:
        _invalidate_env_cache()
        return

    try:
        _write_env_lines(env_path, new_lines)
    except OSError:
        logger.exception("Konnte .env nicht aktualisieren")
        raise


def remove_env_setting(key_name: str) -> None:
    """Entfernt eine Einstellung aus der .env Datei.

    Args:
        key_name: Name der Einstellung (z.B. "PULSESCRIBE_REFINE")
    """
    env_path = ENV_FILE

    if not env_path.exists():
        return

    try:
        lines = _read_existing_env_lines(env_path)
        new_lines, changed = _apply_env_updates_to_lines(lines, {key_name: None})
        if not changed:
            return
        _write_env_lines(env_path, new_lines)
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
