# Changelog

All notable changes to PulseScribe are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **Microphone selection** – choose an input device in Settings → Providers on
  macOS and Windows. Save & Apply uses it from the next recording, preserves
  device names across restarts, and falls back automatically if it is unavailable.
  Refresh detects newly connected devices without opening an audio stream.

## [1.7.2] - 2026-10-28

### Fixed

- **Manual macOS update retry** – if opening the downloaded installer fails, the
  verified package is restored for another attempt instead of leaving a missing
  staging file. Existing downloads are preserved.
- **Windows editor drafts** – returning to Vocabulary preserves unsaved edits even
  if its file changed. Explicit reload asks before discarding drafts. Restoring a
  saved custom prompt to its default keeps the Save button enabled until saved.
- **macOS success feedback** – returning to ready no longer hides the brief
  success confirmation immediately. New dictation replaces it without waiting;
  feedback animations stop when the overlay hides.
- **macOS package validation** – transient busy-volume errors use a bounded
  graceful detach retry instead of immediately failing an otherwise valid build.

### Changed

- **Quieter macOS overlay** – pause the recording-level animation timer once
  silent levels and bars have settled; new audio resumes rendering as before.

## [1.7.1] - 2026-10-27

### Fixed

- **Save & Apply feedback** – macOS and Windows report prompt and vocabulary
  write failures as partial saves, retain unsaved drafts, and avoid a misleading
  success confirmation. A failed save also prevents a macOS relaunch.
- **Unsaved Windows settings** – closing the window, pressing Escape, or using
  Close asks to save, discard, or cancel only when drafts have changed. A failed
  or incomplete save keeps the window and its drafts open.
- **Error feedback and retry** – errors stay readable for four seconds. Completed
  Windows error paths allow a new dictation before the message disappears; warm
  capture waits for cleanup. Old reset timers cannot interrupt a new recording.

### Changed

- **macOS history saves** – write accepted transcripts and their timing diagnostics
  on an ordered background worker instead of the UI thread. Clear History respects
  pending saves, and normal quit/update paths drain accepted entries before exit.

## [1.7.0] - 2026-10-27

### Added

- **Copy Last Transcript** in the macOS menu bar and Windows tray menu recovers
  the last normal dictation of the current session, even if saving history fails.

### Fixed

- **Paste feedback** appears after the paste attempt, reports clipboard fallback
  or copy failures, and no longer plays a success sound on failure.
- Explicitly copying a transcript is no longer undone by a pending automatic
  clipboard restore, even when the text is identical.

### Changed

- **Leaner Windows installers** – exclude unused Qt modules while retaining the
  API-only and Local Whisper packages.
- **Shared settings and history handling** – remove duplicated normalization and
  unnecessary formatting layers without changing saved settings or transcript output.

## [1.6.1] - 2026-10-13

### Fixed

- **macOS permissions after updates** – releases are signed with a stable
  certificate, so Microphone, Accessibility and Input Monitoring stay granted
  after an update. The first update from 1.6.0 or earlier asks for them once more.

## [1.6.0] - 2026-10-13

### Added

- **Update window on macOS and Windows** – shows checking, download progress,
  scrollable release notes and the install button. Automatic updates open it
  once per version without taking focus; the menu entry reopens it at any time.

### Fixed

- **Local Whisper updates on Windows** – every release includes the
  `-Local.zip` package again, which Local Whisper installations require.
- **Switching to the API-only installer** – after a successful installation
  it removes Local Whisper leftovers, so future updates use the API-only package.
- **Unreachable install button on macOS** – long release notes no longer push
  the confirmation buttons off screen.

## [1.5.0] - 2026-10-13

### Added

- **Windows tray menu like the macOS menu bar** – shows the version, live status
  and hint, and adds Open Log File and Export Redacted Diagnostics.

### Changed

- **Windows settings match macOS** – a centered tab bar shows all tabs, dropdown
  arrows and checkmarks are drawn, and window height, type scale, footer and
  every tab follow the macOS layout, including the Setup “Try it” card.
