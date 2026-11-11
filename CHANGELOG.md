# Changelog

All notable changes to PulseScribe are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

- **Local Whisper about twice as fast on macOS 26.2 and newer (MLX)** – the
  app shipped MLX's build for macOS 15, which runs Whisper about 2.3 times
  slower on current macOS than the build made for it. The app now carries
  both and loads the one for your system. Measured with turbo on an M5: a
  pass over 2 s of audio takes 0.34 s instead of 0.77 s. Transcripts are
  unchanged; on macOS 15 to 26.1 nothing changes.

## [1.20.2] - 2026-11-11

### Changed

- **Shorter paste confirmation (macOS)** – the "Pasted" overlay now stays
  0.5 s instead of 0.8 s before it fades, so it clears the freshly pasted text
  sooner. "Copied" and "No speech" stay as long as before.

## [1.20.1] - 2026-11-11

### Changed

- **Transcript ready after a pause, also with the streaming preview** – while
  the streaming model shows the text, Whisper now transcribes at speech
  pauses in the background (MLX with a fast model such as turbo). When you
  stop about a second after your last word, the transcript is there almost at
  once instead of after a full Whisper pass.

### Fixed

- **Quiet dictation no longer discarded (macOS, streaming preview)** – a
  recording below the speech level used to end as "No speech". With the
  streaming model selected, PulseScribe now transcribes it if that model
  recognized words. Noise, clicks and silence are discarded as before, and so
  is speech too short for the model to recognize (about two seconds or less).

## [1.20.0] - 2026-11-10

### Added

- **Word-by-word live text with a streaming model (macOS)** – Settings →
  Providers → Advanced backend options → Live preview has a new choice,
  "streaming model". A second model (NVIDIA Nemotron 3.5 ASR streaming, on
  the CPU) then shows each word about 0.6 s after you say it, with every
  local model and backend, including large-v3. Whisper still writes the
  transcript. It costs a one-time 0.7 GB download and about 1.9 GB of memory
  while loaded; `PULSESCRIBE_LOCAL_PREVIEW=streaming` selects it too.

### Changed

- **Live preview option for every local backend** – the option no longer
  hides for large models or other backends; a hint says when "on" cannot
  show text and what the streaming model costs.

## [1.19.0] - 2026-11-10

### Changed

- **Live text you can read (macOS)** – recordings with live text (local
  preview, Deepgram streaming) use a larger overlay with two lines of text
  instead of one short line that jumped with every update. Words keep their
  place while the text grows and a line scrolls out as a whole. Words that
  were already shown are bright; new words are dimmed until the next update
  confirms them.

## [1.18.1] - 2026-11-10

### Fixed

- **Live text with quiet microphones** – the live preview showed no text when
  speech stayed close to the detection level, for example with a MacBook
  microphone at low input volume. It now follows the level of each recording.
  The log has one `Live-Vorschau:` line per recording that says what the
  preview did.
- **No stale live text** – when preview passes get too slow (two in a row
  over 1 s), the preview ends for that recording and its text disappears
  instead of standing still.

### Changed

- **Live preview only with fast models** – `large`, `large-v2` and `large-v3`
  are too slow for text while you speak, so PulseScribe no longer tries: with
  these models there is no preview pass that could delay the transcript.
  Settings hide the Live preview option for them and say why; turbo shows
  live text.

## [1.18.0] - 2026-11-10

### Added

- **Live text for local dictation on Apple Silicon** – with the MLX backend the
  overlay shows the recognized text while you speak, updated about every
  0.7 s. The final transcript is unchanged. After a pause of about one second
  before stopping, the transcript of a recording up to 20 s is ready at once
  instead of after 0.4 s (M6, turbo). Turn the preview off in Settings →
  Providers → Advanced backend options → Live preview or with
  `PULSESCRIBE_LOCAL_PREVIEW=false`.

## [1.17.1] - 2026-11-04

### Changed

- **More sensitive voice detection** – speech about a third quieter than before
  is recognized instead of treated as silence on macOS and Windows.
- **Livelier overlay wave** – the overlay reacts more strongly to quiet speech.
  The maximum wave height is unchanged.

## [1.17.0] - 2026-11-04

### Added

- **Release history in Settings → About** – What's new shows the five newest
  releases bundled with the app on macOS and Windows. Read and copy the notes
  offline; installation instructions are omitted.

## [1.16.1] - 2026-11-04

### Changed

