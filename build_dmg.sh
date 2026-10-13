#!/bin/bash
# =============================================================================
# PulseScribe DMG Builder
# =============================================================================
# Erstellt eine DMG-Datei für die Distribution.
#
# Dev/Debug:
#   - ad-hoc Signatur (kein Notarization) für schnelle lokale Tests.
#
# Release (CI):
#   - Signatur mit dem festen PulseScribe-Zertifikat (CODESIGN_IDENTITY).
#     macOS behält Mikrofon-, Bedienungshilfen- und Eingabeüberwachungs-
#     Freigaben nur, wenn jedes Update mit demselben Zertifikat signiert ist;
#     ad-hoc signierte Updates verlieren sie.
#
# Release (Apple):
#   - Developer ID Signing + Hardened Runtime + Notarization + Stapling.
#
# Voraussetzungen:
#   - PyInstaller Build existiert: dist/PulseScribe.app
#   - Xcode Command Line Tools (für xcrun/notarytool): xcode-select --install
#
# Setup (einmalig) für Notarization:
#   xcrun notarytool store-credentials "pulsescribe-notary" \
#     --apple-id "you@example.com" --team-id "TEAMID" --password "app-specific-password"
#
# Usage:
#   ./build_dmg.sh                       # version aus pyproject.toml, ad-hoc signed
#   ./build_dmg.sh 1.0.0                 # ad-hoc signed
#
#   CODESIGN_IDENTITY="Developer ID Application: Your Name (TEAMID)" \
#   NOTARY_PROFILE="pulsescribe-notary" \
#   ./build_dmg.sh 1.0.0 --notarize
# =============================================================================

set -euo pipefail

APP_NAME="PulseScribe"
APP_PATH="dist/${APP_NAME}.app"

VERSION=""
NOTARIZE="false"

# Override via env (recommended for CI/local)
CODESIGN_IDENTITY="${CODESIGN_IDENTITY:-${PULSESCRIBE_CODESIGN_IDENTITY:-"-"}}"
NOTARY_PROFILE="${NOTARY_PROFILE:-${PULSESCRIBE_NOTARY_PROFILE:-""}}"
ENTITLEMENTS_PATH="${ENTITLEMENTS_PATH:-${PULSESCRIBE_ENTITLEMENTS_PATH:-"macos/entitlements.plist"}}"

DMG_NAME=""
DMG_PATH=""
VOLUME_NAME=""

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

usage() {
    cat <<'EOF'
PulseScribe DMG Builder

Usage:
  ./build_dmg.sh [version] [--notarize] [--identity "Developer ID Application: ..."] [--profile PROFILE]

Options:
  --version VERSION        Version für DMG-Dateiname (Default: aus pyproject.toml)
  --notarize               Notarize + staple (erfordert Developer ID Signing + NOTARY_PROFILE)
  --identity IDENTITY      Codesign Identity, Name oder SHA-1 (Default: $CODESIGN_IDENTITY, '-' = ad-hoc)
  --profile PROFILE        notarytool Keychain Profile (Default: $NOTARY_PROFILE)
  --entitlements PATH      Entitlements file (Default: macos/entitlements.plist)
  -h, --help               Hilfe anzeigen
EOF
}

die() {
    echo -e "${RED}❌ Fehler: $*${NC}" >&2
    exit 1
}

have_cmd() {
    command -v "$1" >/dev/null 2>&1
}

read_version_from_pyproject() {
    python3 - <<'PY'
from __future__ import annotations

import pathlib
import sys

try:
    import tomllib  # py3.11+
except Exception:  # pragma: no cover
    tomllib = None  # type: ignore

pyproject = pathlib.Path("pyproject.toml")
if not pyproject.exists() or tomllib is None:
    print("1.0.0")
    sys.exit(0)

data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
print(data.get("project", {}).get("version", "1.0.0"))
PY
}

notarize_file() {
    local file_path="$1"
    xcrun notarytool submit "$file_path" --keychain-profile "$NOTARY_PROFILE" --wait
}

sign_app() {
    local identity="$1"
    if [ "$identity" = "-" ]; then
        echo -e "${YELLOW}🔐 Signiere App (ad-hoc)...${NC}"
        codesign --force --deep --sign - "$APP_PATH"
        return
    fi

    local args=(--force --deep --sign "$identity")
    # Notarization requires the hardened runtime; other builds keep the
    # unrestricted runtime of ad-hoc builds.
    if [ "$NOTARIZE" = "true" ]; then
        echo -e "${YELLOW}🔐 Signiere App (${identity}, hardened runtime)...${NC}"
        args+=(--options runtime)
        if [ -f "$ENTITLEMENTS_PATH" ]; then
            args+=(--entitlements "$ENTITLEMENTS_PATH")
        fi
    else
        echo -e "${YELLOW}🔐 Signiere App (${identity})...${NC}"
    fi
    codesign "${args[@]}" "$APP_PATH"
}

verify_app_signature() {
    echo -e "${YELLOW}🔍 Verifiziere App-Signatur...${NC}"
    codesign --verify --deep --strict "$APP_PATH"
    if have_cmd spctl; then
        spctl -a -vv --type exec "$APP_PATH" || true
    fi
    echo -e "${GREEN}   ✓ OK${NC}"
}

