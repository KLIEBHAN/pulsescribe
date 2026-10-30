# Building & Notarizing PulseScribe on macOS

PulseScribe ships as a `.app` bundle and a drag‑and‑drop `.dmg`. For a good user experience ("download → open → works"), the DMG should be **Developer‑ID signed + notarized**.

## Prerequisites

- macOS with **Xcode Command Line Tools** (`xcrun`, `notarytool`, `stapler`)
  - Install: `xcode-select --install`
- Python + `pyinstaller`
- Install `requirements.txt` before a full build. Both `mlx-whisper` and
  `lightning-whisper-mlx` are required; Lightning does not install `mlx-whisper`.
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
builds the full Apple Silicon app on the pinned `macos-15` runner, verifies the
bundle version and architecture, mounts and validates the DMG, and stores both the DMG and its
SHA-256 checksum as a workflow artifact for 14 days. The test workflow uses the
same pinned image. `macos-14` is retired by GitHub on November 2, 2026; avoid
`macos-latest` so future image changes do not silently move the build environment.
Local-provider unit tests replace GPU allocator modules with fakes; they must not
call a hosted runner's real CUDA, MPS, or MLX allocator while testing cleanup.
The full build fails if a local backend dependency is missing. CI also runs
`--check-mlx-backend` from the app inside the DMG, checking MLX imports, native
operations and the 80/128 mel-filter assets without downloading a model.

PR builds cancel superseded runs and skip macOS when the whole PR changes only
Windows build files (plus documentation/tests). Shared code, dependencies, unknown
paths, or an unavailable diff keep both platforms enabled. The selection lives in
[installer_platforms.py](../.github/scripts/installer_platforms.py); it uses the
merge-base diff and considers both paths of renames. Releases and manual rebuilds
always build both platforms and are never cancelled by newer PR runs. PR heads
without the selector also build both. Documentation/test-only PRs run only metadata.

Pushing an annotated `vX.Y.Z` tag builds both platforms and publishes the release
only after the installers are uploaded. See [Updates and releases](UPDATES.md).
Publishing a release manually still attaches installers through the same workflow.
An existing release can be rebuilt manually:

```bash
gh workflow run build-installers.yml \
  -f tag=v1.3.0 \
  -f upload_to_release=true
```

Existing assets are not overwritten by default. For an intentional replacement,
add `-f replace_existing_assets=true`.

### Stable signing certificate

macOS stores Microphone, Accessibility and Input Monitoring permissions
per code signature. For an ad-hoc signed app, that is the hash of one build, so
every update would lose the permissions. CI therefore signs releases with the
self-signed "PulseScribe Code Signing" certificate. macOS then identifies the
app by bundle ID and certificate, and permissions survive updates.

The secrets are stored in the GitHub environment `macos-signing`, not as
repository secrets. Anyone holding the key can sign other code that inherits
the users' permissions, so the environment only admits `master` and `v*` tags,
and the workflow uses it only for published builds.

| Environment secret | Content |
|--------------------|---------|
| `MACOS_CERTIFICATE_P12` | Base64 of the `.p12` with certificate and private key |
| `MACOS_CERTIFICATE_PASSWORD` | Password of the `.p12` |

The certificate's SHA-1 is pinned as `MACOS_SIGNING_CERT_SHA1` in
`.github/workflows/build-installers.yml`. Published releases fail without the
secret, and the DMG check rejects apps that are not signed with the pinned
certificate. Pull request and other unpublished builds are ad-hoc signed.

Keep a backup of the `.p12` and its password. Signing a release with a new
certificate, including after losing the old one, makes every user grant the
permissions once more. To replace it, update both secrets and the pinned SHA-1
together.

The certificate is not trusted by Gatekeeper and the build is not notarized, so
the first installation has the same Gatekeeper limitations as a local build. Use
the notarized release process below when Developer ID credentials are available.

For the first installation, copy the app to Applications. If macOS blocks it,
check that the DMG comes from the official GitHub release and matches its entry
in `SHA256SUMS.txt` (`shasum -a 256 PulseScribe-<version>.dmg`). Only then remove
the quarantine flag; this skips Gatekeeper's check for the app:

```bash
xattr -dr com.apple.quarantine /Applications/PulseScribe.app
```

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

- `build_app.sh` signs the app ad-hoc by default (fine for local testing, but
  macOS asks for permissions again after each rebuild)
- `build_dmg.sh` signs with `CODESIGN_IDENTITY` when set; it enables the
  hardened runtime only with `--notarize`
- `build_dmg.sh --notarize` signs with Developer ID and notarizes both `.app` and `.dmg`
- Entitlements are read from `macos/entitlements.plist` (override via `ENTITLEMENTS_PATH`)
- Ad-hoc signed builds may trigger Gatekeeper warnings on other machines
