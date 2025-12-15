"""
Berechtigungs-Checks für macOS (Mikrofon, Accessibility).

Hinweis: Diese Funktionen sind macOS-spezifisch. Auf anderen Plattformen
geben sie sichere Defaults zurück (True für Berechtigungen, "authorized" für States).
"""

import logging
import sys

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

        _app_services = ctypes.cdll.LoadLibrary(
            ctypes.util.find_library("ApplicationServices")
        )
        _app_services.AXIsProcessTrusted.restype = ctypes.c_bool
    except Exception:
        pass
    return _app_services


def has_accessibility_permission() -> bool:
    """Returns True if the app has Accessibility permission (no logging/alerts)."""
    if sys.platform != "darwin":
        return True  # Auf non-macOS immer True zurückgeben
    try:
        app_services = _get_app_services()
        if app_services is None:
            return True
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
        return True

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

    return True


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
        return True

    ok = has_input_monitoring_permission()

    if ok:
        return True

    logger.warning(
        "Input‑Monitoring‑Berechtigung fehlt – globale Hotkeys funktionieren nicht"
    )

    if request:
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