build_dmg() {
    local dmg_temp="dist/dmg_content"
    rm -rf "$dmg_temp"
    mkdir -p "$dmg_temp"

    cp -R "$APP_PATH" "$dmg_temp/"
    ln -s /Applications "$dmg_temp/Applications"

    hdiutil create -volname "$VOLUME_NAME" \
        -srcfolder "$dmg_temp" \
        -ov -format UDZO \
        "$DMG_PATH"

    rm -rf "$dmg_temp"
}

sign_dmg() {
    local identity="$1"
    if [ "$identity" = "-" ]; then
        return
    fi
    echo -e "${YELLOW}🔐 Signiere DMG (${identity})...${NC}"
    codesign --force --sign "$identity" "$DMG_PATH"
    echo -e "${GREEN}   ✓ DMG signiert${NC}"
}

verify_dmg_signature() {
    if ! have_cmd codesign; then
        return
    fi
    echo -e "${YELLOW}🔍 Verifiziere DMG...${NC}"
    codesign --verify --strict "$DMG_PATH" || true
    if have_cmd spctl; then
        spctl -a -vv --type open "$DMG_PATH" || true
    fi
}

# ---- Args ----
while [ "${1:-}" != "" ]; do
    case "$1" in
        -h|--help)
            usage
            exit 0
            ;;
        --version)
            VERSION="${2:-}"
            shift 2
            ;;
        --notarize)
            NOTARIZE="true"
            shift
            ;;
        --identity)
            CODESIGN_IDENTITY="${2:-}"
            shift 2
            ;;
        --profile)
            NOTARY_PROFILE="${2:-}"
            shift 2
            ;;
        --entitlements)
            ENTITLEMENTS_PATH="${2:-}"
            shift 2
            ;;
        --)
            shift
            break
            ;;
        -*)
            usage
            die "Unbekannte Option: $1"
            ;;
        *)
            if [ -z "$VERSION" ]; then
                VERSION="$1"
                shift
            else
                usage
                die "Unerwartetes Argument: $1"
            fi
            ;;
    esac
done

if [ -z "$VERSION" ]; then
    VERSION="$(read_version_from_pyproject)"
fi

DMG_NAME="${APP_NAME}-${VERSION}"
DMG_PATH="dist/${DMG_NAME}.dmg"
VOLUME_NAME="${APP_NAME} ${VERSION}"

echo -e "${GREEN}═══════════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}  PulseScribe DMG Builder - Version ${VERSION}${NC}"
echo -e "${GREEN}═══════════════════════════════════════════════════════════════${NC}"
echo ""

[ -d "$APP_PATH" ] || die "${APP_PATH} nicht gefunden! Führe zuerst aus: pyinstaller build_app.spec --clean"

echo -e "${YELLOW}ℹ️  Identity: ${CODESIGN_IDENTITY}${NC}"
if [ "$NOTARIZE" = "true" ]; then
    echo -e "${YELLOW}ℹ️  Notarize: enabled (profile: ${NOTARY_PROFILE})${NC}"
fi

sign_app "$CODESIGN_IDENTITY"
verify_app_signature

if [ "$NOTARIZE" = "true" ]; then
    [ "$CODESIGN_IDENTITY" != "-" ] || die "--notarize erfordert Developer ID Signing (setze --identity oder CODESIGN_IDENTITY)"
    [ -n "$NOTARY_PROFILE" ] || die "--notarize erfordert NOTARY_PROFILE (Keychain profile von notarytool)"
    have_cmd xcrun || die "xcrun nicht gefunden (Xcode Command Line Tools installieren)"

    echo -e "${YELLOW}🧾 Notarize App (zip) + staple...${NC}"
    ZIP_PATH="dist/${APP_NAME}-${VERSION}.app.zip"
    rm -f "$ZIP_PATH"
    ditto -c -k --keepParent "$APP_PATH" "$ZIP_PATH"
    notarize_file "$ZIP_PATH"
    rm -f "$ZIP_PATH"
    xcrun stapler staple "$APP_PATH"
    xcrun stapler validate "$APP_PATH"
    echo -e "${GREEN}   ✓ App notarized + stapled${NC}"
fi

if [ -f "$DMG_PATH" ]; then
    echo -e "${YELLOW}🗑  Entferne alte DMG...${NC}"
    rm "$DMG_PATH"
fi

echo -e "${YELLOW}📦 Erstelle DMG (Drag & Drop → /Applications)...${NC}"
build_dmg

sign_dmg "$CODESIGN_IDENTITY"
verify_dmg_signature

if [ "$NOTARIZE" = "true" ]; then
    echo -e "${YELLOW}🧾 Notarize DMG + staple...${NC}"
    notarize_file "$DMG_PATH"
    xcrun stapler staple "$DMG_PATH"
    xcrun stapler validate "$DMG_PATH"
    verify_dmg_signature
    echo -e "${GREEN}   ✓ DMG notarized + stapled${NC}"
fi

echo ""
echo -e "${GREEN}═══════════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}  ✅ DMG erfolgreich erstellt!${NC}"
echo -e "${GREEN}═══════════════════════════════════════════════════════════════${NC}"
echo ""
echo "   📁 Datei: ${DMG_PATH}"
echo "   📊 Größe: $(du -h "$DMG_PATH" | cut -f1)"
echo ""
echo "   Nächste Schritte:"
echo "   1. DMG testen: open ${DMG_PATH}"
echo "   2. GitHub Release erstellen:"
echo "      gh release create v${VERSION} ${DMG_PATH} --title \"v${VERSION}\" --generate-notes"
echo ""
