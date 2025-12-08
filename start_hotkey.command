#!/bin/bash
# WhisperGo Hotkey Daemon Starter
# Füge diese Datei zu Login Items hinzu:
# Systemeinstellungen → Allgemein → Anmeldeobjekte → "+"

cd "$(dirname "$0")"

# Python dynamisch ermitteln (Priorität: pyenv → System)
if command -v pyenv &>/dev/null; then
    PYTHON=$(pyenv which python3 2>/dev/null)
fi

if [[ -z "$PYTHON" || ! -x "$PYTHON" ]]; then
    PYTHON=$(which python3 2>/dev/null)
fi

if [[ -z "$PYTHON" ]]; then
    echo "Fehler: Python nicht gefunden"
    exit 1
fi

# .env laden
if [[ -f .env ]]; then
    set -a
    source .env
    set +a
fi

# Daemon starten
exec "$PYTHON" hotkey_daemon.py
