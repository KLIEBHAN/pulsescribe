"""Custom Prompts aus TOML laden.

Lädt User-angepasste Prompts aus ~/.whisper_go/prompts.toml.
Falls nicht vorhanden oder fehlerhaft, Fallback auf Hardcoded Defaults.

Struktur der TOML-Datei:
    [voice_commands]
    instruction = \"\"\"...\"\"\"

    [prompts.default]
    prompt = \"\"\"...\"\"\"

    [prompts.email]
    prompt = \"\"\"...\"\"\"

    [app_contexts]
    Mail = "email"
    Slack = "chat"
"""

from __future__ import annotations

import logging
import tomllib
from pathlib import Path

from config import USER_CONFIG_DIR
from refine.prompts import (
    CONTEXT_PROMPTS,
    DEFAULT_APP_CONTEXTS,
    VOICE_COMMANDS_INSTRUCTION,
)

logger = logging.getLogger("whisper_go")

PROMPTS_FILE = USER_CONFIG_DIR / "prompts.toml"

# Cache per path: {Path: (mtime, data)}
_cache: dict[Path, tuple[float, dict]] = {}


def _clear_cache() -> None:
    """Leert den Cache (für Tests)."""
    global _cache
    _cache = {}


def get_defaults() -> dict:
    """Gibt die Hardcoded Defaults zurück (für UI Reset-Funktion).

    Returns:
        Dict mit allen Default-Prompts, Voice-Commands und App-Contexts.
    """
    return {
        "voice_commands": {"instruction": VOICE_COMMANDS_INSTRUCTION},
        "prompts": {
            context: {"prompt": prompt} for context, prompt in CONTEXT_PROMPTS.items()
        },
        "app_contexts": dict(DEFAULT_APP_CONTEXTS),
    }


def load_custom_prompts(path: Path | None = None) -> dict:
    """Lädt Custom Prompts mit Fallback auf Defaults.

    Args:
        path: Optional override für Tests.

    Returns:
        Dict mit Prompts, Voice-Commands und App-Contexts.
        Fehlende Felder werden mit Defaults aufgefüllt.
    """
    prompts_file = path or PROMPTS_FILE

    try:
        mtime = prompts_file.stat().st_mtime
    except FileNotFoundError:
        _cache.pop(prompts_file, None)
        return get_defaults()
    except OSError as e:
        logger.warning(f"Prompts-Datei nicht lesbar: {e}")
        _cache.pop(prompts_file, None)
        return get_defaults()

    # Cache-Hit?
    cached = _cache.get(prompts_file)
    if cached and cached[0] == mtime:
        return cached[1]

    # Parsen
    try:
        raw = tomllib.loads(prompts_file.read_text())
    except (tomllib.TOMLDecodeError, OSError) as e:
        logger.warning(f"Prompts-Datei fehlerhaft: {e}")
        return get_defaults()

    # Mit Defaults mergen
    defaults = get_defaults()
    result = _merge_with_defaults(raw, defaults)

    _cache[prompts_file] = (mtime, result)
    return result


def _merge_with_defaults(custom: dict, defaults: dict) -> dict:
    """Merged Custom-Config mit Defaults (Custom hat Priorität)."""
    result = {
        "voice_commands": {
            "instruction": custom.get("voice_commands", {}).get(
                "instruction", defaults["voice_commands"]["instruction"]
            )
        },
        "prompts": {},
        "app_contexts": {**defaults["app_contexts"]},  # Defaults als Basis
    }

    # Prompts mergen
    for context in defaults["prompts"]:
        if context in custom.get("prompts", {}):
            result["prompts"][context] = custom["prompts"][context]
        else:
            result["prompts"][context] = defaults["prompts"][context]

    # Custom App-Contexts überschreiben Defaults
    if "app_contexts" in custom:
        result["app_contexts"].update(custom["app_contexts"])

    return result


