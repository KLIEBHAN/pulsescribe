# ADR-003: Rust-Migration – Zielarchitektur & Phase-0-Messergebnisse

**Status:** Accepted (Phase 0 abgeschlossen)  
**Datum:** 2026-11-21  
**Kontext:** Komplette Portierung von PulseScribe (56.360 LOC Python + 44.576 LOC Tests) auf Rust für macOS und Windows (Linux architektonisch offenhalten).

## Leitentscheidungen

1. **Lokale ML-Backends (1a):** Primär native C/C++-Engines direkt aus Rust (`whisper-rs` / `whisper.cpp` + `sherpa-onnx` C-API). Ein optionales Apple-Silicon-MLX-Backend (`mlx-rs` / `mlx-c`, ohne Python) wird erst in Phase 5b erwogen, falls die gemessene Latenzlücke auf Metal 4 bis dahin bestehen bleibt.
2. **UI-Architektur (2a):** Hybrid-Split — gemeinsames Lazy-WebView-Frontend (**Tauri v2**, beim Schließen komplett zerstört → 0 MB WebView-RAM im Idle) für Settings, Onboarding-Wizard, Historie, Prompts, Vokabular und Update-Fenster; **natives** System-Tray (`tray-icon` + `muda`) und **natives** 60-FPS-Overlay (`objc2-app-kit`/Core Animation auf macOS, `windows-rs`/Direct2D auf Windows) für Zero-Focus-Steal und 0 FPS Idle-GPU.
3. **Plattformstrategie (3a):** macOS (`aarch64-apple-darwin`) und Windows (`x86_64-pc-windows-msvc`) ab Phase 0/1 parallel im selben Cargo-Workspace und in der CI-Matrix; Linux-Traits architektonisch sauber abtrennen.
4. **Migrationsmodus:** Vertikale Scheiben im selben Repository (kein Big-Bang, kein PyO3-Zwischenschritt). Alle Nutzerdaten unter `~/.pulsescribe/` (`.env`, `preferences.json`, `vocabulary.json`, `prompts.toml`, `history.jsonl`) sowie `~/.cache/huggingface/` bleiben 1:1 kompatibel.

---

## Phase-0-Messergebnisse (2026-11-21)

**Testumgebung:** Apple M6 (`arm64`), macOS 27.0.1, `rustc 1.99.1`, `cargo 1.99.1`, `cargo-xwin`.  
**Testclips:** Identische 16-kHz-Mono-WAV-Clips (Deutsch, `say -v Anna`):
- `clip_3s.wav` (4,90 s): *„Hallo Fabian, wir testen heute die lokale Spracherkennung direkt in Rust.“*
- `clip_11s.wav` (13,91 s): *„Am nächsten Dienstag um zehn Uhr treffen wir uns im Büro, um die neue Architektur von PulseScribe durchzugehen. Besonders wichtig sind uns niedrige Latenz, sauberer Offline-Betrieb und geringe Speichernutzung im Leerlauf.“*

### 1. Lokale ML-Benchmarks (Median aus 5 Läufen, nach Warmup)

| Engine / Modell | Laufzeit | Load | 4,90 s Clip | 13,91 s Clip | Peak RSS | Transkript-Parität |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| **Python `mlx-whisper` turbo (8-bit)** *(Referenz)* | MLX Metal 4 | 570 ms | **326 ms** | **431 ms** | 1.801 MB | Referenz (100 %) |
| **Rust `whisper-rs 0.16` turbo `q8_0`** | Metal (`whisper.cpp`) | 338 ms* | 695 ms | 876 ms | **1.041 MB** | Identisch |
| **Rust `whisper-rs 0.16` turbo `q5_0`** | Metal (`whisper.cpp`) | 390 ms* | 739 ms | 862 ms | **730 MB** | Identisch |
| **Rust `sherpa-onnx 2.0.2` Parakeet v3 int8** | CPU (4 Threads) | 827 ms | **238 ms** | 564 ms (min 431) | 1.427 MB | Sehr gut (2 kleine Wortfehler) |
| **Rust `sherpa-onnx 2.0.2` Parakeet v3 int8** | CoreML EP *(verworfen)* | 5.190 ms | 579 ms | 646 ms | 4.489 MB | Identisch + CoreAnalytics-Leak |
| **Rust `sherpa-onnx 2.0.2` Nemotron 3.5 Streaming** | CPU (2 Threads, 560 ms) | 475 ms | **54,2 ms/Chunk** | **54,0 ms/Chunk** (max 61,8) | 1.169 MB | Streaming-Preview (<10 % Echtzeit) |

