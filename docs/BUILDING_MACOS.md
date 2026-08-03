# Building & Notarizing PulseScribe on macOS

PulseScribe ships as a `.app` bundle and a drag‑and‑drop `.dmg`. For a good user experience ("download → open → works"), the DMG should be **Developer‑ID signed + notarized**.

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

Output: `dist/PulseScribe.app`

### `build_dmg.sh` — DMG Packaging

Creates a drag‑and‑drop DMG with optional notarization.

```bash
./build_dmg.sh              # Ad-hoc signed DMG (dev)
./build_dmg.sh 1.0.0        # Versioned DMG
./build_dmg.sh 1.0.0 --notarize  # Notarized release
```

Output: `dist/PulseScribe-<version>.dmg`

## CI Build

The [`Build Installers`](../.github/workflows/build-installers.yml) workflow
builds the full Apple Silicon app on `macos-14`, verifies the bundle version and
architecture, mounts and validates the DMG, and stores both the DMG and its
SHA-256 checksum as a workflow artifact for 14 days.

Publishing a GitHub release starts the workflow automatically and attaches the
DMG after both platform builds succeed. An existing release can be rebuilt
manually:

```bash
gh workflow run build-installers.yml \
  -f tag=v1.3.0 \
  -f upload_to_release=true
```

Existing assets are not overwritten by default. For an intentional replacement,
add `-f replace_existing_assets=true`.

The CI build is currently ad-hoc signed and not notarized because the repository
does not have Apple signing credentials configured. It therefore has the same
Gatekeeper limitations as a local development build. Use the notarized release
process below when Developer ID credentials are available.

## Release Build (Notarized)

### 1) Store notary credentials (once)

```bash
xcrun notarytool store-credentials "pulsescribe-notary" \
  --apple-id "you@example.com" \
  --team-id "TEAMID" \
  --password "app-specific-password"
```

### 2) Build + notarize

```bash
export CODESIGN_IDENTITY="Developer ID Application: Your Name (TEAMID)"
export NOTARY_PROFILE="pulsescribe-notary"

./build_app.sh --clean
./build_dmg.sh 1.0.0 --notarize
```

Output: `dist/PulseScribe-1.0.0.dmg` (notarized, Gatekeeper‑friendly)

## Notes

- `build_app.sh` signs the app ad-hoc by default (fine for local testing)
- `build_dmg.sh --notarize` signs with Developer ID and notarizes both `.app` and `.dmg`
- Entitlements are read from `macos/entitlements.plist` (override via `ENTITLEMENTS_PATH`)
- Ad-hoc signed builds may trigger Gatekeeper warnings on other machines
