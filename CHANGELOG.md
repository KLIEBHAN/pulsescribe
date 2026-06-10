# Changelog

All notable changes to PulseScribe are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- **Windows: snappier transitions** – hotkey press/release actions no longer run
  inside the pynput low-level keyboard hook callback. Heavy work (recording
  start/stop, REST capture join, sounds) is dispatched to a dedicated FIFO
  worker, eliminating sporadic system-wide input lag and delayed hold-release
  handling.
- **Windows: decoupled tray updates** – `Shell_NotifyIcon` icon/title updates
  now run coalesced (latest-wins) in a background worker so state transitions
  never wait on a slow Explorer. The tray icon now also reflects the
  LISTENING → RECORDING transition.
- **Windows: paste before history I/O** – transcripts are pasted before the
  history file is written (incl. potential rotation), so the visible
  "text appears" moment no longer waits on disk I/O.

## [1.2.0] - 2025-12-27

### Added

- **Windows support** with dedicated daemon (`pulsescribe_windows.py`)
- **Settings GUI** for Windows with PySide6 (`ui/settings_windows.py`)
- **Onboarding wizard** for first-time Windows setup (`ui/onboarding_wizard_windows.py`)
- **Mica backdrop effect** for Windows 11 22H2+ (modern acrylic look)
- **GPU-accelerated overlay** with PySide6 (`ui/overlay_pyside6.py`)
- Centralized animation logic (`ui/animation.py`) for cross-platform consistency
- PyInstaller spec for Windows EXE builds
- **Inno Setup installer** for Windows (`installer_windows.iss`)
  - Start Menu entries, optional Desktop shortcut
  - Autostart option (adds to Windows startup)
  - Clean uninstall with optional settings removal
  - Per-user install (no admin rights required)
- PowerShell build script (`build_windows.ps1`) for automated builds
- Default hotkeys for Windows (Toggle: Ctrl+Alt+R, Hold: Ctrl+Win)
- RTF (Real-Time Factor) display after transcription
- Auto-reload settings without restart (Windows)
- `--settings` CLI flag for bundled EXE
- Local mode: detailed logging + CUDA timeout (120s)
- Slim build variant (`--slim`) for smaller app size
- **`-Local` build flag** for Windows: includes CUDA Whisper (~4GB) for offline use

### Changed

- Synchronized animations between Windows and macOS
- Tuned animation constants to match macOS feel
- Improved CUDA to CPU fallback with compute_type reset
- Intelligent auto-scroll for logs viewer in Settings

### Fixed

- Hold flag reset in `_stop_recording()` on Windows
- Multiple settings/onboarding windows opening simultaneously
- NVIDIA DLL directories dynamically discovered for cuDNN/cuBLAS
- UTF-8 encoding for prompts.toml on Windows
- Clipboard restore after paste
- Subprocess stdout deadlock prevention (DEVNULL)
- Last word cutoff in Deepgram streaming (drain audio queue before shutdown)
- PySide6 not loading in bundled EXE (missing shiboken6 bindings)

## [1.1.1] - 2025-12-24

### Fixed

- **Critical:** Crash on macOS 26 (Tahoe) due to UI updates from background threads
  - All UI updates now dispatched to main thread via `NSOperationQueue.mainQueue()`
- Missing loading feedback when model loads on-demand (without preload)
- Model name not updating after settings change

### Changed

- Phase-based loading status ("Loading turbo...", "Warming up...")
- Blue loading animation in overlay (distinct from orange transcribing animation)
- Thread-safe `_update_state()` with automatic main-thread dispatching

## [1.1.0] - 2025-12-20

### Added

- **Lightning Mode:** `lightning-whisper-mlx` backend for ~4x faster local transcription on Apple Silicon
- Loading indicator in menu bar during model download/init
- Automatic Lightning → MLX fallback on errors
- 33 new unit tests for Lightning backend

### Fixed

- `[Errno 30] Read-only file system: 'mlx_models'` when running from DMG
  - Lightning models now stored in `~/.pulsescribe/lightning_models/`
- `beam_size` ENV variable incorrectly applied to Lightning/MLX backends
- `language="auto"` now correctly triggers auto-detection

### Changed

- Removed legacy Raycast daemon/IPC code
- Added `mlx_models/` to `.gitignore`

## [1.0.0] - 2025-12-15

### Added

- **System-wide dictation workflow**
  - Global hotkeys (Toggle + Hold-to-record / Push-to-talk)
  - Voice activity detection for fast start/stop
  - Instant feedback via menu bar + animated overlay (~170ms ready time)
  - Auto-paste: copies to clipboard + sends Cmd+V

- **Multiple transcription providers**
  - Deepgram (WebSocket streaming, ~300ms latency)
  - Groq (REST, very fast Whisper on LPU)
  - OpenAI (REST, GPT-4o Transcribe, highest quality)
  - Local (offline, no API costs)
    - `whisper`: openai-whisper (PyTorch), MPS on Apple Silicon
    - `faster`: faster-whisper (CTranslate2), CPU-optimized
    - `mlx`: mlx-whisper (MLX/Metal), Apple Silicon GPU

- **LLM post-processing ("Refine")**
  - Removes filler words, fixes grammar, formats paragraphs
  - Context-aware style based on active app (email/chat/code/default)
  - Spoken punctuation/formatting commands
  - Providers: Groq, OpenAI, OpenRouter

- **Advanced local performance tuning**
  - Settings UI with local knobs (device, warmup, fast decoding, etc.)
  - Built-in presets for common macOS setups (including MLX presets)

- **Custom vocabulary**
  - `~/.pulsescribe/vocabulary.json` for domain-specific terms

- **Native UI**
  - Menu bar status (🎤 🔴 ⏳ ✅ ❌)
  - Always-on-top overlay with animated waveform
  - Settings/Welcome window with Advanced tab

### Configuration

- User data in `~/.pulsescribe/`
  - `.env`: persistent settings
  - `logs/pulsescribe.log`: main log file
  - `startup.log`: emergency startup log
  - `vocabulary.json`: custom vocabulary

---

For detailed release notes, see `docs/releases/`.
