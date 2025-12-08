#!/bin/bash
#
# install_overlay.sh – Installiert whisper_go Overlay als Launch Agent
#
# Nutzung:
#   ./scripts/install_overlay.sh          # Installieren + Starten
#   ./scripts/install_overlay.sh uninstall # Deinstallieren
#

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
PLIST_NAME="ai.whispergo.overlay"
PLIST_PATH="$HOME/Library/LaunchAgents/${PLIST_NAME}.plist"

# Python-Pfad ermitteln (pyenv bevorzugt)
find_python() {
    if command -v python3 &>/dev/null; then
        python3 -c "import sys; print(sys.executable)"
    else
        echo "/usr/bin/python3"
    fi
}

install() {
    echo "📦 Installiere whisper_go Overlay..."

    PYTHON_PATH=$(find_python)
    OVERLAY_SCRIPT="$PROJECT_DIR/overlay.py"

    # Prüfe ob overlay.py existiert
    if [[ ! -f "$OVERLAY_SCRIPT" ]]; then
        echo "❌ Fehler: $OVERLAY_SCRIPT nicht gefunden"
        exit 1
    fi

    # Prüfe ob PyObjC installiert ist
    if ! "$PYTHON_PATH" -c "import AppKit" 2>/dev/null; then
        echo "⚠️  PyObjC nicht installiert. Installiere..."
        "$PYTHON_PATH" -m pip install pyobjc-framework-Cocoa
    fi

    # Erstelle LaunchAgents Verzeichnis falls nicht vorhanden
    mkdir -p "$HOME/Library/LaunchAgents"

    # Stoppe falls bereits läuft
    launchctl unload "$PLIST_PATH" 2>/dev/null || true

    # Erstelle plist
    cat > "$PLIST_PATH" << EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${PLIST_NAME}</string>
    <key>ProgramArguments</key>
    <array>
        <string>${PYTHON_PATH}</string>
        <string>${OVERLAY_SCRIPT}</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/tmp/whisper_go_overlay.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/whisper_go_overlay.log</string>
</dict>
</plist>
EOF

    # Lade und starte
    launchctl load "$PLIST_PATH"

    echo "✅ Overlay installiert und gestartet!"
    echo "   Log: /tmp/whisper_go_overlay.log"
    echo ""
    echo "   Deinstallieren: $0 uninstall"
}

uninstall() {
    echo "🗑️  Deinstalliere whisper_go Overlay..."

    if [[ -f "$PLIST_PATH" ]]; then
        launchctl unload "$PLIST_PATH" 2>/dev/null || true
        rm "$PLIST_PATH"
        echo "✅ Overlay deinstalliert"
    else
        echo "ℹ️  Nicht installiert"
    fi
}

status() {
    if launchctl list | grep -q "$PLIST_NAME"; then
        echo "✅ Overlay läuft"
        launchctl list "$PLIST_NAME"
    else
        echo "❌ Overlay läuft nicht"
    fi
}

restart() {
    echo "🔄 Starte whisper_go Overlay neu..."

    if [[ -f "$PLIST_PATH" ]]; then
        launchctl unload "$PLIST_PATH" 2>/dev/null || true
        launchctl load "$PLIST_PATH"
        echo "✅ Overlay neu gestartet"
    else
        echo "⚠️  Nicht installiert. Führe Installation durch..."
        install
    fi
}

case "${1:-install}" in
    install)
        install
        ;;
    uninstall|remove)
        uninstall
        ;;
    status)
        status
        ;;
    restart)
        restart
        ;;
    *)
        echo "Nutzung: $0 [install|uninstall|restart|status]"
        exit 1
        ;;
esac
