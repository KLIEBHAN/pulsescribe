"""
Berechtigungs-Checks für macOS (Mikrofon, Accessibility).

Hinweis: Diese Funktionen sind macOS-spezifisch. Auf anderen Plattformen
geben sie sichere Defaults zurück (True für Berechtigungen, "authorized" für States).
"""

import logging
import sys
import time

logger = logging.getLogger("pulsescribe")

# Used to suppress permission-related popups (we have dedicated UI for that now).
_PERMISSION_MESSAGE_TOKENS = (
    "eingabemonitoring",
    "input monitoring",
    "bedienungshilfen",
    "accessibility",
    "mikrofon",
    "microphone",
)

# Accessibility API (macOS only, lazy loaded)
_app_services = None
_PERMISSION_SIGNATURE_CACHE_TTL_SECONDS = 0.25
_permission_signature_cache: tuple[float, tuple[str, bool, bool]] | None = None


def _get_app_services():
    """Lazy-load ApplicationServices framework (macOS only)."""
    global _app_services
    if _app_services is not None:
        return _app_services
    if sys.platform != "darwin":
        return None
    try:
        import ctypes
        import ctypes.util

        library_path = ctypes.util.find_library("ApplicationServices")
        if not library_path:
            return None

        app_services = ctypes.cdll.LoadLibrary(library_path)
        ax_is_process_trusted = getattr(app_services, "AXIsProcessTrusted", None)
        if ax_is_process_trusted is None:
            return None

        ax_is_process_trusted.restype = ctypes.c_bool
        _app_services = app_services
    except Exception:
        _app_services = None
    return _app_services


def invalidate_permission_signature_cache() -> None:
    """Clear the short-lived shared permission snapshot cache."""
    global _permission_signature_cache
    _permission_signature_cache = None


def get_permission_signature(
    *,
    max_age_seconds: float = _PERMISSION_SIGNATURE_CACHE_TTL_SECONDS,
) -> tuple[str, bool, bool]:
    """Return a short-lived shared permission snapshot.

    The welcome screen, onboarding wizard, and permission card often query the
    same three macOS permission helpers in quick succession. Those calls are
    relatively expensive, so we reuse a very short-lived snapshot across the
    immediate UI burst while still allowing the regular auto-refresh cadence to
    observe changes quickly.
    """
    global _permission_signature_cache

    now = time.monotonic()
    cached = _permission_signature_cache
    if cached is not None and now - cached[0] <= max(0.0, max_age_seconds):
        return cached[1]

    signature = (
        get_microphone_permission_state(),
        has_accessibility_permission(),
        has_input_monitoring_permission(),
    )
    _permission_signature_cache = (now, signature)
    return signature


def has_accessibility_permission() -> bool:
    """Returns True if the app has Accessibility permission (no logging/alerts)."""
    if sys.platform != "darwin":
        return True  # Auf non-macOS immer True zurückgeben
    try:
        app_services = _get_app_services()
        if app_services is None:
            return False
        return bool(app_services.AXIsProcessTrusted())
    except Exception:
        return False


def has_input_monitoring_permission() -> bool:
    """Returns True if the app has Input Monitoring permission (no logging/alerts)."""
    if sys.platform != "darwin":
        return True  # Auf non-macOS immer True zurückgeben
    try:
        from Quartz import CGPreflightListenEventAccess  # type: ignore[import-not-found]
    except Exception:
        return False

    try:
        return bool(CGPreflightListenEventAccess())
    except Exception:
        return False


def get_microphone_permission_state() -> str:
    """Gibt den aktuellen Mikrofon-Permission-State zurück.

    Returns:
        One of: "authorized", "not_determined", "denied", "restricted", "unknown"
    """
    if sys.platform != "darwin":
        return "authorized"  # Auf non-macOS immer authorized zurückgeben

    try:
        from AVFoundation import (  # type: ignore[import-not-found]
            AVAuthorizationStatusAuthorized,
            AVAuthorizationStatusDenied,
            AVAuthorizationStatusNotDetermined,
            AVAuthorizationStatusRestricted,
            AVCaptureDevice,
            AVMediaTypeAudio,
        )

        status = AVCaptureDevice.authorizationStatusForMediaType_(AVMediaTypeAudio)
    except Exception:
        return "unknown"

    if status == AVAuthorizationStatusAuthorized:
        return "authorized"
    if status == AVAuthorizationStatusNotDetermined:
        return "not_determined"
    if status == AVAuthorizationStatusDenied:
        return "denied"
    if status == AVAuthorizationStatusRestricted:
        return "restricted"
    return "unknown"