- **Taller recording wave** – while you speak, the overlay wave now reaches up
  to 30 px instead of 24 px on macOS and Windows. The listening row before a
  recording is unchanged.

### Fixed

- **macOS sounds no longer play twice** – the first start, stop, done or error
  sound after launch played once through CoreAudio and once more through
  `afplay`, and later ones only through the slower `afplay`. PulseScribe read a
  status from `AudioServicesPlaySystemSound`, which returns nothing.

## [1.16.0] - 2026-11-04

### Changed

- **Advanced backend options show only what works** – on macOS and Windows,
  each option appears only for the backends that use it (for example no Beam
  size or Device for MLX, no FP16 for faster-whisper), without gaps between
  rows. Every option has a tooltip with what it does and its default. Windows
  gains the Fast option; the macOS Device list drops CUDA.
- **Advanced values are checked on save** – out-of-range or malformed numbers
  (for example Beam size 50, or Temperature `0,5` with a German decimal comma,
  which was read as the list 0 and 5) block saving with a message instead of
  being dropped or misread. Windows now accepts Temperature fallback lists.

### Added

- **Reset to defaults** for the advanced backend options on macOS and Windows.
- **Unload when idle** (macOS) – optionally frees the local model after 5, 15,
  30 or 60 minutes without dictation (`PULSESCRIBE_LOCAL_IDLE_UNLOAD_MINUTES`).
  The next recording starts loading the model right away, so it loads while you
  speak (turbo 8-bit on an M-series Mac: about 0.1 s from cache). Default: never.

## [1.15.0] - 2026-11-04

### Added

- **Download progress for local models** – the first load of an MLX or
  faster-whisper model shows its download progress in the menu bar (macOS) or
  tray (Windows), e.g. "43 % · downloading turbo (1.6 GB)". Before, PulseScribe
  only showed "Loading…" for the minute a 1.5 GB download can take.

### Changed

- **Smaller macOS app** – the app no longer bundles PyTorch and openai-whisper,
  which no included backend needs. MLX and Lightning also skip the torch device
  probe at startup and report their device as Metal.
- **MLX models use 8-bit weights by default** – in our German test as
  accurate as full precision, about 10 % faster and roughly half the memory
  (turbo 0.8 GB instead of 1.5 GB). `large` downloads a pre-quantized 1.6 GB
  model instead of 2.9 GB. Choose 4bit or none under Settings → Providers →
  Quantization (`PULSESCRIBE_MLX_QUANT`).

### Fixed

- **Releasing local memory on macOS** – freeing the local model now also
  releases the MLX model, which stayed loaded before.
- **Local backend "auto" on Apple Silicon** – now uses MLX. Before, it picked
  Lightning, which has no turbo model and silently loaded the larger, slower
  large-v3 instead.
- **Backends missing from the app** – Settings only offer local backends the
  app contains, and a configured backend that is not bundled falls back to the
  default backend instead of failing. The two "macOS: MPS" presets, which
  needed openai-whisper, are removed; MLX is the better choice on a Mac.

## [1.14.1] - 2026-11-04

### Fixed

- **macOS Dock icon** – removed the white corners around the blue icon by
  preserving the SVG artwork's transparency in every packaged icon size.

## [1.14.0] - 2026-11-03

### Added

- **Server model picker** – in the own server mode, the Server Model field on
  macOS and Windows lists the models your server reports (`GET /models`), with
  a Refresh button. Typing a name still works, also when the server offers no
  list.

## [1.13.0] - 2026-11-03

### Added

- **Server model in Settings** – in the own server mode, Settings → Providers
  on macOS and Windows has a Server Model field (saved as
  `OPENAI_COMPATIBLE_MODEL`), for example `large-v3` for the slower, more
  accurate model. Empty sends `whisper-1` as before.

### Fixed

- **Microphone changes on macOS** – plugging in or removing a microphone, for
  example when docking, no longer needs a restart. PulseScribe re-reads the
  audio devices before the next recording and when Settings lists the
  microphones. Before, recordings failed with "Internal PortAudio error" and
  new microphones did not appear until PulseScribe restarted.
- **Microphone selection on macOS** – the microphone chosen in Settings is now
  used in all modes; before, it applied only to Deepgram streaming, and Local,
  OpenAI, Groq and own server recordings used the system default.

## [1.12.0] - 2026-11-03

### Added

- **Own transcription server** – the new mode `openai_compatible` sends
  recordings to your own server with OpenAI's transcription API, for example a
  Whisper server on another computer in your network. Set
  `OPENAI_COMPATIBLE_BASE_URL` (and `OPENAI_COMPATIBLE_API_KEY` if the server
  checks one); your OpenAI key is never sent there.
