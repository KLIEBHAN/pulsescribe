"""Kontext-Erkennung für PulseScribe.

Erkennt den Kontext basierend auf der aktiven Anwendung und
wählt entsprechend angepasste Prompts für die LLM-Nachbearbeitung.
"""

import json
import logging
import os
import sys

from utils.logging import get_session_id

logger = logging.getLogger("pulsescribe")

# Cache für custom app contexts (aus ENV)
_custom_app_contexts_cache: dict | None = None


def _get_frontmost_app() -> str | None:
    """Ermittelt aktive App.

    Delegiert an whisper_platform.app_detection für plattformspezifische Implementierung.
    """
    try:
        from whisper_platform import get_app_detector

        return get_app_detector().get_frontmost_app()
    except ImportError:
        logger.debug(f"[{get_session_id()}] whisper_platform nicht verfügbar")
        return None
    except Exception as e:
        logger.debug(f"[{get_session_id()}] App-Detection fehlgeschlagen: {e}")
        return None


def _get_custom_app_contexts() -> dict:
    """Lädt und cached custom app contexts aus PULSESCRIBE_APP_CONTEXTS."""
    global _custom_app_contexts_cache

    if _custom_app_contexts_cache is not None:
        return _custom_app_contexts_cache

    custom = os.getenv("PULSESCRIBE_APP_CONTEXTS")
    if custom:
        try:
            parsed = json.loads(custom)
            # Schema-Validierung: Muss dict mit string keys/values sein
            if not isinstance(parsed, dict):
                logger.warning(
                    f"[{get_session_id()}] PULSESCRIBE_APP_CONTEXTS muss ein JSON-Objekt sein, "
                    f"ist aber {type(parsed).__name__}"
                )
                _custom_app_contexts_cache = {}
            elif not all(isinstance(k, str) and isinstance(v, str) for k, v in parsed.items()):
                logger.warning(
                    f"[{get_session_id()}] PULSESCRIBE_APP_CONTEXTS enthält ungültige Typen "
                    "(erwartet: string → string)"
                )
                # Nur gültige Einträge übernehmen
                _custom_app_contexts_cache = {
                    k: v for k, v in parsed.items()
                    if isinstance(k, str) and isinstance(v, str)
                }
            else:
                _custom_app_contexts_cache = parsed
                logger.debug(
                    f"[{get_session_id()}] Custom app contexts geladen: "
                    f"{list(_custom_app_contexts_cache.keys())}"
                )
        except json.JSONDecodeError as e:
            logger.warning(
                f"[{get_session_id()}] PULSESCRIBE_APP_CONTEXTS ungültiges JSON: {e}"
            )
            _custom_app_contexts_cache = {}
    else:
        _custom_app_contexts_cache = {}

    return _custom_app_contexts_cache


def get_context_for_app(app_name: str) -> str:
    """Mappt App-Name auf Kontext-Typ.

    Priorität: ENV (PULSESCRIBE_APP_CONTEXTS) > TOML (~/.pulsescribe/prompts.toml) > Defaults
    Lookup ist case-insensitive (Windows gibt z.B. "OUTLOOK" statt "Outlook").

    Args:
        app_name: Name der Anwendung

    Returns:
        Kontext-Typ: 'email', 'chat', 'code' oder 'default'
    """
    app_lower = app_name.lower()

    # 1. ENV-Override hat höchste Priorität (case-insensitive)
    env_map = _get_custom_app_contexts()
    for key, value in env_map.items():
        if key.lower() == app_lower:
            return value

    # 2. Custom TOML (merged mit Defaults, case-insensitive)
    from utils.custom_prompts import get_custom_app_contexts

    toml_map = get_custom_app_contexts()
    for key, value in toml_map.items():
        if key.lower() == app_lower:
            return value

    return "default"


# Alias für Rückwärtskompatibilität
_app_to_context = get_context_for_app


def detect_context(override: str | None = None) -> tuple[str, str | None, str]:
    """Ermittelt Kontext: CLI > ENV > App-Detection > default.

    Args:
        override: Optional CLI-Override für Kontext

    Returns:
        Tuple (context, app_name, source) - source zeigt woher der Kontext kommt
    """
    # 1. CLI-Override (höchste Priorität)
    if override:
        return override, None, "CLI"

    # 2. ENV-Override
    env_context = os.getenv("PULSESCRIBE_CONTEXT")
    if env_context:
        return env_context.lower(), None, "ENV"

    # 3. Auto-Detection via Platform API (macOS + Windows)
    if sys.platform in ("darwin", "win32"):
        app_name = _get_frontmost_app()
        if app_name:
            return get_context_for_app(app_name), app_name, "App"

    return "default", None, "Default"


def reset_cache() -> None:
    """Setzt den Cache für custom app contexts zurück (für Tests)."""
    global _custom_app_contexts_cache
    _custom_app_contexts_cache = None