*\* Erster Kaltstart nach Build kompiliert einmalig die eingebettete Metal-Bibliothek (~6,2 s), danach 338–390 ms aus dem System-Cache.*

#### Empirische Erkenntnisse & vermiedene Fallen

1. **`whisper.cpp` Metal-4-Lücke erklärt:** `whisper-rs 0.16.0` meldet auf M6 / macOS 27 `ggml_metal_device_init: has tensor = false` (Shader-Probe für Metal-4-Tensor-API scheitert), wodurch Whisper ~2× langsamer läuft als `mlx-whisper` (695 ms vs. 326 ms), aber **42–59 % weniger RAM** belegt (730–1.041 MB statt 1.801 MB) und nur **2,3 MB** Binary-Größe mitbringt.
2. **Kein verkürztes `audio_ctx` bei Whisper `large-v3-turbo`:** Dynamisches `set_audio_ctx` führt bei Turbo zu Halluzinationsschleifen und ~3× längerer Laufzeit (2.623 ms statt 862 ms), da das Modell auf feste 30-s-Positions-Embeddings (`1500`) trainiert ist.
3. **Kein CoreML-EP für `sherpa-onnx` Parakeet:** CoreML ist 2,4× langsamer (579 ms vs. 238 ms), braucht 3,1× mehr RAM (4.489 MB vs. 1.427 MB), lädt 5,2 s und loggt `Context leak detected`. `sherpa-onnx` bleibt fest auf `provider="cpu"`.
4. **Parakeet + Nemotron direkt via C-API sehr attraktiv:** `libsherpa-onnx-c-api.dylib` (4,2 MB) + `libonnxruntime.dylib` (28,8 MB) = **33 MB** statt ~1 GB PyInstaller-Bundle. Parakeet v3 auf CPU schlägt bei 4,9 s sogar MLX-Whisper (238 ms vs. 326 ms); Nemotron-Streaming läuft mit 54 ms/Chunk plattformübergreifend auch auf Windows.
5. **Konsequenz für Phase 5:** `crates/local-asr` abstrahiert über ein `LocalAsrEngine`-Trait. Phase 5a liefert `whisper-rs` + `sherpa-onnx` (CPU). Falls `whisper.cpp` bis Phase 5 keinen Metal-4-Tensor-Support hat oder 80-ms-GPU-Parakeet auf Apple Silicon benötigt wird, kann Phase 5b ein optionales `mlx-rs`-Backend (`--features apple-mlx`, ohne Python) hinter demselben Trait ergänzen.

### 2. macOS Native OS & Overlay Spike (live auf macOS 27.0.1)

| Prüfung (`objc2` 0.6 / `objc2-app-kit` 0.3 / Carbon / Quartz) | Ergebnis |
| --- | --- |
| Release-Binary (`spike-macos-os`) | **499 KB** |
| `NSApplication` Init (`Accessory`-Policy, kein Dock-Icon) | **63,6 ms** |
| Permissions (`AXIsProcessTrusted`, `CGPreflightListenEventAccess`, `AVCaptureDevice`) | Alle 3 dialogfrei abfragbar |
| App-Detection (`NSWorkspace::frontmostApplication`) | **0,1 µs** Durchschnitt (50 Läufe) |
| Carbon `RegisterEventHotKey` (`F19`, ohne Accessibility) | `OSStatus = 0` (Register + Unregister) |
| `CGEventTapCreate` (`kCGEventFlagsChanged` für `Fn`-Hold) | Erfolgreich erstellt (`true`) |
| Non-Activating `NSPanel` Level 25 + `CALayer`/Core-Animation-Shimmer | `12,8 ms` Erzeugung, `is_key_window=false`, `focus_preserved=true` (`Finder` → `Finder`), Click-Through aktiv |

