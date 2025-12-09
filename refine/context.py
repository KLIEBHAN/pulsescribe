"""Kontext-Erkennung für whisper_go.

Erkennt den Kontext basierend auf der aktiven Anwendung und
wählt entsprechend angepasste Prompts für die LLM-Nachbearbeitung.
"""

import json
import logging
import os
import sys

from prompts import DEFAULT_APP_CONTEXTS

logger = logging.getLogger("whisper_go")

# Cache für custom app contexts (aus ENV)
_custom_app_contexts_cache: dict | None = None


def _get_session_id() -> str:
    """Holt Session-ID für Logging."""
    try:
        from utils.logging import get_session_id
        return get_session_id()
    except ImportError:
        return "unknown"


def _get_frontmost_app() -> str | None:
    """Ermittelt aktive App.

    Delegiert an whisper_platform.app_detection für plattformspezifische Implementierung.
    """
    try:
        from whisper_platform import get_app_detector
        return get_app_detector().get_frontmost_app()
    except ImportError:
        # Fallback auf direkte NSWorkspace-Nutzung (macOS)
        if sys.platform != "darwin":
            return None
        try:
            from AppKit import NSWorkspace
            app = NSWorkspace.sharedWorkspace().frontmostApplication()
            return app.localizedName() if app else None
        except ImportError:
            logger.debug(f"[{_get_session_id()}] PyObjC/AppKit nicht verfügbar")
            return None
    except Exception as e:
        logger.debug(f"[{_get_session_id()}] App-Detection fehlgeschlagen: {e}")
        return None


def _get_custom_app_contexts() -> dict:
    """Lädt und cached custom app contexts aus WHISPER_GO_APP_CONTEXTS."""
    global _custom_app_contexts_cache

    if _custom_app_contexts_cache is not None:
        return _custom_app_contexts_cache

    custom = os.getenv("WHISPER_GO_APP_CONTEXTS")
    if custom:
        try:
            _custom_app_contexts_cache = json.loads(custom)
            logger.debug(
                f"[{_get_session_id()}] Custom app contexts geladen: "
                f"{list(_custom_app_contexts_cache.keys())}"
            )
        except json.JSONDecodeError as e:
            logger.warning(
                f"[{_get_session_id()}] WHISPER_GO_APP_CONTEXTS ungültiges JSON: {e}"
            )
            _custom_app_contexts_cache = {}
    else:
        _custom_app_contexts_cache = {}

    return _custom_app_contexts_cache


def get_context_for_app(app_name: str) -> str:
    """Mappt App-Name auf Kontext-Typ.

    Args:
        app_name: Name der Anwendung

    Returns:
        Kontext-Typ: 'email', 'chat', 'code' oder 'default'
    """
    custom_map = _get_custom_app_contexts()
    if app_name in custom_map:
        return custom_map[app_name]

    return DEFAULT_APP_CONTEXTS.get(app_name, "default")


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
    env_context = os.getenv("WHISPER_GO_CONTEXT")
    if env_context:
        return env_context.lower(), None, "ENV"

    # 3. Auto-Detection via NSWorkspace (nur macOS)
    if sys.platform == "darwin":
        app_name = _get_frontmost_app()
        if app_name:
            return get_context_for_app(app_name), app_name, "App"

    return "default", None, "Default"


def reset_cache() -> None:
    """Setzt den Cache für custom app contexts zurück (für Tests)."""
    global _custom_app_contexts_cache
    _custom_app_contexts_cache = None