- **Own server in Settings** – on macOS and Windows, Settings → Providers
  offers the mode `openai_compatible` with a Server URL field, and the Windows
  setup status asks for the URL instead of an API key.

## [1.11.1] - 2026-11-03

### Changed

- **Windows CI** – the Windows test job installs PySide6 and runs the Qt tests
  for Settings, the setup wizard, the overlay, the microphone picker and the
  update window offscreen. Before, they always skipped.

### Fixed

- **Live text in the Windows overlay** – the overlay missed an interim text
  update (or the clear at the end) when the text file was rewritten within one
  Windows timestamp tick. It now also compares the file size.
- **Custom hotkey row in the Windows wizard** – the recording field no longer
  cuts off "Press keys" with wider system fonts, and the description wraps
  instead of being squeezed by the field.
- **Hotkey validation messages in English** – the "Invalid hotkey" alert on
  macOS and the messages for Toggle/Hold conflicts, blocked combinations, Input
  Monitoring and unknown keys were still German. Windows hotkey format errors
  are English too.
- **Startup alerts and vocabulary messages in English** – the macOS alerts for
  a missing or invalid hotkey and for vocabulary problems, the hotkey overlap
  message and the vocabulary file messages were still German.

## [1.11.0] - 2026-11-03

### Changed

- **Setup wizard in the Settings style (macOS)** – the wizard uses the flat
  dark look of Settings: a solid 640 × 520 window, a sidebar that lists the
  five steps with a check for finished steps (click one to go back), a page
  title with one short subtitle, and a footer with "Open Settings…", Back
  and a light Next/Finish button. Setup shows Fast, Private and Advanced as
  selectable rows; Next applies the choice, and Fast asks for a Deepgram key
  right away when none is stored. The "Step n/5" label is gone.
- **Setup wizard Permissions and Review (macOS)** – Permissions lists
  Microphone, Accessibility and Input Monitoring as rows with their purpose,
  a Granted / Missing / Not set badge and "Allow…" only where access is
  missing. Review shows Provider, Hotkey, Permissions and the practice test
  with tinted badges instead of colored text and ends with how to dictate
  with your hotkey, for example "Hold Fn in any app to dictate."
- **Setup wizard Hotkey: one choice (macOS)** – the Hotkey step offers Hold
  Fn, Toggle Option+Space, Toggle F19 and Custom (Record) as rows. Picking a
  row saves that hotkey and clears the other slot, so the wizard sets up one
  hotkey. If you already use a Toggle and a Hold hotkey, nothing is
  preselected and both stay until you pick a row; Settings → General keeps
  managing two hotkeys. Permission and Record buttons use the quiet outline
  style.
- **Setup wizard Practice (macOS)** – "Try it" shows one prompt with your
  hotkey as a keycap ("Hold Fn and say a short sentence."), a Start practice
  / Stop / Try again button and a transcript box with a Listening,
  Processing, Worked, No speech or Error badge. The repeated hint lines and
  the in-step skip link are gone; Skip sits in the footer. The test stays
  safe: nothing is pasted, and leaving the step cancels a running test.
- **Setup wizard in the Settings style (Windows)** – the Windows wizard gets
  the same layout as on macOS: a 640 × 520 window with the step sidebar, page
  title and footer, Setup rows, a Microphone row, one-choice Hotkey rows
  (Hold Ctrl+Win, Toggle Ctrl+Alt+R, Toggle F19, Custom), a Practice step
  with a transcript badge and Review rows with badges. The wizard is now in
  English like Settings. A first run preselects Hold Ctrl+Win instead of
  setting two hotkeys.

### Fixed

- **"Open Settings…" in the Windows wizard** – the button finished the
  wizard but never opened Settings; the wizard now asks the running app to
  open them.
- **Back on the first wizard step (Windows)** – Back no longer shows on the
  first step when the wizard opens there.
- **Setup wizard review after Private (macOS)** – Review showed the previous
  provider after choosing Private; it now shows Local.

## [1.10.2] - 2026-11-02

### Changed

- **Microphone level while listening** – before you start speaking, the
  grey listening bars follow the microphone level with a small amplitude,
  so you can see that PulseScribe hears you. Steady room noise keeps them
  still, and they grow smoothly into the white recording bars once speech
  is detected. As an informational level meter it stays on with "Reduce
  motion". The Windows Tkinter fallback keeps the static bars.
