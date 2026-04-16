"""Persistente Einstellungen für PulseScribe.

Speichert User-Preferences in ~/.pulsescribe/preferences.json.
API-Keys werden in ~/.pulsescribe/.env gespeichert.
"""

import json
import logging
from collections.abc import Callable
from enum import Enum
from pathlib import Path
from typing import TypeVar

from config import USER_CONFIG_DIR

from utils.atomic_io import write_text_atomic
from utils.env import parse_bool, parse_env_line_with_dotenv, read_env_file_values
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
_EMPTY_FILE_SIGNATURE: FileSignature = (0, 0, 0)

TValue = TypeVar("TValue")
TEnum = TypeVar("TEnum", bound=Enum)
TCacheValue = TypeVar("TCacheValue")


def _load_cached_mapping_file(
    path: Path,
    *,
    cache: tuple[FileSignature, dict[str, TCacheValue]] | None,
    loader: Callable[[Path], dict[str, TCacheValue]],
) -> tuple[dict[str, TCacheValue], tuple[FileSignature, dict[str, TCacheValue]]]:
    """Load a small key/value file with shared file-signature cache handling."""
    try:
        signature = build_file_signature(path)
    except (FileNotFoundError, OSError):
        empty_values: dict[str, TCacheValue] = {}
        return {}, (_EMPTY_FILE_SIGNATURE, empty_values)

    if cache is not None and cache[0] == signature:
        return dict(cache[1]), cache

    values = loader(path)
    return dict(values), (signature, values)


def _load_env_values(env_path: Path) -> dict[str, str]:
    return read_env_file_values(
        env_path,
        encoding="utf-8",
        errors="replace",
        first_wins=True,
    )


def read_env_file(path: Path | None = None) -> dict[str, str]:
    """Liest eine .env Datei und gibt ein Key→Value Dict zurück.

    - Ignoriert leere Zeilen und Kommentare (# …)
    - Behält die erste Definition eines Keys (entspricht get_api_key/get_env_setting Verhalten)
    - Cached anhand mtime, damit UI-Reads nicht ständig die Datei neu parsen
    """
    global _env_cache

    env_path = path or ENV_FILE
    if path is not None:
        return _load_env_values(env_path)

    values, _env_cache = _load_cached_mapping_file(
        env_path,
        cache=_env_cache,
        loader=_load_env_values,
    )
    return values


def _canonical_env_updates(
    updates: dict[str, str | None],
) -> dict[str, str | None]:
    """Normalize raw env updates into canonical persisted lines."""
    return {
        key: None if value is None else f"{key}={value}"
        for key, value in updates.items()
    }


def _resolve_updated_env_line(
    line: str,
    *,
    existing_value: str | None,
    requested_value: str | None,
    canonical_line: str | None,
    is_duplicate: bool,
    collapse_handled_duplicates: bool,
) -> tuple[str | None, bool]:
    """Resolve one matching `.env` line into keep/replace/remove plus changed-flag."""
    if is_duplicate:
        if collapse_handled_duplicates or canonical_line is None:
            return None, True
        return line, False

    if canonical_line is None:
        return None, True

    if not collapse_handled_duplicates and existing_value == requested_value:
        return line, False

    return canonical_line, line != canonical_line


def _append_missing_env_updates(
    new_lines: list[str],
    *,
    canonical_updates: dict[str, str | None],
    handled_keys: set[str],
) -> bool:
    """Append updates for keys that were not present in the original file."""
    changed = False
    for key, canonical_line in canonical_updates.items():
        if key in handled_keys or canonical_line is None:
            continue
        new_lines.append(canonical_line)
        changed = True
    return changed


def _apply_env_update_to_line(
    line: str,
    *,
    updates: dict[str, str | None],
    canonical_updates: dict[str, str | None],
    handled_keys: set[str],
    collapse_handled_duplicates: bool,
) -> tuple[str | None, bool]:
    """Apply matching `.env` updates to one raw line while tracking duplicates."""
    key, existing_value = parse_env_line_with_dotenv(line)
    if not key or key not in canonical_updates:
        return line, False

    is_duplicate = key in handled_keys
    handled_keys.add(key)
    return _resolve_updated_env_line(
        line,
        existing_value=existing_value,
        requested_value=updates[key],
        canonical_line=canonical_updates[key],
        is_duplicate=is_duplicate,
        collapse_handled_duplicates=collapse_handled_duplicates,
    )


