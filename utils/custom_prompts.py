"""Custom Prompts Management für PulseScribe.

Ermöglicht Benutzern, LLM-Prompts über ~/.pulsescribe/prompts.toml anzupassen.
Bei fehlender oder fehlerhafter Datei werden Hardcoded-Defaults verwendet.

Dateiformat:
    [voice_commands]
    instruction = \"\"\"...\"\"\"

    [prompts.email]
    prompt = \"\"\"...\"\"\"

    [app_contexts]
    Mail = "email"
"""

from __future__ import annotations

from copy import deepcopy
import logging
import tomllib
from pathlib import Path

from config import PROMPTS_FILE
from utils.atomic_io import write_text_atomic as _write_text_atomic
from utils.file_signatures import FileSignature, build_file_signature
from refine.prompts import (
    CONTEXT_PROMPTS,
    DEFAULT_APP_CONTEXTS,
    VOICE_COMMANDS_INSTRUCTION,
)

logger = logging.getLogger("pulsescribe")

# Bekannte Kontext-Typen für Prompt-Auswahl
KNOWN_CONTEXTS = ("default", "email", "chat", "code")


def _normalize_context_name(value: str | None) -> str | None:
    normalized = (value or "").strip().strip('"').strip("'").lower()
    if not normalized:
        return None
    if normalized in KNOWN_CONTEXTS:
        return normalized
    return None


def _normalize_app_context_entry(
    app: object,
    ctx: object,
) -> tuple[str, str] | None:
    """Normalize one app→context mapping while preserving current rules."""
    normalized_app = str(app).strip()
    normalized_ctx = _normalize_context_name(str(ctx))
    if not normalized_app or not normalized_ctx:
        return None
    return normalized_app, normalized_ctx


def _iter_normalized_app_context_entries(app_contexts: dict) -> list[tuple[str, str]]:
    """Return only valid, normalized app-context mappings."""
    return [
        normalized
        for normalized in (
            _normalize_app_context_entry(app, ctx) for app, ctx in app_contexts.items()
        )
        if normalized is not None
    ]


def _coerce_mapping(value: object) -> dict:
    """Return only dict-like sections so malformed UI payloads stay ignorable."""
    return value if isinstance(value, dict) else {}


def _get_section_mapping(data: dict, section_name: str) -> dict:
    """Read one top-level section while falling back to an empty mapping."""
    return _coerce_mapping(data.get(section_name))


def _get_string_field(mapping: object, field_name: str) -> str | None:
    """Return one string field from a section/config mapping when present."""
    if not isinstance(mapping, dict):
        return None
    value = mapping.get(field_name)
    return value if isinstance(value, str) else None


def _iter_prompt_entries(prompts: object) -> list[tuple[str, str]]:
    """Yield only prompt entries that still match the expected ``{prompt: str}`` shape."""
    return [
        (str(context), prompt_text)
        for context, config in _coerce_mapping(prompts).items()
        if (prompt_text := _get_string_field(config, "prompt")) is not None
    ]


# =============================================================================
# Cache (Signature-basiert für Hot-Reload)
# =============================================================================

_cache: dict[Path, tuple[FileSignature, dict]] = {}


def _clear_cache() -> None:
    """Leert den Cache. Nur für Tests relevant."""
    global _cache
    _cache = {}


def _invalidate_cache(path: Path) -> None:
    """Entfernt einen Pfad aus dem Cache."""
    _cache.pop(path, None)


def _get_file_signature(path: Path) -> FileSignature:
    """Return a stable cache signature for prompt file reloads."""
    return build_file_signature(path)


def _copy_prompt_data(data: dict) -> dict:
    """Return a defensive copy so callers cannot mutate cached prompt state."""
    return deepcopy(data)


def _get_cached_prompt_data(
    prompts_file: Path,
    signature: FileSignature,
) -> dict | None:
    """Return cached prompt data when the signature still matches."""
    cached = _cache.get(prompts_file)
    if cached and cached[0] == signature:
        return _copy_prompt_data(cached[1])
    return None


def _cache_prompt_data(
    prompts_file: Path,
    signature: FileSignature,
    data: dict,
) -> None:
    """Store prompt data in the cache using a defensive copy."""
    _cache[prompts_file] = (signature, _copy_prompt_data(data))


