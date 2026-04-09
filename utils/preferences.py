"""Persistente Einstellungen für PulseScribe.

Speichert User-Preferences in ~/.pulsescribe/preferences.json.
API-Keys werden in ~/.pulsescribe/.env gespeichert.
"""

import json
import logging
import os
import tempfile
from collections.abc import Callable
from pathlib import Path

from config import USER_CONFIG_DIR

from utils.env import parse_env_line_with_dotenv, read_env_file_values
from utils.file_signatures import FileSignature, build_file_signature
from utils.onboarding import (
    OnboardingChoice,
    OnboardingStep,
    coerce_onboarding_choice,
    coerce_onboarding_step,
)

logger = logging.getLogger("pulsescribe")

PREFS_FILE = USER_CONFIG_DIR / "preferences.json"
ENV_FILE = USER_CONFIG_DIR / ".env"
_HOTKEY_ENV_KEYS = {
    "toggle": "PULSESCRIBE_TOGGLE_HOTKEY",
    "hold": "PULSESCRIBE_HOLD_HOTKEY",
}
_LEGACY_HOTKEY_ENV_KEYS = (
    "PULSESCRIBE_HOTKEY",
    "PULSESCRIBE_HOTKEY_MODE",
)

# Cache: ((mtime_ns, size, ctime_ns), values)
_env_cache: tuple[FileSignature, dict[str, str]] | None = None
_prefs_cache: tuple[FileSignature, dict[str, object]] | None = None


def _build_file_signature(path: Path) -> FileSignature | None:
    """Return a stable file signature for lightweight cache invalidation."""
    try:
        return build_file_signature(path)
    except FileNotFoundError:
        return None
    except OSError:
        return None


def _parse_env_line(raw_line: str) -> tuple[str | None, str | None]:
    """Parst eine einzelne `.env`-Zeile möglichst dotenv-kompatibel."""
    return parse_env_line_with_dotenv(raw_line)


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


def _canonical_env_line(key_name: str, value: str) -> str:
    return f"{key_name}={value}"


def _set_first_env_line(
    lines: list[str],
    key_name: str,
    value: str,
) -> tuple[list[str], bool]:
    """Set the first matching assignment while preserving later duplicates."""
    canonical_line = _canonical_env_line(key_name, value)

    for index, line in enumerate(lines):
        parsed_key, existing_value = _parse_env_line(line)
        if parsed_key != key_name:
            continue
        if existing_value == value:
            return list(lines), False
        new_lines = list(lines)
        new_lines[index] = canonical_line
        return new_lines, True

    return [*lines, canonical_line], True


def _apply_env_updates_to_lines(
    lines: list[str],
    updates: dict[str, str | None],
) -> tuple[list[str], bool]:
    """Apply batched `.env` updates while preserving unrelated lines."""
    canonical_updates = {
        key: None if value is None else _canonical_env_line(key, value)
        for key, value in updates.items()
    }
    handled_keys: set[str] = set()
    new_lines: list[str] = []
    changed = False

    for line in lines:
        key, _existing_value = _parse_env_line(line)
        if not key or key not in canonical_updates:
            new_lines.append(line)
            continue

        # Collapse duplicate entries for keys we are explicitly updating.
        if key in handled_keys:
            changed = True
            continue

        handled_keys.add(key)
        canonical_line = canonical_updates[key]
        if canonical_line is None:
            changed = True
            continue

        if line != canonical_line:
            changed = True
        new_lines.append(canonical_line)

    for key, canonical_line in canonical_updates.items():
        if key in handled_keys or canonical_line is None:
            continue
        new_lines.append(canonical_line)
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



def _update_env_file(
    env_path: Path,
    *,
    apply_changes: Callable[[list[str]], tuple[list[str], bool]],
    read_error_message: str,
    write_error_message: str,
    invalidate_cache_on_noop: bool = False,
    suppress_errors: bool = False,
) -> bool:
    """Liest, mutiert und schreibt `.env`-Zeilen mit konsistentem Fehlerverhalten."""
    try:
        lines = _read_existing_env_lines(env_path)
    except OSError:
        if suppress_errors:
            logger.warning(read_error_message, exc_info=True)
            return False
        logger.exception(read_error_message)
        raise

    new_lines, changed = apply_changes(lines)
    if not changed:
        if invalidate_cache_on_noop:
            _invalidate_env_cache()
        return False

    try:
        _write_env_lines(env_path, new_lines)
    except OSError:
        if suppress_errors:
            logger.warning(write_error_message, exc_info=True)
            return False
        logger.exception(write_error_message)
        raise

    return True



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


def _mutate_preferences(mutator: Callable[[dict[str, object]], None]) -> None:
    """Load, mutate and persist preferences in one shared code path."""
    prefs = load_preferences()
    mutator(prefs)
    save_preferences(prefs)


def _store_preference(key: str, value: object) -> None:
    """Persist one preference key while preserving all unrelated values."""

    def _apply(prefs: dict[str, object]) -> None:
        prefs[key] = value

    _mutate_preferences(_apply)


def has_seen_onboarding() -> bool:
    """Prüft ob User das Onboarding bereits gesehen hat."""
    return load_preferences().get("has_seen_onboarding", False)


