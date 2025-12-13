# Building & Notarizing WhisperGo on macOS

WhisperGo ships as a `.app` bundle and a drag‑and‑drop `.dmg`. For a good user experience ("download → open → works"), the DMG should be **Developer‑ID signed + notarized**.

## Prerequisites

- macOS with **Xcode Command Line Tools** (`xcrun`, `notarytool`, `stapler`)
  - Install: `xcode-select --install`
- Python + `pyinstaller`
- (For notarization) an Apple Developer Program membership with a **Developer ID Application** certificate installed in your keychain

## Quick Start

```bash
# Build the app (ad-hoc signed)
./build_app.sh

# Build + create DMG
./build_app.sh --dmg

# Clean build + DMG + launch
./build_app.sh --clean --dmg --open
```

## Build Scripts

### `build_app.sh` — Main Build Script

| Option    | Description                        |
| --------- | ---------------------------------- |
| `--clean` | Delete build cache before building |
| `--dmg`   | Also create DMG after building     |
| `--open`  | Launch the app after building      |

Examples:

```bash
./build_app.sh                    # Standard build
./build_app.sh --clean --dmg      # Fresh build + DMG
./build_app.sh --open             # Build + launch
```

Output: `dist/WhisperGo.app`

### `build_dmg.sh` — DMG Packaging

Creates a drag‑and‑drop DMG with optional notarization.

```bash
./build_dmg.sh              # Ad-hoc signed DMG (dev)
./build_dmg.sh 1.0.0        # Versioned DMG
./build_dmg.sh 1.0.0 --notarize  # Notarized release
```

Output: `dist/WhisperGo-<version>.dmg`

## Release Build (Notarized)

### 1) Store notary credentials (once)

```bash
xcrun notarytool store-credentials "whispergo-notary" \
  --apple-id "you@example.com" \
  --team-id "TEAMID" \
  --password "app-specific-password"
```

### 2) Build + notarize

```bash
export CODESIGN_IDENTITY="Developer ID Application: Your Name (TEAMID)"
export NOTARY_PROFILE="whispergo-notary"

./build_app.sh --clean
./build_dmg.sh 1.0.0 --notarize
```

Output: `dist/WhisperGo-1.0.0.dmg` (notarized, Gatekeeper‑friendly)

## Notes

- `build_app.sh` signs the app ad-hoc by default (fine for local testing)
- `build_dmg.sh --notarize` signs with Developer ID and notarizes both `.app` and `.dmg`
- Entitlements are read from `macos/entitlements.plist` (override via `ENTITLEMENTS_PATH`)
- Ad-hoc signed builds may trigger Gatekeeper warnings on other machines