# =============================================================================
# Defaults (Hardcoded Fallback)
# =============================================================================


def get_defaults() -> dict:
    """Gibt die Hardcoded-Defaults zurück.

    Wird verwendet für:
    - Fallback bei fehlender/fehlerhafter TOML-Datei
    - "Reset to Default" in der UI
    - Vergleich, ob User etwas geändert hat
    """
    return {
        "voice_commands": {"instruction": VOICE_COMMANDS_INSTRUCTION},
        "prompts": {ctx: {"prompt": text} for ctx, text in CONTEXT_PROMPTS.items()},
        "app_contexts": dict(DEFAULT_APP_CONTEXTS),
    }


# =============================================================================
# Laden (mit Cache und Merge)
# =============================================================================


def _load_prompt_data_from_disk(prompts_file: Path) -> dict:
    """Read prompt config from disk and fall back to defaults on parse failures."""
    try:
        user_config = tomllib.loads(prompts_file.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, OSError, UnicodeDecodeError) as e:
        logger.warning(f"Prompts-Datei fehlerhaft: {e}")
        return get_defaults()

    return _merge_user_with_defaults(user_config)


def load_custom_prompts(path: Path | None = None) -> dict:
    """Lädt Custom Prompts mit automatischem Fallback auf Defaults.

    Features:
    - mtime-basierter Cache (Änderungen werden erkannt)
    - Partielle Configs werden mit Defaults aufgefüllt
    - Fehlerhafte TOML → stille Rückkehr zu Defaults

    Args:
        path: Überschreibt PROMPTS_FILE (für Tests)
    """
    prompts_file = path or PROMPTS_FILE

    # Datei-Metadaten prüfen
    try:
        current_signature = _get_file_signature(prompts_file)
    except FileNotFoundError:
        _invalidate_cache(prompts_file)
        return get_defaults()
    except OSError as e:
        logger.warning(f"Prompts-Datei nicht lesbar: {e}")
        _invalidate_cache(prompts_file)
        return get_defaults()

    cached = _get_cached_prompt_data(prompts_file, current_signature)
    if cached is not None:
        return cached

    data = _load_prompt_data_from_disk(prompts_file)
    _cache_prompt_data(prompts_file, current_signature, data)
    return _copy_prompt_data(data)


def _merge_user_with_defaults(user_config: dict) -> dict:
    """Führt User-Konfiguration mit Defaults zusammen.

    Strategie: User-Werte überschreiben Defaults, fehlende Felder
    werden aus Defaults ergänzt.
    """
    defaults = get_defaults()

    return {
        "voice_commands": _merge_voice_commands(user_config, defaults),
        "prompts": _merge_prompts(user_config, defaults),
        "app_contexts": _merge_app_contexts(user_config, defaults),
    }


def _merge_voice_commands(user: dict, defaults: dict) -> dict:
    """Voice-Commands: User überschreibt komplett oder Default."""
    instruction = _get_string_field(
        _get_section_mapping(user, "voice_commands"),
        "instruction",
    )
    if instruction is None:
        instruction = defaults["voice_commands"]["instruction"]
    return {"instruction": instruction}


def _merge_prompts(user: dict, defaults: dict) -> dict:
    """Prompts: Jeder Kontext einzeln überschreibbar."""
    result = {}
    user_prompts = _get_section_mapping(user, "prompts")

    for context, default_config in defaults["prompts"].items():
        user_prompt = _get_string_field(user_prompts.get(context), "prompt")
        result[context] = {"prompt": user_prompt} if user_prompt is not None else default_config

    return result


def _merge_app_contexts(user: dict, defaults: dict) -> dict:
    """App-Contexts: Defaults + User-Ergänzungen/Überschreibungen."""
    merged = dict(defaults["app_contexts"])

    for app, ctx in _iter_normalized_app_context_entries(
        _get_section_mapping(user, "app_contexts")
    ):
        merged[app] = ctx
    return merged


# =============================================================================
# Getter (Public API)
# =============================================================================


