import sys
import os

def get_resource_path(relative_path: str) -> str:
    """
    Gibt den absoluten Pfad zu einer Ressource zurück.
    Funktioniert sowohl im Entwicklungs-Modus als auch in der PyInstaller .app.
    """
    if hasattr(sys, '_MEIPASS'):
        # PyInstaller entpackt Daten in sys._MEIPASS
        return os.path.join(sys._MEIPASS, relative_path)
    
    # Im Entwicklungsmodus ist der Pfad relativ zum aktuellen Arbeitsverzeichnis
    # oder zum Skript-Speicherort
    return os.path.join(os.path.abspath("."), relative_path)