def check_microphone_permission(show_alert: bool = True, request: bool = False) -> bool:
    """
    Prüft Mikrofon-Berechtigung.
    Zeigt keine modalen Popups (UI handled via Permissions page).

    Returns:
        True wenn Zugriff erlaubt oder (noch) nicht entschieden.
        False wenn explizit verweigert/eingeschränkt.
    """
    if sys.platform != "darwin":
        return True  # Auf non-macOS immer True zurückgeben

    state = get_microphone_permission_state()

    if state == "authorized":
        return True

    if state == "not_determined":
        # OS wird beim ersten Zugriff fragen
        if request:
            invalidate_permission_signature_cache()
            try:
                from AVFoundation import (  # type: ignore[import-not-found]
                    AVCaptureDevice,
                    AVMediaTypeAudio,
                )

                AVCaptureDevice.requestAccessForMediaType_completionHandler_(
                    AVMediaTypeAudio, lambda _granted: None
                )
            except Exception:
                pass
        return True

    if state in ("denied", "restricted"):
        logger.error("Mikrofon-Zugriff verweigert!")
        return False

    logger.warning("Mikrofon-Berechtigungsstatus konnte nicht ermittelt werden")
    return False


def check_accessibility_permission(
    show_alert: bool = True, request: bool = False
) -> bool:
    """
    Prüft Accessibility-Berechtigung (für Auto-Paste via CMD+V).
    Zeigt keine modalen Popups (UI handled via Permissions page).

    Returns:
        True wenn Zugriff erlaubt, False wenn nicht.
    """
    if sys.platform != "darwin":
        return True  # Auf non-macOS immer True zurückgeben

    if has_accessibility_permission():
        return True

    if request:
        invalidate_permission_signature_cache()
        try:
            from Quartz import (  # type: ignore[import-not-found]
                AXIsProcessTrustedWithOptions,
                kAXTrustedCheckOptionPrompt,
            )

            AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: True})
        except Exception:
            pass

    logger.warning(
        "Accessibility-Berechtigung fehlt - Auto-Paste wird nicht funktionieren"
    )
    return False


def check_input_monitoring_permission(
    show_alert: bool = True, request: bool = False
) -> bool:
    """
    Prüft Input‑Monitoring/Eingabemonitoring‑Berechtigung (für globale Key‑Listener).

    macOS verlangt diese Berechtigung für Quartz Event Taps und pynput Listener.

    Args:
        show_alert: Deprecated/ignored (keine modalen Popups).
        request: Wenn True, fordert der Prozess die Berechtigung aktiv an.

    Returns:
        True wenn Zugriff erlaubt, False wenn nicht.
    """
    if sys.platform != "darwin":
        return True  # Auf non-macOS immer True zurückgeben

    try:
        from Quartz import CGRequestListenEventAccess  # type: ignore[import-not-found]
    except Exception:
        CGRequestListenEventAccess = None  # type: ignore[assignment]

    ok = has_input_monitoring_permission()

    if ok:
        return True

    logger.warning(
        "Input‑Monitoring‑Berechtigung fehlt – globale Hotkeys funktionieren nicht"
    )

    if request and CGRequestListenEventAccess is not None:
        invalidate_permission_signature_cache()
        try:
            CGRequestListenEventAccess()
        except Exception:
            pass

    return False


def is_permission_related_message(message: str | None) -> bool:
    """Best-effort check if a message is about missing system permissions."""
    msg = (message or "").lower()
    return any(token in msg for token in _PERMISSION_MESSAGE_TOKENS)


def open_privacy_settings(anchor: str, *, window=None) -> None:
    """Opens macOS System Settings → Privacy & Security.

    Args:
        anchor: Privacy section (e.g., "Privacy_Microphone", "Privacy_Accessibility")
        window: Optional NSWindow to temporarily lower its level so Settings appears in front
    """
    if sys.platform != "darwin":
        return  # No-op auf non-macOS

    import subprocess

    invalidate_permission_signature_cache()
    url = f"x-apple.systempreferences:com.apple.preference.security?{anchor}"
    try:
        # Lower window level so System Settings appears in front
        if window is not None:
            try:
                from AppKit import NSNormalWindowLevel  # type: ignore[import-not-found]

                window.setLevel_(NSNormalWindowLevel)
            except Exception:
                pass
        subprocess.Popen(["open", url])
    except Exception:
        pass