def get_prompt_for_context(context: str) -> str:
    """Gibt den Prompt-Text für einen Kontext zurück.

    Bei unbekanntem Kontext wird "default" verwendet.
    """
    data = load_custom_prompts()
    # Fallback auf "default" für unbekannte Kontexte
    effective_context = context if context in KNOWN_CONTEXTS else "default"
    return data["prompts"][effective_context]["prompt"]


# Alias für Rückwärtskompatibilität
get_custom_prompt_for_context = get_prompt_for_context


def get_voice_commands() -> str:
    """Gibt die Voice-Commands Instruktion zurück."""
    return load_custom_prompts()["voice_commands"]["instruction"]


# Alias für Rückwärtskompatibilität
get_custom_voice_commands = get_voice_commands


def get_app_contexts() -> dict[str, str]:
    """Gibt das App→Kontext Mapping zurück (Defaults + User-Anpassungen)."""
    return load_custom_prompts()["app_contexts"]


# Alias für Rückwärtskompatibilität
get_custom_app_contexts = get_app_contexts


# =============================================================================
# App-Mappings: Text-Format für UI-Editor
# =============================================================================


def format_app_mappings(mappings: dict[str, str]) -> str:
    """Konvertiert App-Mappings Dict zu editierbarem Text.

    Format: Eine Zeile pro App, "AppName = context"
    """
    lines = ["# App → Context Mappings (one per line: AppName = context)"]
    lines.extend(f"{app} = {ctx}" for app, ctx in sorted(mappings.items()))
    return "\n".join(lines)


def parse_app_mappings(text: str) -> dict[str, str]:
    """Parst App-Mappings aus Text zurück zu Dict.

    Ignoriert Leerzeilen und Kommentare (#).
    """
    result = {}
    for line in text.strip().split("\n"):
        line = line.strip()
        # Leerzeilen und Kommentare überspringen
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue

        app, ctx = line.split("=", 1)
        normalized = _normalize_app_context_entry(
            app.strip().strip('"'),
            ctx.split("#", 1)[0],
        )
        if normalized is None:
            continue

        normalized_app, normalized_ctx = normalized
        result[normalized_app] = normalized_ctx
    return result


# =============================================================================
# Storage Normalization
# =============================================================================


def filter_overrides_for_storage(
    data: dict,
    *,
    defaults: dict | None = None,
) -> dict:
    """Reduziert gemergte Prompt-Daten auf echte User-Overrides.

    `load_custom_prompts()` liefert bereits Defaults + User-Werte. Für die
    Persistierung soll aber nur gespeichert werden, was vom Default abweicht.
    Dadurch funktionieren "Reset to Default"-Flows zuverlässig und die Datei
    bleibt klein.
    """
    baseline = defaults or get_defaults()
    result: dict = {}

    voice_instruction = _get_string_field(
        _get_section_mapping(data, "voice_commands"),
        "instruction",
    )
    default_instruction = str(baseline["voice_commands"]["instruction"])
    if voice_instruction and voice_instruction != default_instruction:
        result["voice_commands"] = {"instruction": voice_instruction}

    prompts_result: dict[str, dict[str, str]] = {}
    default_prompts = _get_section_mapping(baseline, "prompts")
    for context, prompt_text in _iter_prompt_entries(_get_section_mapping(data, "prompts")):
        default_prompt = str(_get_string_field(default_prompts.get(context), "prompt") or "")
        if prompt_text and prompt_text != default_prompt:
            prompts_result[context] = {"prompt": prompt_text}
    if prompts_result:
        result["prompts"] = prompts_result

    app_contexts_result: dict[str, str] = {}
    default_app_contexts = _get_section_mapping(baseline, "app_contexts")
    for normalized_app, normalized_ctx in _iter_normalized_app_context_entries(
        _get_section_mapping(data, "app_contexts")
    ):
        if default_app_contexts.get(normalized_app) == normalized_ctx:
            continue
        app_contexts_result[normalized_app] = normalized_ctx
    if app_contexts_result:
        result["app_contexts"] = app_contexts_result

    return result


# =============================================================================
# Speichern (TOML-Serialisierung)
# =============================================================================