def _apply_env_updates_to_lines(
    lines: list[str],
    updates: dict[str, str | None],
    *,
    collapse_handled_duplicates: bool = True,
) -> tuple[list[str], bool]:
    """Apply `.env` updates while preserving unrelated lines and order."""
    canonical_updates = _canonical_env_updates(updates)
    handled_keys: set[str] = set()
    new_lines: list[str] = []
    changed = False

    for line in lines:
        updated_line, line_changed = _apply_env_update_to_line(
            line,
            updates=updates,
            canonical_updates=canonical_updates,
            handled_keys=handled_keys,
            collapse_handled_duplicates=collapse_handled_duplicates,
        )
        changed = changed or line_changed
        if updated_line is not None:
            new_lines.append(updated_line)

    changed = _append_missing_env_updates(
        new_lines,
        canonical_updates=canonical_updates,
        handled_keys=handled_keys,
    ) or changed

    return new_lines, changed


def _apply_single_env_update(
    lines: list[str],
    key_name: str,
    value: str | None,
    *,
    preserve_following_duplicates: bool = False,
) -> tuple[list[str], bool]:
    """Apply one `.env` mutation via the shared line-update helpers."""
    return _apply_env_updates_to_lines(
        lines,
        {key_name: value},
        collapse_handled_duplicates=not (
            preserve_following_duplicates and value is not None
        ),
    )


def _invalidate_env_cache() -> None:
    global _env_cache
    _env_cache = None


def _invalidate_preferences_cache() -> None:
    global _prefs_cache
    _prefs_cache = None


def _read_existing_env_lines(env_path: Path) -> list[str]:
    """Read `.env` lines while tolerating missing/partially malformed files."""
    if not env_path.exists():
        return []
    return env_path.read_text(encoding="utf-8", errors="replace").splitlines()


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
    write_text_atomic(path, content, encoding=encoding)


def _load_preferences_values(path: Path) -> dict[str, object]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}

    return data if isinstance(data, dict) else {}


def load_preferences() -> dict:
    """Lädt Preferences aus JSON."""
    global _prefs_cache

    values, _prefs_cache = _load_cached_mapping_file(
        PREFS_FILE,
        cache=_prefs_cache,
        loader=_load_preferences_values,
    )
    return values


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


def _coerce_loaded_preference(
    raw_value: object | None,
    coercer: Callable[[str | None], TValue | None],
) -> TValue | None:
    """Normalize a persisted preference value via a shared coercion path."""
    if raw_value is None:
        return None
    return coercer(str(raw_value))


def _get_normalized_preference(
    key: str,
    *,
    coercer: Callable[[str | None], TValue | None],
) -> TValue | None:
    """Load and coerce one stored preference via the shared JSON cache path."""
    return _coerce_loaded_preference(load_preferences().get(key), coercer)


def _normalize_preference_input(
    value: TEnum | str | None,
    *,
    enum_type: type[TEnum],
    coercer: Callable[[str | None], TEnum | None],
) -> TEnum | None:
    """Accept enum instances or raw strings and coerce them consistently."""
    if isinstance(value, enum_type):
        return value
    return _coerce_loaded_preference(value, coercer)


def _set_normalized_preference(
    key: str,
    value: TEnum | str | None,
    *,
    enum_type: type[TEnum],
    coercer: Callable[[str | None], TEnum | None],
    fallback: TEnum | None = None,
    after_set: Callable[[dict[str, object], TEnum | None], None] | None = None,
) -> TEnum | None:
    """Normalize one enum-like preference and persist it through one shared path."""
    normalized = _normalize_preference_input(
        value,
        enum_type=enum_type,
        coercer=coercer,
    )
    if normalized is None:
        normalized = fallback

    def _apply(prefs: dict[str, object]) -> None:
        if normalized is None:
            prefs.pop(key, None)
        else:
            prefs[key] = normalized.value
        if after_set is not None:
            after_set(prefs, normalized)

    _mutate_preferences(_apply)
    return normalized


def _coerce_bool_preference(raw_value: object | None, *, default: bool) -> bool:
    """Normalize persisted bool-like values from JSON or manual edits."""
    if isinstance(raw_value, bool):
        return raw_value
    if raw_value is None:
        return default
    parsed = parse_bool(str(raw_value))
    return default if parsed is None else parsed


def _get_bool_preference(key: str, *, default: bool) -> bool:
    """Read one stored bool-like preference with sensible fallbacks."""
    return _coerce_bool_preference(load_preferences().get(key), default=default)


