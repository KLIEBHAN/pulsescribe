# Changelog

All notable changes to PulseScribe are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **Windows: adaptive stop tail** – when the audio tail was already silent for
  ~200 ms at hotkey release (the speaker finished talking – the common case),
  the stop grace shrinks to ~50 ms instead of the configured 0.20–0.30 s, so
  text appears noticeably faster. Releasing mid-word keeps the full
  conservative tail, so final words are never clipped. Applies to streaming
  and REST capture; disable via `PULSESCRIBE_WINDOWS_ADAPTIVE_STOP_TAIL=false`.

### Fixed

- **Deepgram SDK pinned to 5.x** – `deepgram-sdk` 7.x removed the 5.x module
  paths the streaming provider relies on (`deepgram.extensions.types.sockets`
  for `ListenV1ControlMessage`), silently breaking KeepAlive/Finalize/
  CloseStream control messages. Requirements now pin `deepgram-sdk>=5,<6`.

### Changed

- **Windows: hotkeys retrigger instantly after release** – the global 300 ms
  hotkey debounce was replaced with per-combo press-cycle tracking: key
  auto-repeat still cannot double-trigger, but after actually releasing the
  key the combo fires again immediately (important for back-to-back
  dictation).
- **Windows: hard paste sync cap** – the clipboard verify loop now uses a
  single-attempt native read (no built-in open-retry sleeps), so
  `PULSESCRIBE_WINDOWS_PASTE_SYNC_MS` is a hard upper bound even when another
  process holds the clipboard lock.
- **Windows: monotonic overlay updates** – overlay state publications carry
  the state generation; a slow old worker can no longer overwrite a newer
  overlay state (e.g. stale DONE after a new recording's LISTENING).
- **Windows: IPC test state under lock** – wizard command id and server are
  snapshotted and cleared under a dedicated lock, hardening overlapping
  IPC test runs.
- **Windows: hotkey works during DONE feedback** – a new recording can start
  immediately after a dictation finishes; the hotkey is no longer swallowed
  during the short green success feedback (~0.6s).
- **Windows: faster paste** – the fixed 50ms clipboard→Ctrl+V delay is now a
  verify loop: paste happens as soon as the clipboard read-back confirms the
  new content (typically <5ms). `PULSESCRIBE_WINDOWS_PASTE_SYNC_MS` remains
  the upper bound for slow clipboard environments (managers/RDP).
- **Deepgram: earlier finalize exit** – the empty-finalize grace window
  (`PULSESCRIBE_DEEPGRAM_EMPTY_FINALIZE_GRACE_SECONDS`) now ends as soon as a
  late final transcript arrives instead of always sleeping the full duration
  (all platforms using Deepgram streaming).
- **Windows: responsiveness boost** – the daemon now requests 1ms system timer
  resolution (`timeBeginPeriod`) and `ABOVE_NORMAL` process priority at
  startup (best-effort, disable via
  `PULSESCRIBE_WINDOWS_RESPONSIVENESS_BOOST=false`). Tightens internal 10-20ms
  polls and keeps hotkey/audio handling snappy under system load.

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