def _serialize_prompt_sections(data: dict) -> list[str]:
    """Serialize prompt sections in one stable, human-readable order."""
    lines = ["# Custom Prompts für pulsescribe", ""]
    for section_name, serializer in (
        ("voice_commands", _serialize_voice_commands),
        ("prompts", _serialize_prompts),
        ("app_contexts", _serialize_app_contexts),
    ):
        if section_name not in data:
            continue
        lines.extend(serializer(_get_section_mapping(data, section_name)))
    return lines


def save_custom_prompts(data: dict, path: Path | None = None) -> None:
    """Speichert Custom Prompts als TOML-Datei.

    Speichert nur die übergebenen Felder (partielle Updates möglich).
    """
    prompts_file = path or PROMPTS_FILE

    try:
        _write_text_atomic(prompts_file, "\n".join(_serialize_prompt_sections(data)))
    except OSError as e:
        logger.warning(f"Prompts-Datei nicht schreibbar: {e}")
        raise

    # Cache aktualisieren damit nächster Load die neuen Daten sieht
    _invalidate_cache(prompts_file)
    load_custom_prompts(path=prompts_file)


def _serialize_voice_commands(voice_commands: dict) -> list[str]:
    """Serialisiert Voice-Commands Sektion zu TOML-Zeilen."""
    instruction_value = _get_string_field(voice_commands, "instruction")
    if instruction_value is None:
        return []

    instruction = _escape_toml_multiline(instruction_value)
    return ["[voice_commands]", f'instruction = """\n{instruction}"""', ""]


def _serialize_prompts(prompts: dict) -> list[str]:
    """Serialisiert Prompts Sektion zu TOML-Zeilen."""
    lines = []
    for context, prompt_value in _iter_prompt_entries(prompts):
        prompt_text = _escape_toml_multiline(prompt_value)
        lines.extend(
            [f"[prompts.{context}]", f'prompt = """\n{prompt_text}"""', ""]
        )
    return lines


def _serialize_app_contexts(app_contexts: dict) -> list[str]:
    """Serialisiert App-Contexts Sektion zu TOML-Zeilen."""
    items = sorted((str(app), str(ctx)) for app, ctx in app_contexts.items())
    if not items:
        return []

    lines = ["[app_contexts]"]
    for app, ctx in items:
        # TOML bare keys erlauben nur [A-Za-z0-9_-].
        # Immer quoten ist sicher und verhindert Probleme mit Dots
        # (Table-Separator), Leerzeichen, Quotes und anderen Sonderzeichen.
        escaped_app = app.replace("\\", "\\\\").replace('"', '\\"')
        lines.append(f'"{escaped_app}" = "{ctx}"')
    lines.append("")
    return lines


def _escape_toml_multiline(text: str) -> str:
    """Escaped Text für TOML Multi-Line Strings.

    Reihenfolge wichtig: Erst Backslashes, dann Triple-Quotes.
    Sonst würde \\\" zu \\\\\" statt zu \\\"
    """
    text = text.replace("\\", "\\\\")
    text = text.replace('"""', '\\"""')
    return text


# =============================================================================
# Reset
# =============================================================================


def reset_to_defaults(path: Path | None = None) -> None:
    """Löscht die User-Config und kehrt zu Defaults zurück."""
    prompts_file = path or PROMPTS_FILE

    try:
        prompts_file.unlink(missing_ok=True)
    except OSError as e:
        logger.warning(f"Prompts-Datei nicht löschbar: {e}")

    _invalidate_cache(prompts_file)


# =============================================================================
# Public API
# =============================================================================

__all__ = [
    # Laden
    "load_custom_prompts",
    "get_defaults",
    # Getter (neue Namen)
    "get_prompt_for_context",
    "get_voice_commands",
    "get_app_contexts",
    # Getter (Aliase für Rückwärtskompatibilität)
    "get_custom_prompt_for_context",
    "get_custom_voice_commands",
    "get_custom_app_contexts",
    # App-Mappings Format
    "format_app_mappings",
    "parse_app_mappings",
    # Speichern/Reset
    "save_custom_prompts",
    "reset_to_defaults",
    "filter_overrides_for_storage",
    # Konstanten
    "PROMPTS_FILE",
    "KNOWN_CONTEXTS",
    # Testing
    "_clear_cache",
]