- **Windows overlay matches macOS** – same height, corner radius, 15 px
  semibold label and teal success color.

### Fixed

- **Vocabulary edits on Windows** – Save & Apply also saves pending vocabulary
  edits, as on macOS, and warns if they cannot be written.
- **Long overlay text on Windows** – labels end in an ellipsis instead of being
  clipped on both sides; live transcripts keep their newest words.
- **macOS “Try it” card** – no longer shows “(nicht konfiguriert)” as a hotkey.

## [1.4.2] - 2026-10-13

### Fixed

- **Local MLX transcription on macOS** – include the missing `mlx-whisper`
  package so the local model can preload in the released app.
- **Incomplete macOS packages** – full builds reject missing local backend
  dependencies and validate MLX operations and mel-filter assets in the app
  inside the DMG before publication.

## [1.4.1] - 2026-10-13

### Fixed

- **Packaged macOS update checks** – the updater loads the app's existing CA
  bundle in addition to host trust, allowing verified HTTPS requests without
  a separate Python certificate installation.

## [1.4.0] - 2026-10-13

### Added

- **In-app updates for macOS and Windows** – packaged apps check GitHub releases
  in the background, download the matching installer, verify its SHA-256, and
  show release notes before a confirmed restart. Updates wait for dictation to
  finish. Mac updates restore the previous bundle if the replacement move fails;
  Windows Local Whisper installations keep their installer variant.
- **Tag-triggered releases** – annotated version tags build both standard
  installers and publish the release only after its assets are uploaded.

### Changed

- **Less repeated UI work** – settings, prompt editing, history, and status
  displays reuse feedback and avoid redundant refresh work.
- **Refreshed app icons** – packaged apps use icons generated from the new
  PulseScribe SVG artwork.


## [1.3.0] - 2026-08-04

### Added

- **Windows: adaptive stop tail** – when the audio tail was already silent for
  ~200 ms at hotkey release (the speaker finished talking – the common case),
  the stop grace shrinks to ~50 ms instead of the configured 0.20–0.30 s, so
  text appears noticeably faster. Releasing mid-word keeps the full
  conservative tail, so final words are never clipped. Applies to streaming
  and REST capture; disable via `PULSESCRIBE_WINDOWS_ADAPTIVE_STOP_TAIL=false`.

### Fixed

- **Audio and streaming lifecycle leaks** – Deepgram stop watchers, warm-stream
  forwarders, sender/listener tasks, and native microphone streams are now
  terminated or closed on initialization, connection, listener, stop, and
  cancellation failures. PortAudio close deadlocks remain tracked and block
  unsafe retry accumulation until the app is restarted.
- **Abandoned worker accumulation on macOS** – watchdog-abandoned recording
  workers can no longer be replaced while still alive, preventing retained
  audio buffers, provider calls, and native resources from growing per retry.
- **CUDA and local preload worker accumulation** – timed-out CUDA loads are
  deduplicated in process-exit-safe daemon workers, while Windows startup and
  settings reloads share one coalescing local-model preload owner.
- **Deepgram SDK pinned to 5.x** – `deepgram-sdk` 7.x removed the 5.x module
  paths the streaming provider relies on (`deepgram.extensions.types.sockets`
  for `ListenV1ControlMessage`), silently breaking KeepAlive/Finalize/
  CloseStream control messages. Requirements now pin `deepgram-sdk>=5,<6`.

### Changed

- **Windows: `snappy` latency preset is now the default** – the adaptive stop
  tail protects final words independently of the preset (releasing mid-word
  keeps the full conservative tail), so the shorter capture/finalize buffers
  no longer carry a clipping risk. `safe` remains available as an explicit
  opt-out via `PULSESCRIBE_WINDOWS_LATENCY_PRESET=safe`; invalid values still
  fall back to `safe`. The onboarding toggle now reflects the new default
  (checked = snappy/default, unchecked = safe).
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