def has_seen_onboarding() -> bool:
    """Prüft ob User das Onboarding bereits gesehen hat."""
    return _get_bool_preference("has_seen_onboarding", default=False)


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
    step = _coerce_loaded_preference(
        prefs.get("onboarding_step"),
        coerce_onboarding_step,
    )
    if step is not None:
        return step
    if _coerce_bool_preference(prefs.get("has_seen_onboarding"), default=False):
        return OnboardingStep.DONE
    return OnboardingStep.CHOOSE_GOAL


def set_onboarding_step(step: OnboardingStep | str) -> None:
    """Setzt den aktuellen Wizard-Step."""

    def _mark_seen_when_done(
        prefs: dict[str, object],
        normalized: OnboardingStep | None,
    ) -> None:
        # Completion implies "seen".
        if normalized == OnboardingStep.DONE:
            prefs["has_seen_onboarding"] = True

    _set_normalized_preference(
        "onboarding_step",
        step,
        enum_type=OnboardingStep,
        coercer=coerce_onboarding_step,
        fallback=OnboardingStep.DONE,
        after_set=_mark_seen_when_done,
    )


def get_onboarding_choice() -> OnboardingChoice | None:
    """Letzte Wizard-Auswahl (fast/private/advanced)."""
    return _get_normalized_preference(
        "onboarding_choice",
        coercer=coerce_onboarding_choice,
    )


def set_onboarding_choice(choice: OnboardingChoice | str | None) -> None:
    """Speichert die Wizard-Auswahl oder löscht sie."""
    _set_normalized_preference(
        "onboarding_choice",
        choice,
        enum_type=OnboardingChoice,
        coercer=coerce_onboarding_choice,
    )


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
    return _get_bool_preference("show_welcome_on_startup", default=True)


def set_show_welcome_on_startup(show: bool) -> None:
    """Setzt ob Welcome-Window bei jedem Start gezeigt werden soll."""
    _store_preference("show_welcome_on_startup", show)


def _update_single_env_setting(
    key_name: str,
    value: str | None,
    *,
    read_error_message: str,
    write_error_message: str,
    invalidate_cache_on_noop: bool = False,
    suppress_errors: bool = False,
    preserve_following_duplicates: bool = False,
) -> bool:
    """Update one `.env` key through the shared file-mutation pipeline."""
    return _update_env_file(
        ENV_FILE,
        apply_changes=lambda lines: _apply_single_env_update(
            lines,
            key_name,
            value,
            preserve_following_duplicates=preserve_following_duplicates,
        ),
        read_error_message=read_error_message,
        write_error_message=write_error_message,
        invalidate_cache_on_noop=invalidate_cache_on_noop,
        suppress_errors=suppress_errors,
    )


def _save_first_env_setting(key_name: str, value: str) -> None:
    """Persist one env assignment while preserving later duplicates for that key."""
    _update_single_env_setting(
        key_name,
        value,
        read_error_message="Konnte .env nicht lesen",
        write_error_message="Konnte .env nicht schreiben",
        invalidate_cache_on_noop=True,
        preserve_following_duplicates=True,
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
    _update_single_env_setting(
        key_name,
        None,
        read_error_message="Konnte .env nicht aktualisieren",
        write_error_message="Konnte .env nicht aktualisieren",
        suppress_errors=True,
    )


def _resolve_hotkey_env_key(kind: str) -> str:
    """Return the target env key for a hotkey kind, defaulting to toggle."""
    normalized_kind = (kind or "").strip().lower()
    return _HOTKEY_ENV_KEYS.get(normalized_kind, _HOTKEY_ENV_KEYS["toggle"])


def _normalize_hotkey_value(hotkey_str: str) -> str:
    """Normalize user-facing hotkey input before persisting it."""
    return (hotkey_str or "").strip().lower()


def _build_hotkey_env_updates(kind: str, value: str) -> dict[str, str | None]:
    """Build the env updates for one hotkey change while clearing legacy keys."""
    return {
        _resolve_hotkey_env_key(kind): value,
        **{legacy_key: None for legacy_key in _LEGACY_HOTKEY_ENV_KEYS},
    }


def apply_hotkey_setting(kind: str, hotkey_str: str) -> None:
    """Speichert Toggle/Hold Hotkey und entfernt Legacy Keys.

    `kind` ist "toggle" oder "hold". Die jeweils andere Konfiguration bleibt unverändert.
    """
    value = _normalize_hotkey_value(hotkey_str)
    if not value:
        return

    update_env_settings(_build_hotkey_env_updates(kind, value))
