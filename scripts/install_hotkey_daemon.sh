#!/bin/bash
# Installiert LaunchAgent für whisper_go Hotkey-Daemon
# Verwendet QuickMacHotKey – KEINE Accessibility-Berechtigung nötig!

set -e

PLIST_NAME="com.whispergo.hotkey.plist"
PLIST_PATH="$HOME/Library/LaunchAgents/$PLIST_NAME"
SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

echo "🎤 whisper_go Hotkey-Daemon Installation"
echo "========================================="
echo ""

# ═══════════════════════════════════════════════════════════════════════════
# Auto-Detection: Python-Pfad
# WICHTIG: pyenv-shims funktionieren nicht in LaunchAgents (kein CWD)
# Daher: Echten Python-Pfad ermitteln via pyenv which oder Fallback
# ═══════════════════════════════════════════════════════════════════════════

PYTHON_PATH=""

# 1. Versuche pyenv which (gibt echten Pfad zurück)
if command -v pyenv &>/dev/null; then
    PYTHON_PATH=$(pyenv which python3 2>/dev/null || pyenv which python 2>/dev/null || true)
    if [[ -n "$PYTHON_PATH" && -x "$PYTHON_PATH" ]]; then
        echo "✓ Python via pyenv: $PYTHON_PATH"
    else
        PYTHON_PATH=""
    fi
fi

# 2. Fallback auf bekannte Pfade (KEINE shims!)
if [[ -z "$PYTHON_PATH" ]]; then
    PYTHON_CANDIDATES=(
        "/opt/homebrew/bin/python3"
        "/usr/local/bin/python3"
        "/usr/bin/python3"
    )

    for candidate in "${PYTHON_CANDIDATES[@]}"; do
        if [[ -x "$candidate" ]]; then
            PYTHON_PATH="$candidate"
            echo "✓ Python gefunden: $PYTHON_PATH"
            break
        fi
    done
fi

if [[ -z "$PYTHON_PATH" ]]; then
    echo "✗ Kein Python gefunden. Bitte installieren:"
    echo "  brew install python3"
    exit 1
fi

# Warnung wenn shim erkannt wird
if [[ "$PYTHON_PATH" == *"shims"* ]]; then
    echo "⚠️  Warnung: pyenv-shim erkannt. Das kann Probleme mit LaunchAgent verursachen."
    echo "   Versuche echten Pfad zu ermitteln..."
    REAL_PATH=$(pyenv which python3 2>/dev/null || true)
    if [[ -n "$REAL_PATH" && -x "$REAL_PATH" ]]; then
        PYTHON_PATH="$REAL_PATH"
        echo "✓ Echter Python-Pfad: $PYTHON_PATH"
    fi
fi

# ═══════════════════════════════════════════════════════════════════════════
# Auto-Detection: Script-Pfad
# ═══════════════════════════════════════════════════════════════════════════

DAEMON_SCRIPT="$SCRIPT_DIR/hotkey_daemon.py"

if [[ ! -f "$DAEMON_SCRIPT" ]]; then
    echo "✗ hotkey_daemon.py nicht gefunden: $DAEMON_SCRIPT"
    exit 1
fi
echo "✓ Script gefunden: $DAEMON_SCRIPT"

# ═══════════════════════════════════════════════════════════════════════════
# quickmachotkey-Check
# ═══════════════════════════════════════════════════════════════════════════

if ! "$PYTHON_PATH" -c "import quickmachotkey" 2>/dev/null; then
    echo ""
    echo "⚠️  quickmachotkey nicht installiert. Installiere jetzt..."
    "$PYTHON_PATH" -m pip install quickmachotkey --quiet
    echo "✓ quickmachotkey installiert"
fi

# ═══════════════════════════════════════════════════════════════════════════
# Log-Verzeichnis erstellen
# ═══════════════════════════════════════════════════════════════════════════

mkdir -p "$SCRIPT_DIR/logs"

# ═══════════════════════════════════════════════════════════════════════════
# LaunchAgent erstellen
# ═══════════════════════════════════════════════════════════════════════════

mkdir -p "$(dirname "$PLIST_PATH")"

cat > "$PLIST_PATH" << EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.whispergo.hotkey</string>
    <key>ProgramArguments</key>
    <array>
        <string>$PYTHON_PATH</string>
        <string>$DAEMON_SCRIPT</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>$SCRIPT_DIR/logs/hotkey_daemon.log</string>
    <key>StandardErrorPath</key>
    <string>$SCRIPT_DIR/logs/hotkey_daemon.log</string>
    <key>WorkingDirectory</key>
    <string>$SCRIPT_DIR</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
    </dict>
</dict>
</plist>
EOF

echo "✓ LaunchAgent erstellt: $PLIST_PATH"

# ═══════════════════════════════════════════════════════════════════════════
# LaunchAgent laden
# ═══════════════════════════════════════════════════════════════════════════

# Erst entladen falls bereits geladen
launchctl unload "$PLIST_PATH" 2>/dev/null || true

launchctl load "$PLIST_PATH"
echo "✓ LaunchAgent geladen"

# ═══════════════════════════════════════════════════════════════════════════
# Konfiguration anzeigen
# ═══════════════════════════════════════════════════════════════════════════

# Defaults aus .env laden falls vorhanden
HOTKEY="${WHISPER_GO_HOTKEY:-f19}"
MODE="${WHISPER_GO_HOTKEY_MODE:-toggle}"

if [[ -f "$SCRIPT_DIR/.env" ]]; then
    source "$SCRIPT_DIR/.env" 2>/dev/null || true
    HOTKEY="${WHISPER_GO_HOTKEY:-f19}"
    MODE="${WHISPER_GO_HOTKEY_MODE:-toggle}"
fi

echo ""
echo "🎤 whisper_go Hotkey-Daemon installiert!"
echo ""
echo "   Hotkey: $HOTKEY"
echo "   Modus:  $MODE"
echo ""
echo "   Konfiguration: $SCRIPT_DIR/.env"
echo "   Logs:          $SCRIPT_DIR/logs/hotkey_daemon.log"
echo ""
echo "   Deinstallieren: ./scripts/uninstall_hotkey_daemon.sh"
echo ""
echo "✨ Keine Accessibility-Berechtigung erforderlich!"
echo "   QuickMacHotKey nutzt die native Carbon-API."
echo ""