def set_onboarding_seen(seen: bool = True) -> None:
    """Markiert Onboarding als gesehen."""
    _store_preference("has_seen_onboarding", seen)


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

    def _apply(prefs: dict[str, object]) -> None:
        prefs["onboarding_step"] = normalized.value
        # Completion implies "seen".
        if normalized == OnboardingStep.DONE:
            prefs["has_seen_onboarding"] = True

    _mutate_preferences(_apply)


def get_onboarding_choice() -> OnboardingChoice | None:
    """Letzte Wizard-Auswahl (fast/private/advanced)."""
    raw = load_preferences().get("onboarding_choice")
    return coerce_onboarding_choice(str(raw)) if raw is not None else None


def set_onboarding_choice(choice: OnboardingChoice | str | None) -> None:
    """Speichert die Wizard-Auswahl oder löscht sie."""
    normalized = (
        choice
        if isinstance(choice, OnboardingChoice)
        else coerce_onboarding_choice(str(choice)) if choice is not None else None
    )

    def _apply(prefs: dict[str, object]) -> None:
        if normalized is None:
            prefs.pop("onboarding_choice", None)
            return
        prefs["onboarding_choice"] = normalized.value

    _mutate_preferences(_apply)


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
    _store_preference("show_welcome_on_startup", show)


def _save_first_env_setting(key_name: str, value: str) -> None:
    """Persist one env assignment while preserving later duplicates for that key."""
    _update_env_file(
        ENV_FILE,
        apply_changes=lambda lines: _set_first_env_line(lines, key_name, value),
        read_error_message="Konnte .env nicht lesen",
        write_error_message="Konnte .env nicht schreiben",
        invalidate_cache_on_noop=True,
    )


def save_api_key(key_name: str, value: str) -> None:
    """Speichert/aktualisiert einen API-Key in der .env Datei.

    Args:
        key_name: Name des Keys (z.B. "DEEPGRAM_API_KEY")
        value: Der API-Key Wert

    Raises:
        OSError: Bei Schreibfehlern (Disk voll, keine Berechtigung)
    """
    _save_first_env_setting(key_name, value)


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


def _get_env_value(key_name: str) -> str | None:
    """Read one value from the cached `.env` mapping."""
    return read_env_file().get(key_name)


def get_api_key(key_name: str) -> str | None:
    """Liest einen API-Key aus der .env Datei.

    Args:
        key_name: Name des Keys (z.B. "DEEPGRAM_API_KEY")

    Returns:
        Der API-Key Wert oder None wenn nicht gefunden
    """
    return _get_env_value(key_name)


def get_env_setting(key_name: str) -> str | None:
    """Liest eine Einstellung aus der .env Datei.

    Args:
        key_name: Name der Einstellung (z.B. "PULSESCRIBE_MODE")

    Returns:
        Der Wert oder None wenn nicht gefunden
    """
    return _get_env_value(key_name)


def save_env_setting(key_name: str, value: str) -> None:
    """Speichert/aktualisiert eine Einstellung in der .env Datei.

    Args:
        key_name: Name der Einstellung (z.B. "PULSESCRIBE_MODE")
        value: Der Wert
    """
    _save_first_env_setting(key_name, value)


def update_env_settings(updates: dict[str, str | None]) -> None:
    """Apply multiple `.env` mutations in a single read/write pass.

    ``None`` removes the key, any other value stores ``KEY=value`` in canonical form.
    Unrelated lines are preserved, while duplicate definitions of updated keys are
    collapsed to a single canonical line.
    """

    if not updates:
        return

    _update_env_file(
        ENV_FILE,
        apply_changes=lambda lines: _apply_env_updates_to_lines(lines, updates),
        read_error_message="Konnte .env nicht lesen",
        write_error_message="Konnte .env nicht aktualisieren",
        invalidate_cache_on_noop=True,
    )


def remove_env_setting(key_name: str) -> None:
    """Entfernt eine Einstellung aus der .env Datei.

    Args:
        key_name: Name der Einstellung (z.B. "PULSESCRIBE_REFINE")
    """
    env_path = ENV_FILE

    if not env_path.exists():
        return

    _update_env_file(
        env_path,
        apply_changes=lambda lines: _apply_env_updates_to_lines(lines, {key_name: None}),
        read_error_message="Konnte .env nicht aktualisieren",
        write_error_message="Konnte .env nicht aktualisieren",
        suppress_errors=True,
    )


def _build_hotkey_env_updates(kind: str, value: str) -> dict[str, str | None]:
    """Build the env updates for one hotkey change while clearing legacy keys."""
    key_name = _HOTKEY_ENV_KEYS.get(kind, _HOTKEY_ENV_KEYS["toggle"])
    env_updates: dict[str, str | None] = {key_name: value}
    env_updates.update({legacy_key: None for legacy_key in _LEGACY_HOTKEY_ENV_KEYS})
    return env_updates


def apply_hotkey_setting(kind: str, hotkey_str: str) -> None:
    """Speichert Toggle/Hold Hotkey und entfernt Legacy Keys.

    `kind` ist "toggle" oder "hold". Die jeweils andere Konfiguration bleibt unverändert.
    """
    value = (hotkey_str or "").strip().lower()
    if not value:
        return

    update_env_settings(_build_hotkey_env_updates(kind, value))
