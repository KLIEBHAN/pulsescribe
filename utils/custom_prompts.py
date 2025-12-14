"""Custom Prompts Management für whisper_go.

Ermöglicht Benutzern, LLM-Prompts über ~/.whisper_go/prompts.toml anzupassen.
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

import logging
import tomllib
from pathlib import Path

from config import PROMPTS_FILE
from refine.prompts import (
    CONTEXT_PROMPTS,
    DEFAULT_APP_CONTEXTS,
    VOICE_COMMANDS_INSTRUCTION,
)

logger = logging.getLogger("whisper_go")

# Bekannte Kontext-Typen für Prompt-Auswahl
KNOWN_CONTEXTS = ("default", "email", "chat", "code")

# =============================================================================
# Cache (mtime-basiert für Hot-Reload)
# =============================================================================

_cache: dict[Path, tuple[float, dict]] = {}


def _clear_cache() -> None:
    """Leert den Cache. Nur für Tests relevant."""
    global _cache
    _cache = {}


def _invalidate_cache(path: Path) -> None:
    """Entfernt einen Pfad aus dem Cache."""
    _cache.pop(path, None)


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
        current_mtime = prompts_file.stat().st_mtime
    except FileNotFoundError:
        _invalidate_cache(prompts_file)
        return get_defaults()
    except OSError as e:
        logger.warning(f"Prompts-Datei nicht lesbar: {e}")
        _invalidate_cache(prompts_file)
        return get_defaults()

    # Cache nutzen wenn Datei unverändert
    cached = _cache.get(prompts_file)
    if cached and cached[0] == current_mtime:
        return cached[1]

    # TOML parsen
    try:
        user_config = tomllib.loads(prompts_file.read_text())
    except (tomllib.TOMLDecodeError, OSError) as e:
        logger.warning(f"Prompts-Datei fehlerhaft: {e}")
        # Defaults cachen um wiederholtes Parsen zu vermeiden
        defaults = get_defaults()
        _cache[prompts_file] = (current_mtime, defaults)
        return defaults

    # User-Config mit Defaults zusammenführen
    merged = _merge_user_with_defaults(user_config)
    _cache[prompts_file] = (current_mtime, merged)
    return merged


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
    user_vc = user.get("voice_commands", {})
    return {
        "instruction": user_vc.get(
            "instruction", defaults["voice_commands"]["instruction"]
        )
    }


def _merge_prompts(user: dict, defaults: dict) -> dict:
    """Prompts: Jeder Kontext einzeln überschreibbar."""
    result = {}
    user_prompts = user.get("prompts", {})

    for context in defaults["prompts"]:
        if context in user_prompts:
            result[context] = user_prompts[context]
        else:
            result[context] = defaults["prompts"][context]

    return result


def _merge_app_contexts(user: dict, defaults: dict) -> dict:
    """App-Contexts: Defaults + User-Ergänzungen/Überschreibungen."""
    merged = dict(defaults["app_contexts"])
    if "app_contexts" in user:
        merged.update(user["app_contexts"])
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
        if "=" in line:
            app, ctx = line.split("=", 1)
            app = app.strip().strip('"')
            ctx = ctx.strip().strip('"')
            if app and ctx:
                result[app] = ctx
    return result


# =============================================================================
# Speichern (TOML-Serialisierung)
# =============================================================================


def save_custom_prompts(data: dict, path: Path | None = None) -> None:
    """Speichert Custom Prompts als TOML-Datei.

    Speichert nur die übergebenen Felder (partielle Updates möglich).
    """
    prompts_file = path or PROMPTS_FILE
    prompts_file.parent.mkdir(parents=True, exist_ok=True)

    lines = ["# Custom Prompts für whisper_go", ""]

    # Jede Sektion einzeln serialisieren
    if "voice_commands" in data:
        lines.extend(_serialize_voice_commands(data["voice_commands"]))

    if "prompts" in data:
        lines.extend(_serialize_prompts(data["prompts"]))

    if "app_contexts" in data:
        lines.extend(_serialize_app_contexts(data["app_contexts"]))

    try:
        prompts_file.write_text("\n".join(lines))
    except OSError as e:
        logger.warning(f"Prompts-Datei nicht schreibbar: {e}")
        raise

    # Cache aktualisieren damit nächster Load die neuen Daten sieht
    _invalidate_cache(prompts_file)
    load_custom_prompts(path=prompts_file)


def _serialize_voice_commands(voice_commands: dict) -> list[str]:
    """Serialisiert Voice-Commands Sektion zu TOML-Zeilen."""
    if "instruction" not in voice_commands:
        return []

    instruction = _escape_toml_multiline(voice_commands["instruction"])
    return ["[voice_commands]", f'instruction = """\n{instruction}"""', ""]


def _serialize_prompts(prompts: dict) -> list[str]:
    """Serialisiert Prompts Sektion zu TOML-Zeilen."""
    lines = []
    for context, config in prompts.items():
        if "prompt" in config:
            prompt_text = _escape_toml_multiline(config["prompt"])
            lines.extend(
                [f"[prompts.{context}]", f'prompt = """\n{prompt_text}"""', ""]
            )
    return lines


def _serialize_app_contexts(app_contexts: dict) -> list[str]:
    """Serialisiert App-Contexts Sektion zu TOML-Zeilen."""
    lines = ["[app_contexts]"]
    for app, ctx in sorted(app_contexts.items()):
        # App-Namen mit Leerzeichen müssen gequotet werden
        key = f'"{app}"' if " " in app else app
        lines.append(f'{key} = "{ctx}"')
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
    # Konstanten
    "PROMPTS_FILE",
    "KNOWN_CONTEXTS",
    # Testing
    "_clear_cache",
]
