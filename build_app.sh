#!/bin/bash
# =============================================================================
# PulseScribe App Builder
# =============================================================================
# Erstellt die PulseScribe.app mit PyInstaller.
#
# Usage:
#   ./build_app.sh              # Standard-Build
#   ./build_app.sh --clean      # Cache löschen + Build
#   ./build_app.sh --dmg        # Build + DMG erstellen
#   ./build_app.sh --open       # Build + App starten
# =============================================================================

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

CLEAN="false"
BUILD_DMG="false"
OPEN_APP="false"
SLIM_BUILD="false"

usage() {
    cat <<'EOF'
PulseScribe App Builder

Usage:
  ./build_app.sh [options]

Options:
  --clean       Cache löschen vor dem Build
  --slim        Slim-Build (~300 MB, nur Cloud-Provider, keine lokalen Backends)
  --dmg         Nach dem Build auch DMG erstellen
  --open        App nach dem Build starten
  -h, --help    Hilfe anzeigen

Build-Varianten:
  Standard (Full):  ~1 GB - alle Provider inkl. lokale Whisper-Backends
  Slim:             ~300 MB - nur Deepgram, OpenAI, Groq (kein lokales Whisper)

Beispiele:
  ./build_app.sh                    # Full-Build (Standard)
  ./build_app.sh --slim             # Slim-Build (nur Cloud)
  ./build_app.sh --slim --dmg       # Slim-Build + DMG
  ./build_app.sh --clean --dmg      # Frischer Full-Build + DMG
EOF
}

die() {
    echo -e "${RED}❌ Fehler: $*${NC}" >&2
    exit 1
}

# ---- Args ----
while [ "${1:-}" != "" ]; do
    case "$1" in
        -h|--help)
            usage
            exit 0
            ;;
        --clean)
            CLEAN="true"
            shift
            ;;
        --slim)
            SLIM_BUILD="true"
            shift
            ;;
        --dmg)
            BUILD_DMG="true"
            shift
            ;;
        --open)
            OPEN_APP="true"
            shift
            ;;
        *)
            usage
            die "Unbekannte Option: $1"
            ;;
    esac
done

# Export für PyInstaller spec
export PULSESCRIBE_SLIM_BUILD="$SLIM_BUILD"

echo -e "${GREEN}═══════════════════════════════════════════════════════════════${NC}"
if [ "$SLIM_BUILD" = "true" ]; then
    echo -e "${GREEN}  PulseScribe App Builder (SLIM)${NC}"
else
    echo -e "${GREEN}  PulseScribe App Builder (FULL)${NC}"
fi
echo -e "${GREEN}═══════════════════════════════════════════════════════════════${NC}"
echo ""

if [ "$SLIM_BUILD" = "true" ]; then
    echo -e "${YELLOW}📦 Slim-Build: Nur Cloud-Provider (Deepgram, OpenAI, Groq)${NC}"
    echo -e "${YELLOW}   Lokale Whisper-Backends werden übersprungen${NC}"
    echo ""
fi

# Prüfe PyInstaller
if ! command -v pyinstaller >/dev/null 2>&1; then
    die "PyInstaller nicht gefunden. Installiere mit: pip install pyinstaller"
fi

# Prüfe build_app.spec
if [ ! -f "build_app.spec" ]; then
    die "build_app.spec nicht gefunden. Bist du im richtigen Verzeichnis?"
fi

# Clean wenn gewünscht
if [ "$CLEAN" = "true" ]; then
    echo -e "${YELLOW}🧹 Lösche Build-Cache...${NC}"
    rm -rf build/ dist/PulseScribe.app dist/pulsescribe/
    rm -rf ~/Library/Application\ Support/pyinstaller/
    echo -e "${GREEN}   ✓ Cache gelöscht${NC}"
fi

# Build
echo -e "${YELLOW}🔨 Starte PyInstaller Build...${NC}"
echo ""

if ! pyinstaller build_app.spec --noconfirm; then
    die "PyInstaller Build fehlgeschlagen"
fi

echo ""

# Prüfe Ergebnis
if [ ! -d "dist/PulseScribe.app" ]; then
    die "Build fehlgeschlagen: dist/PulseScribe.app nicht gefunden"
fi

# Signiere App (ad-hoc)
echo -e "${YELLOW}🔐 Signiere App (ad-hoc)...${NC}"
codesign --force --deep --sign - dist/PulseScribe.app
echo -e "${GREEN}   ✓ Signiert${NC}"

# Größe anzeigen
APP_SIZE=$(du -sh dist/PulseScribe.app | cut -f1)

echo ""
echo -e "${GREEN}═══════════════════════════════════════════════════════════════${NC}"
if [ "$SLIM_BUILD" = "true" ]; then
    echo -e "${GREEN}  ✅ Slim-Build erfolgreich!${NC}"
else
    echo -e "${GREEN}  ✅ Full-Build erfolgreich!${NC}"
fi
echo -e "${GREEN}═══════════════════════════════════════════════════════════════${NC}"
echo ""
echo "   📁 App:   dist/PulseScribe.app"
echo "   📊 Größe: ${APP_SIZE}"
if [ "$SLIM_BUILD" = "true" ]; then
    echo "   📦 Typ:   Slim (nur Cloud-Provider)"
else
    echo "   📦 Typ:   Full (mit lokalen Backends)"
fi
echo ""

# DMG erstellen wenn gewünscht
if [ "$BUILD_DMG" = "true" ]; then
    echo -e "${YELLOW}📦 Erstelle DMG...${NC}"
    echo ""
    ./build_dmg.sh
fi

# App öffnen wenn gewünscht
if [ "$OPEN_APP" = "true" ]; then
    echo -e "${YELLOW}🚀 Starte App...${NC}"
    open dist/PulseScribe.app
fi
