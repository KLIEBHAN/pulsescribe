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

from pathlib import Path

import rumps

# IPC-Dateien (synchron mit transcribe.py)
STATE_FILE = Path("/tmp/whisper_go.state")
PID_FILE = Path("/tmp/whisper_go.pid")

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


class WhisperGoStatus(rumps.App):
    """Menübar-App für whisper_go Status-Anzeige."""

    def __init__(self):
        super().__init__(ICONS["idle"], quit_button="Beenden")
        self.timer = rumps.Timer(self.poll_state, POLL_INTERVAL)
        self.timer.start()
        self._last_state = "idle"

    def poll_state(self, _sender):
        """Liest aktuellen State aus IPC-Datei."""
        state = self._read_state()

        # Nur aktualisieren wenn sich State geändert hat
        if state != self._last_state:
            self.title = ICONS.get(state, ICONS["idle"])
            self._last_state = state

    def _read_state(self) -> str:
        """Ermittelt aktuellen State aus IPC-Dateien."""
        # Primär: STATE_FILE
        if STATE_FILE.exists():
            try:
                return STATE_FILE.read_text().strip()
            except (OSError, IOError):
                pass

        # Fallback: PID_FILE (für Abwärtskompatibilität)
        if PID_FILE.exists():
            return "recording"

        return "idle"


def main():
    """Startet die Menübar-App."""
    WhisperGoStatus().run()


if __name__ == "__main__":
    main()
