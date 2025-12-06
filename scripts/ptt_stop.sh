#!/bin/bash
# Push-to-Talk: Aufnahme stoppen und Text einfügen
#
# Wird von Karabiner bei Key-Up aufgerufen.
# Sendet SIGUSR1 an den Daemon, wartet auf Transkript, fügt Text ein.

set -euo pipefail

PID_FILE="/tmp/whisper_go.pid"
TRANSCRIPT_FILE="/tmp/whisper_go.transcript"
ERROR_FILE="/tmp/whisper_go.error"
TIMEOUT_SECONDS=30

# Prüfen ob Aufnahme läuft
[[ ! -f "$PID_FILE" ]] && exit 0

PID=$(cat "$PID_FILE")

# Prüfen ob Prozess existiert
if ! kill -0 "$PID" 2>/dev/null; then
    rm -f "$PID_FILE"
    exit 0
fi

# SIGUSR1 senden (stoppt Aufnahme, startet Transkription)
kill -USR1 "$PID"

# Warten auf Ergebnis
ITERATIONS=$((TIMEOUT_SECONDS * 10))
for ((i = 0; i < ITERATIONS; i++)); do
    # Fehler prüfen
    if [[ -f "$ERROR_FILE" ]]; then
        ERROR_MSG=$(cat "$ERROR_FILE")
        rm -f "$ERROR_FILE"
        osascript -e "display notification \"$ERROR_MSG\" with title \"Whisper Go\" sound name \"Basso\""
        exit 1
    fi

    # Transkript prüfen
    if [[ -f "$TRANSCRIPT_FILE" ]]; then
        TEXT=$(cat "$TRANSCRIPT_FILE")
        rm -f "$TRANSCRIPT_FILE"

        # Text in Zwischenablage und einfügen
        echo -n "$TEXT" | pbcopy
        osascript -e 'tell application "System Events" to keystroke "v" using command down'

        # Erfolgs-Sound (optional, auskommentiert)
        # afplay /System/Library/Sounds/Pop.aiff &
        exit 0
    fi

    sleep 0.1
done

# Timeout
osascript -e 'display notification "Transkription fehlgeschlagen (Timeout)" with title "Whisper Go" sound name "Basso"'
exit 1
