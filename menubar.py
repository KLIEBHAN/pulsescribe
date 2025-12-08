#!/usr/bin/env python3
"""
menubar.py – Menübar-Status für whisper_go

Zeigt den aktuellen Aufnahme-Status in der macOS-Menüleiste an:
- 🎤 Idle (bereit)
- 🔴 Recording (Aufnahme läuft)
- ⏳ Transcribing (Transkription läuft)
- ✅ Done (erfolgreich)
- ❌ Error (Fehler)

Nutzung:
    python menubar.py

Voraussetzung:
    pip install rumps
"""

import os
from pathlib import Path

import rumps

# IPC-Dateien (synchron mit transcribe.py)
STATE_FILE = Path("/tmp/whisper_go.state")
PID_FILE = Path("/tmp/whisper_go.pid")
INTERIM_FILE = Path("/tmp/whisper_go.interim")

# Status-Icons
ICONS = {
    "idle": "🎤",
    "recording": "🔴",
    "transcribing": "⏳",
    "done": "✅",
    "error": "❌",
}

# Polling-Intervall in Sekunden
POLL_INTERVAL = 0.2

# Maximale Länge für Interim-Preview
MAX_PREVIEW_LENGTH = 25


def truncate_text(text: str, max_length: int = MAX_PREVIEW_LENGTH) -> str:
    """Kürzt Text für Menübar-Anzeige."""
    text = text.strip()
    if len(text) <= max_length:
        return text
    return text[:max_length].rstrip() + "…"


class WhisperGoStatus(rumps.App):
    """Menübar-App für whisper_go Status-Anzeige."""

    def __init__(self):
        super().__init__(ICONS["idle"], quit_button="Beenden")
        self.timer = rumps.Timer(self.poll_state, POLL_INTERVAL)
        self.timer.start()

    def poll_state(self, _sender):
        """Liest aktuellen State und optional Interim-Text."""
        state = self._read_state()

        # Interim-Text nur während Recording anzeigen
        if state == "recording":
            interim_text = self._read_interim()
            if interim_text:
                new_title = f"{ICONS['recording']} {truncate_text(interim_text)}"
            else:
                new_title = ICONS["recording"]
        else:
            new_title = ICONS.get(state, ICONS["idle"])

        # Nur aktualisieren wenn sich Titel geändert hat
        if new_title != self.title:
            self.title = new_title

    def _read_state(self) -> str:
        """Ermittelt aktuellen State aus IPC-Dateien."""
        # Primär: STATE_FILE (direkt lesen, keine Race Condition)
        try:
            state = STATE_FILE.read_text().strip()
            if state:
                return state
        except FileNotFoundError:
            pass  # Erwartet: Keine Aufnahme aktiv
        except OSError:
            pass  # Disk-Fehler → Fallback auf PID_FILE/idle

        # Fallback: PID_FILE (für Abwärtskompatibilität)
        if self._is_process_alive():
            return "recording"

        # Prozess tot oder PID-Datei fehlt → versuche aufzuräumen
        try:
            PID_FILE.unlink()
        except FileNotFoundError:
            pass  # Erwartet: Datei war schon weg
        except OSError:
            pass  # Cleanup nicht kritisch, ignorieren

        return "idle"

    def _is_process_alive(self) -> bool:
        """Prüft ob der Daemon-Prozess noch läuft."""
        try:
            pid = int(PID_FILE.read_text().strip())
            # Signal 0 prüft nur ob Prozess existiert, sendet nichts
            os.kill(pid, 0)
            return True
        except (ValueError, OSError, IOError):
            return False

    def _read_interim(self) -> str | None:
        """Liest aktuellen Interim-Text für Live-Preview."""
        try:
            text = INTERIM_FILE.read_text().strip()
            return text or None
        except FileNotFoundError:
            return None  # Erwartet: Noch kein Interim-Result
        except OSError:
            return None  # Disk-Fehler → kein Preview, nicht kritisch


def main():
    """Startet die Menübar-App."""
    WhisperGoStatus().run()


if __name__ == "__main__":
    main()