- **Readable app names (Windows)** – the result line names the target app
  as "into Microsoft Word" or "into Google Chrome" instead of the process
  name (`WINWORD`, `chrome`).

### Fixed

- **Settings polish** – the Lightning preset and the Deepgram mode help no
  longer show an emoji. On macOS, the Refine model help no longer touches
  the model field, and the Permissions section has no empty space at its
  bottom.

## [1.10.1] - 2026-11-02

### Changed

- **Processing feedback** – a soft light sweeps across the line while
  preparing, transcribing and refining; with Refine on it runs over the
  active step. It stops as soon as the result appears. With "Reduce motion"
  (macOS) or animation effects turned off (Windows) the line stays static.
  The Windows Tkinter fallback keeps the static line.

## [1.10.0] - 2026-11-02

### Changed

- **Steadier overlay** – the recording overlay keeps one width (400 px)
  from listening to the result, so its text no longer jumps between states.
  Status (bars or badge), text and a right-aligned detail each have a fixed
  place; only error messages that stay on screen grow wider.
- **Live text fades out** – while you speak, the newest words stay visible
  and older words fade out on the left instead of widening the overlay
  (Windows Tkinter fallback: shortened from the left, without fade).
- **Recording time** – the overlay shows how long you have been recording
  (`m:ss`), updated once per second.
- **Two steps when Refine is on** – while Refine follows transcription, the
  overlay shows a two-part line with "1 of 2" (transcribing) and "2 of 2"
  (refining). Without Refine it keeps the single line.
- **Clearer result** – after a paste the overlay names the target app
  ("into Mail"; on Windows the program name). With "Show transcription time"
  turned on (formerly transcription speed), it shows how long transcription
  took, e.g. "1.2 s", instead of the real-time factor. Windows now shows it too.
- **Helpful errors** – when a paste fails, the overlay says "Copied — press ⌘V"
  (Windows: "Copied — press Ctrl+V"). "Missing API key" and "Microphone
  unavailable" show a "Settings" link that opens Settings on Transcription
  (Windows: if the settings window is already open, it stays on its section).

### Fixed

- **macOS result badge** – the overlay said "Pasted" even when the transcript
  was only copied to the clipboard; it now says "Copied" in that case.

## [1.9.0] - 2026-11-02

### Changed

- **Settings redesign** – the settings window is easier to scan on macOS and
  Windows. Nine tabs become six sections in a left sidebar (General,
  Transcription, Refine, Vocabulary, History, About) in a wider, shorter window.
  Behavior options (overlay, clipboard restore, Dock icon, speed display, show at
  startup) live under General, local presets next to the local model, prompts
  next to Refine. History opens on Transcripts.
- **Only relevant settings** – Transcription shows the API key of the selected
  provider, streaming only for Deepgram, and local model options only for Local,
  with advanced backend options collapsed. Refine shows the key of its own
  provider and greys out its options while Refine is off.
- **Flat dark style** – sections sit directly on a dark background with thin
  dividers; permissions and API keys show small status badges. The save bar
  appears only with unsaved changes, and a short toast confirms saving. Windows
  can discard unsaved changes and stays dark regardless of the system theme.
- **Recording overlay redesign** – a compact, solid 44 px overlay in monochrome
  on macOS and Windows. Only recording animates (bars follow the microphone);
  listening shows static low bars and preparing/transcribing/refining a flat
  line, so idle states no longer redraw. Outcomes use small colored badges:
  "Pasted" (with the speed value when enabled), "No speech", "Not pasted" for
  paste/copy failures and "Error". The overlay grows with long live text.
- **Errors stay readable** – errors and paste/copy failures remain on screen
  with a "×" until you click the overlay or start the next recording, instead
  of disappearing after 4 seconds. Hotkeys work immediately as before. The
  overlay only accepts clicks while such a message is shown (Windows: the
  overlay is now click-through otherwise; the Tkinter fallback stays clickable).

## [1.8.0] - 2026-10-30

### Added

- **Microphone selection** – choose an input device in Settings → Providers on
  macOS and Windows. Save & Apply uses it from the next recording, preserves
  device names across restarts, and falls back automatically if it is unavailable.
  Refresh detects newly connected devices without opening an audio stream.

### Changed

- **macOS CI** – build and test on the supported, pinned macOS 15 runner instead
  of the retiring macOS 14 image or the moving latest image. Local-provider
  cleanup tests use fake GPU allocators rather than touching hosted hardware.

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
