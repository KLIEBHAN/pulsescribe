# Building PulseScribe on Windows

PulseScribe ships as a native Rust/Tauri v2 desktop executable and per-user NSIS installer (`apps/desktop`). It combines a zero-build lazy WebView2 settings window (0 MB WebView RAM when closed), a native system tray, and a native 60 FPS Direct2D/DirectWrite overlay.

## Prerequisites

- Rust toolchain (`rustc` ≥ 1.88, `x86_64-pc-windows-msvc` target) with Visual Studio Build Tools (MSVC)
- `cargo-tauri` v2 (`cargo install tauri-cli --version ^2 --locked` or prebuilt `cargo-tauri-x86_64-pc-windows-msvc.zip` from `tauri-apps/tauri`)
- `cmake` — required **only** for the Full variant (`--features all-local`, compiling `whisper.cpp`)

## Build Variants

```powershell
# Release binaries only (Slim / cloud-only)
cargo build --release -p pulsescribe-desktop -p pulsescribe-cli

# Per-user NSIS installer (Slim / cloud-only, English & German)
Push-Location apps\desktop
cargo tauri build --target x86_64-pc-windows-msvc --bundles nsis
Pop-Location

# Per-user NSIS installer (Full: static whisper-rs + dynamic sherpa-onnx C-API)
Push-Location apps\desktop
cargo tauri build --target x86_64-pc-windows-msvc --bundles nsis --features all-local
Pop-Location
```

- **`sherpa-onnx` runtime (Full):** `crates/local-asr` dynamically loads `sherpa-onnx-c-api.dll` (`2.0.x` ABI) from `$PULSESCRIBE_SHERPA_LIB_DIR`, the executable directory, or `%USERPROFILE%\.pulsescribe\libs\`. The CI Full build bundles `sherpa-onnx-c-api.dll`, `onnxruntime.dll`, and `onnxruntime_providers_shared.dll` next to `pulsescribe-desktop.exe` in the NSIS install root.
- **Installer configuration:** Configured in `apps/desktop/tauri.conf.json` (`installMode: "currentUser"`, English & German wizard languages, and `apps/desktop/nsis/hooks.nsh` for migrating legacy autostart entries).
- **Icons:** Uses the checked-in `assets/icon.ico` and `assets/icon.iconset/icon_32x32.png`.

## CI Build

[`.github/workflows/rust-installers.yml`](../.github/workflows/rust-installers.yml) builds and validates both `PulseScribe-<version>-Windows-x64-Slim-Setup.exe` and `PulseScribe-<version>-Windows-x64-Full-Setup.exe` (+ `SHA256SUMS.txt`) on `windows-latest` for PRs, manual dispatches, and `v*` release tags.

To rebuild an existing release manually:

```powershell
gh workflow run rust-installers.yml `
  -f tag=v2.1.0 `
  -f variant=all `
  -f upload_to_release=true
```

## Configuration & Troubleshooting

Configuration is stored in `%USERPROFILE%\.pulsescribe\.env` (and `preferences.json`, `vocabulary.json`, `prompts.toml`, `history.jsonl`).

| Problem | Solution |
|---------|----------|
| Windows SmartScreen warning | Unsigned builds may trigger SmartScreen on first launch; click **More info** → **Run anyway** after verifying `SHA256SUMS.txt` |
| Overlay not showing | Check `PULSESCRIBE_OVERLAY=true` in Settings → General |
| Hotkeys not firing in elevated apps | Elevated windows require an elevated PulseScribe instance on Windows (UIPI) |
| Local build fails without `cmake` | Install CMake or build the Slim (cloud-only) variant without `--features all-local` |