### 3. Windows Cross-Compile Spike (`x86_64-pc-windows-msvc` via `cargo-xwin`)

`cargo xwin check --target x86_64-pc-windows-msvc` lief in **16,5 s fehler- und warnungsfrei** durch für:
- `windows 0.62` (`WH_KEYBOARD_LL`, `SendInput`, `OpenClipboard`/`GlobalAlloc`, `GetForegroundWindow`, `GetFileVersionInfoW`, `WS_EX_NOACTIVATE | WS_EX_TOPMOST | WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW`, Direct2D)
- `cpal 0.18` (WASAPI)
- `tray-icon 0.26` + `muda 0.21`

Damit können Windows-OS-Adapter bereits auf macOS-Entwicklungsrechnern vor jedem Push typgeprüft werden; die Laufzeitvalidierung erfolgt auf Windows (lokal/CI).

---

## Ziel-Architektur (Cargo-Workspace)

```text
Cargo.toml                  # Workspace Root (Edition 2021 / MSRV 1.85)
crates/
├── core/                   # Pure Domain: Config/.env, Preferences, History, Vocab,
│                           # Prompts, Presets, Hotkey-Parser, AppState, Overlay-ViewModels
├── audio/                  # cpal + lock-free ringbuf, Resampling (rubato), symphonia,
│                           # CoreAudio Device-Refresh, WASAPI Warm-Stream
├── providers/              # reqwest (OpenAI, Groq, Deepgram REST, OpenAI-Compatible),
│                           # tokio-tungstenite Deepgram WS (ADR-001), refine (LLM + Context)
├── local-asr/              # LocalAsrEngine Trait: whisper-rs + sherpa-onnx C-API (optional mlx-rs)
├── platform/               # OS-Traits (Hotkey, Clipboard/Paste, AppDetector, Sound, Permissions)
├── daemon/                 # 1 gemeinsame State-Machine (ersetzt macOS- & Windows-Daemon)
└── cli/                    # clap CLI (1:1 Ersatz für transcribe.py)
apps/
└── desktop/                # Tauri v2 (Lazy Settings/Wizard/Update) + Native Tray & 60-FPS-Overlay
```

**Build-Varianten über Cargo-Features:**
- **Slim (Cloud-only):** `--no-default-features` (~15–25 MB)
- **Full (mit Offline-ASR):** `--features local-whisper,local-sherpa` (~20 MB + ~33 MB `sherpa-onnx` dylibs)

---

## Schnitt für Phase 1 (kleine, unabhängige PRs)

Jeder PR bleibt unter ~400 LOC, kompiliert auf macOS + Windows-Check und bringt eigene Tests mit:

1. **PR 1.1 – Workspace, CI-Grundgerüst & Golden-Fixtures:** `Cargo.toml` Workspace + `crates/core` (Lifecycle-States & Error-Codes), GitHub-Actions-Matrix (`macos-15` + `windows-latest`: `cargo fmt --check`, `clippy`, `cargo test`; lokal optional `cargo xwin check`), Python-Exportskript für sprachneutrale JSON-Fixtures.
2. **PR 1.2 – `.env`, Pfade & atomares I/O:** Port von `utils/paths.py`, `utils/atomic_io.py`, `utils/env.py`, `utils/preferences.py` mit identischer Präzedenz (Process Env > `~/.pulsescribe/.env` > Projekt-`.env`) und Golden-Tests.
3. **PR 1.3 – Nutzerdaten-Verträge (`vocabulary.json`, `prompts.toml`, `history.jsonl`):** 1:1 kompatibles Lesen/Schreiben inkl. History-Rotation (10 MB) und File-Signature-Caching.
4. **PR 1.4 – Hotkey-Parsing, Validierung & Diktier-State:** Port von `utils/hotkey_validation.py`, `utils/hotkey_windows.py`, `utils/hold_state.py`, `utils/state.py`, `utils/transcript_delivery.py` gegen Golden-Fixtures.
5. **PR 1.5 – Plattformneutrale Overlay- & UI-ViewModels:** Port von `ui/animation.py`, `ui/overlay_feedback.py`, `ui/overlay_style.py`, `ui/overlay_text.py`, `ui/daemon_status_feedback.py`.
