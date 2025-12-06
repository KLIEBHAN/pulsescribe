#!/bin/bash
# Push-to-Talk: Aufnahme starten
#
# Wird von Karabiner bei Key-Down aufgerufen.
# Startet den Python-Daemon im Hintergrund.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TRANSCRIBE="${SCRIPT_DIR}/../transcribe.py"
PID_FILE="/tmp/whisper_go.pid"

# Python finden (pyenv bevorzugt)
if [[ -x "${HOME}/.pyenv/shims/python3" ]]; then
    PYTHON="${HOME}/.pyenv/shims/python3"
elif [[ -x "/opt/homebrew/bin/python3" ]]; then
    PYTHON="/opt/homebrew/bin/python3"
else
    PYTHON="/usr/bin/python3"
fi

# Falls bereits eine Aufnahme läuft, nichts tun
[[ -f "$PID_FILE" ]] && exit 0

# Daemon starten (detached, Ausgabe unterdrückt)
nohup "$PYTHON" "$TRANSCRIBE" --record-daemon --language de >/dev/null 2>&1 &
