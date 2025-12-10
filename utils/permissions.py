"""
Berechtigungs-Checks für macOS (Mikrofon, Accessibility).
"""

import logging
import ctypes
import ctypes.util

from AppKit import NSAlert, NSInformationalAlertStyle
from AVFoundation import (
    AVCaptureDevice,
    AVMediaTypeAudio,
    AVAuthorizationStatusAuthorized,
    AVAuthorizationStatusDenied,
    AVAuthorizationStatusRestricted,
    AVAuthorizationStatusNotDetermined,
)

logger = logging.getLogger("whisper_go")

# Accessibility API laden
_app_services = ctypes.cdll.LoadLibrary(
    ctypes.util.find_library("ApplicationServices")
)
_app_services.AXIsProcessTrusted.restype = ctypes.c_bool


def check_microphone_permission() -> bool:
    """
    Prüft Mikrofon-Berechtigung.
    Zeigt einen Alert, falls Zugriff verweigert wurde.
    
    Returns:
        True wenn Zugriff erlaubt oder (noch) nicht entschieden.
        False wenn explizit verweigert/eingeschränkt.
    """
    status = AVCaptureDevice.authorizationStatusForMediaType_(AVMediaTypeAudio)
    
    if status == AVAuthorizationStatusAuthorized:
        return True
        
    if status == AVAuthorizationStatusNotDetermined:
        # OS wird beim ersten Zugriff fragen
        return True
        
    if status == AVAuthorizationStatusDenied or status == AVAuthorizationStatusRestricted:
        logger.error("Mikrofon-Zugriff verweigert!")
        _show_permission_alert(
            "Mikrofon-Zugriff erforderlich",
            "Whisper Go benötigt Zugriff auf das Mikrofon, um Sprache aufzunehmen.\n\n"
            "Bitte aktiviere es unter:\n"
            "Systemeinstellungen → Datenschutz & Sicherheit → Mikrofon"
        )
        return False
        
    return True


def check_accessibility_permission() -> bool:
    """
    Prüft Accessibility-Berechtigung (für Auto-Paste via CMD+V).
    Zeigt einen Alert, falls Zugriff nicht gewährt.
    
    Returns:
        True wenn Zugriff erlaubt, False wenn nicht.
    """
    if _app_services.AXIsProcessTrusted():
        return True
    
    logger.warning("Accessibility-Berechtigung fehlt - Auto-Paste wird nicht funktionieren")
    _show_permission_alert(
        "Bedienungshilfen-Zugriff empfohlen",
        "Whisper Go benötigt Bedienungshilfen-Zugriff für Auto-Paste (CMD+V).\n\n"
        "Ohne diese Berechtigung wird der Text nur in die Zwischenablage kopiert.\n\n"
        "Aktiviere es unter:\n"
        "Systemeinstellungen → Datenschutz & Sicherheit → Bedienungshilfen"
    )
    return False


def _show_permission_alert(title: str, message: str) -> None:
    """Zeigt modalen Fehler-Dialog."""
    alert = NSAlert.alloc().init()
    alert.setMessageText_(title)
    alert.setInformativeText_(message)
    alert.setAlertStyle_(NSInformationalAlertStyle)
    alert.addButtonWithTitle_("OK")
    alert.runModal()
