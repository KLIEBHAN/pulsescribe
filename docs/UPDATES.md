# Updates and Releases

## Automatic In-App Updates

Installed builds of the native Rust desktop app (`apps/desktop`) check GitHub Releases (`/releases/latest`) automatically **15 seconds after startup** and then **hourly** in the background (source runs via `cargo run` never replace an installed app or download in the background, and manual checks fall back to opening the release page).

- **Variant-aware asset selection:** `Slim` and `Full` builds automatically select the matching signed installer (`PulseScribe-X.Y.Z-macOS-arm64-{Slim,Full}.dmg` or `PulseScribe-X.Y.Z-Windows-x64-{Slim,Full}-Setup.exe`).
- **Cryptographic verification:** All requests enforce per-hop HTTPS-only redirects. Downloaded packages are streamed with exact byte-size enforcement and verified against the release asset's SHA-256 digest (falling back to `SHA256SUMS.txt`) before any preparation step runs.
- **macOS installation:** Mounts the verified DMG read-only (`hdiutil attach -readonly -nobrowse`), verifies `CFBundleIdentifier`, `CFBundleShortVersionString`, and `codesign --verify --deep --strict`, stages `PulseScribe.app` via `ditto` on the same volume, and detaches the DMG. Clicking **Restart & Install** spawns a detached swap helper that waits for the process to exit, atomically swaps `/Applications/PulseScribe.app` with automatic rollback on failure, and relaunches the updated app. Installations running from read-only or translocated locations fall back to opening the verified DMG in `~/Downloads`.
- **Windows installation:** Verifies the downloaded NSIS executable header and spawns a detached PowerShell helper after exit that runs `/S /UPDATE /D=<install_dir>` (preserving `HKCU\...\Run\PulseScribe` autostart, shortcuts, and `%USERPROFILE%\.pulsescribe\`) and relaunches `pulsescribe-desktop.exe` in `finally`.
- **Safety & UX:** Updates never install while dictation is active, never interrupt an active recording, and keep background check failures quiet in the tray menu. User configuration in `~/.pulsescribe/` and pinned-certificate macOS permissions are preserved across upgrades.

## Publishing a Release (`v2.x`)

Release notes live in `docs/releases/vX.Y.Z.md`. Keep user-facing release highlights before `<!-- install-notes -->`; the in-app updater displays only the section before that marker.

1. Bump `version` in both `Cargo.toml` (`[workspace.package]`) and `apps/desktop/tauri.conf.json`, refresh `Cargo.lock`, update `CHANGELOG.md` and `docs/releases/vX.Y.Z.md`, and merge via PR after CI passes.
2. Create and push an annotated Git tag matching `vX.Y.Z`:
   ```bash
   git tag -a v2.2.0 -F docs/releases/v2.2.0.md
   git push origin v2.2.0
   ```
3. [`.github/workflows/rust-installers.yml`](../.github/workflows/rust-installers.yml) validates the tag against `Cargo.toml` and `apps/desktop/tauri.conf.json`, builds and signs:
   - `PulseScribe-X.Y.Z-macOS-arm64-Slim.dmg` & `PulseScribe-X.Y.Z-macOS-arm64-Full.dmg` (signed using the `macos-signing` GitHub environment)
   - `PulseScribe-X.Y.Z-Windows-x64-Slim-Setup.exe` & `PulseScribe-X.Y.Z-Windows-x64-Full-Setup.exe`
   - `SHA256SUMS.txt`
4. The workflow uploads all installers and checksums to a draft release and publishes it as `Latest` only after all uploads succeed (never moving `Latest` backwards).

Lightweight tags are rejected. Published tags must not be moved. Manual rebuilds are available via `workflow_dispatch` on `rust-installers.yml` (`-f tag=vX.Y.Z -f upload_to_release=true`; add `-f replace_existing_assets=true` only to replace existing assets intentionally).

## Focused Validation

```bash
cargo fmt --all -- --check
cargo clippy --workspace --all-targets -- -D warnings
cargo test --workspace
cargo clippy -p pulsescribe-local-asr -p pulsescribe-daemon -p pulsescribe-cli -p pulsescribe-desktop --features all-local --all-targets -- -D warnings
cargo test -p pulsescribe-local-asr -p pulsescribe-daemon -p pulsescribe-cli -p pulsescribe-desktop --features all-local
```
