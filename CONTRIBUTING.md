# Contributing to PulseScribe

Thank you for your interest in contributing to PulseScribe! This guide covers development setup, workspace layout, testing, and PR conventions.

## Getting Started

### Prerequisites

- Rust toolchain (`rustc` ≥ 1.88) with `rustfmt` and `clippy`
- Git
- macOS (Apple Silicon) or Windows (`x86_64-pc-windows-msvc`)
- `cmake` — only needed when building or testing `--features all-local` (`whisper-rs` / `whisper.cpp`)

### Development Setup

```bash
# Clone the repository
git clone https://github.com/KLIEBHAN/pulsescribe.git && cd pulsescribe

# Verify formatting, lints, and tests
cargo fmt --all -- --check
cargo clippy --workspace --all-targets -- -D warnings
cargo test --workspace
```

## Development Workflow

### Running the Application

```bash
# Desktop app (menu bar / system tray + Settings UI + native overlay)
cargo run -p pulsescribe-desktop

# Desktop app with local offline ASR backends (whisper-rs + sherpa-onnx)
cargo run -p pulsescribe-desktop --features all-local

# CLI
cargo run -p pulsescribe-cli -- --record --copy --mode deepgram
```

### Running Tests

```bash
# Standard workspace suite (cloud + core + platform + desktop)
cargo test --workspace

# Targeted crate tests
cargo test -p pulsescribe-desktop
cargo test -p pulsescribe-core

# Local ASR feature matrix (requires cmake)
cargo test -p pulsescribe-local-asr -p pulsescribe-daemon -p pulsescribe-cli -p pulsescribe-desktop --features all-local
```

### Code Style

- MSRV 1.88, `unsafe_code = "forbid"` at the workspace level (OS FFI lives in dedicated platform/overlay crates)
- `cargo fmt --all` and `cargo clippy --workspace --all-targets -- -D warnings` must pass cleanly
- Keep complexity at adapter boundaries; keep `crates/core` pure and free of I/O
- Atomic, focused commits following [Conventional Commits](https://www.conventionalcommits.org/) (`feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `build:`, `chore:`)

## Project Structure

```text
pulsescribe/
├── apps/
│   └── desktop/           # Tauri v2 shell, native tray, zero-build WebView UI
├── crates/
│   ├── core/              # Pure domain logic, state, hotkeys, prompts, history, preferences
│   ├── audio/             # Live microphone capture, WAV I/O, RMS/DSP
│   ├── platform/          # macOS & Windows permissions, clipboard/paste, hotkeys, autostart
│   ├── providers/         # Deepgram (WebSocket/REST), OpenAI, Groq, OpenAI-compatible, LLM refine
│   ├── local-asr/         # Offline ASR engines (whisper-rs Metal/CPU, sherpa-onnx) + model downloader
│   ├── daemon/            # Dictation runtime state machine, preloading, watchdog, IPC
│   ├── overlay/           # Native 60 FPS macOS NSPanel/CALayer & Windows Direct2D overlays
│   └── cli/               # Standalone CLI binary
├── assets/                # App & tray icons (SVG, ICNS, ICO, template PNG)
├── macos/                 # macOS code-signing entitlements
└── docs/                  # Architecture, configuration, build, and release docs
```

## Pull Request Guidelines

- Branch from `master` with focused, reviewable PRs
- Ensure `cargo fmt --all -- --check`, `cargo clippy`, and relevant tests pass before pushing
- Update documentation when changing configuration, build steps, or user-facing behavior:

| Change Type | Update These Files |
|-------------|-------------------|
| New ENV variable | `docs/CONFIGURATION.md`, `docs/KONFIGURATION.md` |
| New provider | `README.md`, `README.de.md`, `docs/CONFIGURATION.md` |
| New CLI flag | `docs/CLI_REFERENCE.md` |
| Version bump | `Cargo.toml`, `apps/desktop/tauri.conf.json`, `Cargo.lock`, `CHANGELOG.md`, `docs/releases/vX.Y.Z.md` |
| New feature | `README.md`, `README.de.md` |
| Security-related | `docs/SECURITY.md`, `docs/SICHERHEIT.md` |

## Reporting Issues

Include your PulseScribe version, OS version, provider/model configuration, and the diagnostics bundle exported from **Settings → Diagnostics → Export Diagnostics**.
