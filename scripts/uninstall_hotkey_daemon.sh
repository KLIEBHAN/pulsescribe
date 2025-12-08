#!/bin/bash
# Deinstalliert whisper_go Hotkey-Daemon

PLIST_PATH="$HOME/Library/LaunchAgents/com.whispergo.hotkey.plist"

echo "🎤 whisper_go Hotkey-Daemon Deinstallation"
echo "==========================================="
echo ""

if [[ -f "$PLIST_PATH" ]]; then
    # LaunchAgent entladen
    launchctl unload "$PLIST_PATH" 2>/dev/null || true
    echo "✓ LaunchAgent entladen"

    # Plist löschen
    rm "$PLIST_PATH"
    echo "✓ LaunchAgent gelöscht: $PLIST_PATH"

    echo ""
    echo "👋 Hotkey-Daemon deinstalliert"
    echo ""
    echo "   Logs wurden nicht gelöscht."
    echo "   Zum Entfernen: rm -rf logs/"
else
    echo "ℹ️  LaunchAgent nicht installiert"
    echo "   Pfad: $PLIST_PATH"
fi
