"""
Berechtigungs-Checks für macOS (Mikrofon, Accessibility).
"""

import logging
from AppKit import NSAlert, NSInformationalAlertStyle
from AVFoundation import (
    AVCaptureDevice,
    AVMediaTypeAudio,
    AVAuthorizationStatusAuthorized,
    AVAuthorizationStatusDenied,
    AVAuthorizationStatusRestricted,
    AVAuthorizationStatusNotDetermined
)

logger = logging.getLogger("whisper_go")


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


def _show_permission_alert(title: str, message: str) -> None:
    """Zeigt modalen Fehler-Dialog."""
    # Sicherstellen, dass wir im Main-Thread sind (für UI wichtig)
    # Da diese Prüfung beim Start läuft, sind wir meist im Main-Thread.
    
    alert = NSAlert.alloc().init()
    alert.setMessageText_(title)
    alert.setInformativeText_(message)
    alert.setAlertStyle_(NSInformationalAlertStyle)
    alert.addButtonWithTitle_("OK")
    
    # Alert anzeigen (blockiert bis Klick)
    alert.runModal()
