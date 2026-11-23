# Building PulseScribe on macOS

PulseScribe ships as a native Rust/Tauri v2 `.app` bundle and drag-and-drop `.dmg` (`apps/desktop`, bundle ID `com.kliebhan.pulsescribe`). It combines a zero-build lazy WebView settings UI (0 MB WebView RAM when closed), a native monochrome template menu bar icon, and a native 60 FPS `NSPanel`/`CALayer` overlay.

## Prerequisites

- Rust toolchain (`rustc` ≥ 1.88, `aarch64-apple-darwin` target) and `cargo-tauri` v2 (`cargo install tauri-cli --version ^2 --locked` or prebuilt binary from `tauri-apps/tauri`)
- Xcode Command Line Tools (`xcrun`, `codesign`, `hdiutil`)
- `cmake` — required **only** for the Full variant (`--features all-local`, compiling `whisper.cpp` with Metal)

## Build Variants

```bash
# Release binaries only (Slim / cloud-only)
cargo build --release -p pulsescribe-desktop -p pulsescribe-cli

# Packaged .app bundle (Slim / cloud-only)
(cd apps/desktop && cargo tauri build --target aarch64-apple-darwin --bundles app)

# Packaged .app bundle (Full: static whisper-rs Metal + dynamic sherpa-onnx C-API)
(cd apps/desktop && cargo tauri build --target aarch64-apple-darwin --bundles app --features all-local)
```

- **Bundle metadata & entitlements:** `apps/desktop/Info.plist` carries `NSMicrophoneUsageDescription` and `NSAppleEventsUsageDescription`; `macos/entitlements.plist` supplies `com.apple.security.cs.disable-library-validation`.
- **`sherpa-onnx` runtime (Full):** `crates/local-asr` loads `libsherpa-onnx-c-api.dylib` (`2.0.x` ABI) from `$PULSESCRIBE_SHERPA_LIB_DIR`, `<exe_dir>`, `PulseScribe.app/Contents/Frameworks/`, or `~/.pulsescribe/libs/`. The CI Full build bundles `libsherpa-onnx-c-api.dylib` and `libonnxruntime.dylib` directly into `Contents/Frameworks/`.
- **App & menu bar icons:** Checked-in icons live in `assets/` (`assets/icon.svg`, `assets/icon.icns`, `assets/icon.iconset/`, and `assets/icon_tray_macos_template.{svg,png}`). Normal builds consume them directly without regenerating them.

## CI Build

[`.github/workflows/rust-installers.yml`](../.github/workflows/rust-installers.yml) builds, signs, packages, and verifies both `PulseScribe-<version>-macOS-arm64-Slim.dmg` and `PulseScribe-<version>-macOS-arm64-Full.dmg` (+ `SHA256SUMS.txt`) on `macos-15` for PRs, manual dispatches, and `v*` release tags.

To rebuild an existing release manually:

```bash
gh workflow run rust-installers.yml \
  -f tag=v2.1.0 \
  -f variant=all \
  -f upload_to_release=true
```

Existing assets are not overwritten by default. For an intentional replacement, add `-f replace_existing_assets=true`.

## Stable Signing Certificate

macOS stores Microphone, Accessibility, and Input Monitoring permissions per code signature. For an ad-hoc signed app, that is the hash of a single build, so every update would lose permissions. CI therefore signs published releases with the self-signed `"PulseScribe Code Signing"` certificate so macOS identifies the app by bundle ID and certificate across updates.

The secrets live in the GitHub environment `macos-signing`, which only admits `master` and `v*` tags:

| Environment secret | Content |
|--------------------|---------|
| `MACOS_CERTIFICATE_P12` | Base64 of the `.p12` with certificate and private key |
| `MACOS_CERTIFICATE_PASSWORD` | Password of the `.p12` |

The certificate's SHA-1 is pinned as `MACOS_SIGNING_CERT_SHA1` in `.github/workflows/rust-installers.yml`. Published releases fail without the secret, and the workflow verifies the signature against the pinned certificate. Pull request and other unpublished builds are ad-hoc signed.

Keep a backup of the `.p12` and its password. Signing a release with a new certificate requires users to grant permissions once more. To replace it, update both secrets and the pinned SHA-1 together.

### First Installation (Quarantine Note)

Because the self-signed certificate is not Apple-notarized, macOS Gatekeeper may block the first launch on a new machine. Verify that the DMG matches `SHA256SUMS.txt` from the official GitHub Release (`shasum -a 256 PulseScribe-<version>-macOS-arm64-*.dmg`), copy `PulseScribe.app` to `/Applications`, and clear the quarantine flag if needed:

```bash
xattr -dr com.apple.quarantine /Applications/PulseScribe.app
```