def get_custom_prompt_for_context(context: str) -> str:
    """Gibt Custom Prompt für Kontext zurück (oder Default).

    Args:
        context: Kontext-Typ (email, chat, code, default)

    Returns:
        Der Prompt-Text. Bei unbekanntem Kontext → default.
    """
    data = load_custom_prompts()
    prompts = data.get("prompts", {})

    if context in prompts:
        return prompts[context].get("prompt", CONTEXT_PROMPTS.get(context, ""))

    # Fallback auf default
    return prompts.get("default", {}).get("prompt", CONTEXT_PROMPTS["default"])


def get_custom_voice_commands() -> str:
    """Gibt Custom Voice-Commands zurück (oder Default)."""
    data = load_custom_prompts()
    return data.get("voice_commands", {}).get("instruction", VOICE_COMMANDS_INSTRUCTION)


def get_custom_app_contexts() -> dict[str, str]:
    """Gibt Custom App-Mappings zurück (merged mit Defaults)."""
    data = load_custom_prompts()
    return data.get("app_contexts", dict(DEFAULT_APP_CONTEXTS))


def _escape_toml_multiline(text: str) -> str:
    """Escaped Text für TOML multi-line basic strings.

    In TOML multi-line basic strings (\"\"\"...\"\"\":
    - Backslashes müssen escaped werden: \\ → \\\\
    - Triple-Quotes müssen escaped werden: \"\"\" → \\\"\"\"
    """
    # Erst Backslashes escapen, dann Triple-Quotes
    text = text.replace("\\", "\\\\")
    text = text.replace('"""', '\\"""')
    return text


def save_custom_prompts(data: dict, path: Path | None = None) -> None:
    """Speichert Custom Prompts als TOML.

    Args:
        data: Dict mit prompts, voice_commands und/oder app_contexts.
        path: Optional override für Tests.
    """
    prompts_file = path or PROMPTS_FILE
    prompts_file.parent.mkdir(parents=True, exist_ok=True)

    lines: list[str] = ["# Custom Prompts für whisper_go", ""]

    # Voice Commands
    if "voice_commands" in data and "instruction" in data["voice_commands"]:
        lines.append("[voice_commands]")
        instruction = _escape_toml_multiline(data["voice_commands"]["instruction"])
        lines.append(f'instruction = """\n{instruction}"""')
        lines.append("")

    # Prompts
    if "prompts" in data:
        for context, cfg in data["prompts"].items():
            if "prompt" in cfg:
                lines.append(f"[prompts.{context}]")
                prompt = _escape_toml_multiline(cfg["prompt"])
                lines.append(f'prompt = """\n{prompt}"""')
                lines.append("")

    # App Contexts
    if "app_contexts" in data:
        lines.append("[app_contexts]")
        for app, ctx in sorted(data["app_contexts"].items()):
            # Quote app name if contains spaces
            key = f'"{app}"' if " " in app else app
            lines.append(f'{key} = "{ctx}"')
        lines.append("")

    try:
        prompts_file.write_text("\n".join(lines))
    except OSError as e:
        logger.warning(f"Prompts-Datei nicht schreibbar: {e}")
        raise

    # Cache invalidieren und neu laden
    _cache.pop(prompts_file, None)
    load_custom_prompts(path=prompts_file)


def reset_to_defaults(path: Path | None = None) -> None:
    """Löscht User-Config und setzt auf Defaults zurück.

    Args:
        path: Optional override für Tests.
    """
    prompts_file = path or PROMPTS_FILE

    try:
        prompts_file.unlink(missing_ok=True)
    except OSError as e:
        logger.warning(f"Prompts-Datei nicht löschbar: {e}")

    _cache.pop(prompts_file, None)


__all__ = [
    "load_custom_prompts",
    "get_custom_prompt_for_context",
    "get_custom_voice_commands",
    "get_custom_app_contexts",
    "save_custom_prompts",
    "reset_to_defaults",
    "get_defaults",
    "PROMPTS_FILE",
    "_clear_cache",
]
