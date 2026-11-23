# Updates and Releases

## In-App Update Check

The native Rust desktop app (`apps/desktop`) checks GitHub Releases (`/releases/latest`) over HTTPS with SemVer comparison when triggered via **Check for Updates…** in the menu bar/tray or **Diagnostics → Check for Updates**. When a newer release is available, it opens the verified GitHub Release page in the default browser (**Download Release**).

- User configuration in `~/.pulsescribe/` (`.env`, `preferences.json`, `vocabulary.json`, `prompts.toml`, `history.jsonl`, downloaded models) is preserved across upgrades.
- On macOS, releases signed with the pinned `"PulseScribe Code Signing"` certificate preserve Microphone, Accessibility, and Input Monitoring permissions across updates.
- On Windows, the NSIS per-user installer updates in place and migrates legacy autostart entries (`HKCU\...\Run\PulseScribe`) automatically.

## Publishing a Release (`v2.x`)

Release notes live in `docs/releases/vX.Y.Z.md`.

1. Bump `version` in both `Cargo.toml` (`[workspace.package]`) and `apps/desktop/tauri.conf.json`, refresh `Cargo.lock`, update `CHANGELOG.md` and `docs/releases/vX.Y.Z.md`, and merge via PR after CI passes.
2. Create and push an annotated Git tag matching `vX.Y.Z`:
   ```bash
   git tag -a v2.1.0 -F docs/releases/v2.1.0.md
   git push origin v2.1.0
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
