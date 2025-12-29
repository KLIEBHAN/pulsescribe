# Claude Code Projektanweisungen

## Projekt-Übersicht

**PulseScribe** – Minimalistische Spracheingabe für macOS und Windows, inspiriert von [Wispr Flow](https://wisprflow.ai).

Siehe [docs/VISION.md](docs/VISION.md) für Roadmap und langfristige Ziele.
Siehe [docs/SECURITY.md](docs/SECURITY.md) und [docs/NETWORK.md](docs/NETWORK.md) für Sicherheit und Netzwerk.

## Architektur

### Projektstruktur (Übersicht)

```
pulsescribe/
├── transcribe.py            # CLI Entry-Point
├── pulsescribe_daemon.py    # macOS Daemon (NSApplication)
├── pulsescribe_windows.py   # Windows Daemon (pystray + pynput)
├── config.py                # Zentrale Konfiguration
│
├── audio/                   # Audio-Aufnahme (AudioRecorder)
├── cli/                     # CLI-Typen (Enums für Mode, Context, Provider)
├── providers/               # Transkription (Deepgram, OpenAI, Groq, Local)
├── refine/                  # LLM-Nachbearbeitung (Prompts, Context, LLM-Calls)
├── ui/                      # UI-Komponenten (Overlay, Menubar, Settings, Onboarding)
├── utils/                   # Utilities (Logging, Permissions, Preferences)
├── whisper_platform/        # Plattform-Abstraktion (Clipboard, Sound, Hotkey)
│
├── assets/                  # Icons (icon.icns, icon.ico)
├── macos/                   # macOS-spezifisch (entitlements.plist)
├── docs/                    # Dokumentation
└── tests/                   # Unit & Integration Tests
```

> **Tipp:** Für die vollständige Dateiliste:
>
> ```bash
> tree -L 2 --dirsfirst -I '__pycache__|*.pyc|venv|dist|build|*.egg-info'
> ```

### Modul-Verantwortlichkeiten

| Modul               | Verantwortlichkeit                                                     |
| ------------------- | ---------------------------------------------------------------------- |
| `audio/`            | Mikrofon-Aufnahme via sounddevice, WAV-Export                          |
| `cli/`              | Shared Enums (TranscriptionMode, Context, RefineProvider, HotkeyMode)  |
| `providers/`        | Transkriptions-APIs (Deepgram REST+WS, OpenAI, Groq, lokales Whisper)  |
| `refine/`           | LLM-Nachbearbeitung, Kontext-Detection, Prompt-Templates               |
| `ui/`               | Overlay-Animation, Menubar (macOS), Settings-GUI (Windows), Onboarding |
| `utils/`            | Shared Utilities: Logging, Paths, Preferences, Hotkey-Parsing          |
| `whisper_platform/` | OS-Abstraktion: Clipboard, Sound, Hotkeys, App-Detection               |

### Build-Dateien

| Datei                                      | Zweck                        |
| ------------------------------------------ | ---------------------------- |
| `build_app.sh` / `build_app.spec`          | macOS App Bundle             |
| `build_dmg.sh`                             | macOS DMG-Erstellung         |
| `build_windows.ps1` / `build_windows.spec` | Windows EXE + Installer      |
| `installer_windows.iss`                    | Inno Setup Script            |
| `pyproject.toml`                           | Python-Projekt-Konfiguration |

## Kern-Datei: `transcribe.py`

**Funktionen:**

| Funktion       | Zweck                                |
| -------------- | ------------------------------------ |
| `transcribe()` | Zentrale API – orchestriert Provider |
| `parse_args()` | CLI-Argument-Handling                |

**Design-Entscheidungen:**

- **Modular:** Nutzt `providers.*`, `audio.*`, `refine.*`, `utils.*`
- **Lean:** Orchestrator statt Monolith (~1000 LOC weniger)
- **Kompatibel:** Alle bestehenden CLI-Flags funktionieren weiter
- **Entry-Point:** Bleibt die zentrale Anlaufstelle für Skripte
- **Lazy Imports:** `openai`, `whisper`, `sounddevice` werden erst bei Bedarf importiert

## Daemons

### macOS: `pulsescribe_daemon.py`

Konsolidiert alle Komponenten in einem Prozess (empfohlen für tägliche Nutzung):

| Klasse              | Modul                | Zweck                                           |
| ------------------- | -------------------- | ----------------------------------------------- |
| `MenuBarController` | `ui.menubar`         | Menübar-Status via NSStatusBar (🎤 🔴 ⏳ ✅ ❌) |
| `OverlayController` | `ui.overlay`         | Animiertes Overlay am unteren Bildschirmrand    |
| `SoundWaveView`     | `ui.overlay`         | Animierte Schallwellen-Visualisierung           |
| `PulseScribeDaemon` | `pulsescribe_daemon` | Hauptklasse: Orchestriert Hotkey, Audio & UI    |

**Architektur:** Main-Thread (Hotkey + UI Event Loop) + Worker-Thread (Deepgram-Streaming)
**Streaming:** Deepgram-Stop leert die Audio-Queue bis zum None-Sentinel, um abgeschnittene letzte Wörter zu vermeiden (`providers/deepgram_stream.py`).

### Windows: `pulsescribe_windows.py`

Separater Entry-Point mit Windows-nativen Komponenten:

| Klasse                     | Modul                 | Zweck                                          |
| -------------------------- | --------------------- | ---------------------------------------------- |
| `PySide6OverlayController` | `ui.overlay_pyside6`  | GPU-beschleunigtes Overlay (Fallback: Tkinter) |
| `pystray.Icon`             | extern                | System-Tray-Icon mit Farbstatus                |
| `pynput.keyboard.Listener` | extern                | Globale Hotkeys (F1-F24, Ctrl+Alt+X, etc.)     |
| `PulseScribeWindows`       | `pulsescribe_windows` | Hauptklasse: State-Machine + Orchestrierung    |

**Features:**

- Pre-Warming (SDK-Imports, DNS-Prefetch, PortAudio) für schnellen Start
- LOADING-State für akkurates UI-Feedback während Mikrofon-Init
- Native Clipboard via ctypes (kein Tkinter/pyperclip)
- Windows System-Sounds (DeviceConnect, Notification.SMS, etc.)

### Animation-Architektur: `ui/animation.py`

Zentrale Animationslogik für konsistentes Verhalten auf allen Plattformen:

```
ui/animation.py (Single Source of Truth)
├── AnimationLogic (Klasse)
│   ├── update_level() + update_agc()     ← Audio-Level + AGC
│   └── calculate_bar_normalized(i, t, state) → 0.0-1.0
│
├── overlay_windows.py  ← nutzt AnimationLogic für alle States
├── overlay_pyside6.py  ← nutzt AnimationLogic für alle States
└── overlay.py (macOS)  ← nutzt AnimationLogic für LISTENING, TRANSCRIBING,
                          REFINING, DONE; eigene Logik für RECORDING
                          (komplexere Envelope/Wander-Animation)
```

**Normalized API:** `calculate_bar_normalized()` gibt Werte 0-1 zurück, damit jede Plattform eigene MIN/MAX-Höhen anwenden kann.

## CLI-Interface

```bash
# Datei transkribieren
python transcribe.py audio.mp3
python transcribe.py audio.mp3 --mode local --model large

# Mikrofon-Aufnahme
python transcribe.py --record --copy --language de
```

## Dependencies

### Core (Cross-Platform)

| Paket            | Zweck                                     |
| ---------------- | ----------------------------------------- |
| `openai`         | API-Modus + LLM-Refine (OpenRouter)       |
| `openai-whisper` | Lokaler Modus                             |
| `deepgram-sdk`   | Deepgram Nova-3 Transkription (REST + WS) |
| `groq`           | Groq Whisper + LLM-Refine                 |
| `sounddevice`    | Mikrofon-Aufnahme                         |
| `soundfile`      | WAV-Export                                |
| `python-dotenv`  | .env Konfiguration                        |
| `numpy`          | Audio-Verarbeitung                        |
| `typer`          | CLI-Framework                             |
| `pynput`         | Globale Hotkeys + Keyboard-Simulation     |
| `pystray`        | System-Tray-Icon                          |
| `pyperclip`      | Clipboard (Fallback)                      |
| `faster-whisper` | Schnelleres lokales Backend (CTranslate2) |

### macOS-only

| Paket                   | Zweck                                  |
| ----------------------- | -------------------------------------- |
| `rumps`                 | Menübar-App (NSStatusBar)              |
| `quickmachotkey`        | Globale Hotkeys (Carbon API, kein TCC) |
| `pyobjc-*`              | Cocoa-Bindings (NSWorkspace, etc.)     |
| `lightning-whisper-mlx` | Schnellstes Backend auf Apple Silicon  |

### Windows-only

| Paket      | Zweck                                 |
| ---------- | ------------------------------------- |
| `PySide6`  | GPU-beschleunigtes Overlay (optional) |
| `pywin32`  | Windows API (win32gui, win32process)  |
| `psutil`   | Prozess-Info für App-Detection        |
| `Pillow`   | Icons für pystray                     |
| `watchdog` | Datei-Änderungen beobachten           |

**Externe:**

- `ffmpeg` (für lokalen Modus, beide Plattformen)
- `portaudio` (macOS: `brew install portaudio`)

## Konfiguration (ENV-Variablen)

### Allgemein

| Variable                | Beschreibung                                                       |
| ----------------------- | ------------------------------------------------------------------ |
| `PULSESCRIBE_MODE`      | Default-Modus: `openai`, `local`, `deepgram`, `groq`               |
| `PULSESCRIBE_MODEL`     | Transkriptions-Modell (überschreibt Provider-Default)              |
| `PULSESCRIBE_LANGUAGE`  | Sprache für Transkription: `de`, `en`, etc. (default: auto-detect) |
| `PULSESCRIBE_STREAMING` | WebSocket-Streaming für Deepgram: `true`/`false`                   |

### LLM-Nachbearbeitung

| Variable                      | Beschreibung                                       |
| ----------------------------- | -------------------------------------------------- |
| `PULSESCRIBE_REFINE`          | LLM-Nachbearbeitung: `true`/`false`                |
| `PULSESCRIBE_REFINE_MODEL`    | Modell für Refine (default: `openai/gpt-oss-120b`) |
| `PULSESCRIBE_REFINE_PROVIDER` | Provider: `groq`, `openai` oder `openrouter`       |
| `PULSESCRIBE_CONTEXT`         | Kontext-Override: `email`/`chat`/`code`            |
| `PULSESCRIBE_APP_CONTEXTS`    | Custom App-Mappings (JSON)                         |

### UI & Verhalten

| Variable                        | Beschreibung                                                             |
| ------------------------------- | ------------------------------------------------------------------------ |
| `PULSESCRIBE_OVERLAY`           | Untertitel-Overlay aktivieren: `true`/`false`                            |
| `PULSESCRIBE_DOCK_ICON`         | Dock-Icon anzeigen: `true`/`false` (default: `true`)                     |
| `PULSESCRIBE_SHOW_RTF`          | RTF nach Transkription anzeigen: `true`/`false` (default: `false`)       |
| `PULSESCRIBE_CLIPBOARD_RESTORE` | Clipboard nach Paste wiederherstellen: `true`/`false` (default: `false`) |

### Hotkeys

| Variable                    | Beschreibung                                              |
| --------------------------- | --------------------------------------------------------- |
| `PULSESCRIBE_TOGGLE_HOTKEY` | Toggle-Hotkey: z.B. `fn`, `f19`, `ctrl+alt+r`, `capslock` |
| `PULSESCRIBE_HOLD_HOTKEY`   | Hold-Hotkey: z.B. `fn`, `ctrl+alt+space`                  |
| `PULSESCRIBE_HOTKEY`        | Legacy: Single-Hotkey (überschrieben durch TOGGLE/HOLD)   |
| `PULSESCRIBE_HOTKEY_MODE`   | Legacy: `toggle` oder `hold`                              |

### Lokaler Modus

| Variable                               | Beschreibung                                             |
| -------------------------------------- | -------------------------------------------------------- |
| `PULSESCRIBE_LOCAL_BACKEND`            | Backend: `whisper`, `faster`, `mlx`, `lightning`, `auto` |
| `PULSESCRIBE_LOCAL_MODEL`              | Modell: `turbo`, `large`, `large-v3`, etc.               |
| `PULSESCRIBE_DEVICE`                   | Device für openai-whisper: `auto`, `mps`, `cpu`, `cuda`  |
| `PULSESCRIBE_FP16`                     | FP16 für openai-whisper erzwingen: `true`/`false`        |
| `PULSESCRIBE_LOCAL_FAST`               | Schnelleres Decoding: `true`/`false`                     |
| `PULSESCRIBE_LOCAL_BEAM_SIZE`          | Beam-Size (default: 1)                                   |
| `PULSESCRIBE_LOCAL_BEST_OF`            | Best-of (default: 1)                                     |
| `PULSESCRIBE_LOCAL_TEMPERATURE`        | Temperature (default: 0.0)                               |
| `PULSESCRIBE_LOCAL_COMPUTE_TYPE`       | faster-whisper Compute-Type: `int8`, `float16`           |
| `PULSESCRIBE_LOCAL_CPU_THREADS`        | faster-whisper CPU-Threads (0=auto)                      |
| `PULSESCRIBE_LOCAL_NUM_WORKERS`        | faster-whisper Workers (default: 1)                      |
| `PULSESCRIBE_LOCAL_WITHOUT_TIMESTAMPS` | Timestamps deaktivieren: `true`/`false`                  |
| `PULSESCRIBE_LOCAL_VAD_FILTER`         | VAD-Filter: `true`/`false`                               |
| `PULSESCRIBE_LOCAL_WARMUP`             | Warmup bei Start: `true`/`false`/`auto`                  |
| `PULSESCRIBE_LIGHTNING_BATCH_SIZE`     | Batch-Size für Lightning (default: 12)                   |
| `PULSESCRIBE_LIGHTNING_QUANT`          | Quantisierung: `4bit`, `8bit`, oder leer                 |

### API-Keys

| Variable             | Beschreibung                          |
| -------------------- | ------------------------------------- |
| `OPENAI_API_KEY`     | Für API-Modus und OpenAI-Refine       |
| `DEEPGRAM_API_KEY`   | Für Deepgram-Modus (REST + Streaming) |
| `GROQ_API_KEY`       | Für Groq-Modus und Groq-Refine        |
| `OPENROUTER_API_KEY` | Für OpenRouter-Refine                 |

### OpenRouter-Optionen

| Variable                     | Beschreibung                                     |
| ---------------------------- | ------------------------------------------------ |
| `OPENROUTER_PROVIDER_ORDER`  | Provider-Reihenfolge: `Together,DeepInfra`, etc. |
| `OPENROUTER_ALLOW_FALLBACKS` | Fallbacks erlauben: `true`/`false`               |

## Dateipfade

| Pfad                                  | Beschreibung                             |
| ------------------------------------- | ---------------------------------------- |
| `~/.pulsescribe/`                     | User-Konfigurationsverzeichnis           |
| `~/.pulsescribe/.env`                 | User-spezifische ENV-Datei (Priorität 1) |
| `~/.pulsescribe/logs/pulsescribe.log` | Haupt-Logdatei (rotierend, max 1MB)      |
| `~/.pulsescribe/startup.log`          | Emergency-Log für Startup-Fehler         |
| `~/.pulsescribe/vocabulary.json`      | Custom Vocabulary für Transkription      |
| `~/.pulsescribe/prompts.toml`         | Custom Prompts für LLM-Nachbearbeitung   |

## Transkriptions-Modi

| Modus                      | Provider | Methode   | Latenz | Beschreibung                               |
| -------------------------- | -------- | --------- | ------ | ------------------------------------------ |
| `openai`                   | OpenAI   | REST      | ~2-3s  | GPT-4o Transcribe, höchste Qualität        |
| `deepgram`                 | Deepgram | WebSocket | ~300ms | **Streaming** (Default), minimale Latenz   |
| `deepgram (streaming off)` | Deepgram | REST      | ~2-3s  | Fallback via `PULSESCRIBE_STREAMING=false` |
| `groq`                     | Groq     | REST      | ~1s    | Whisper auf LPU, sehr schnell              |
| `local`                    | Whisper  | Lokal     | ~5-10s | Offline, keine API-Kosten                  |

## Kontext-Awareness

Die LLM-Nachbearbeitung passt den Prompt automatisch an den Nutzungskontext an:

| Kontext   | Stil                            | Apps (Beispiele)         |
| --------- | ------------------------------- | ------------------------ |
| `email`   | Formell, vollständige Sätze     | Mail, Outlook, Spark     |
| `chat`    | Locker, kurz und knapp          | Slack, Discord, Messages |
| `code`    | Technisch, Begriffe beibehalten | VS Code, Cursor, iTerm   |
| `default` | Standard-Korrektur              | Alle anderen             |

**Priorität:** CLI (`--context`) > ENV (`PULSESCRIBE_CONTEXT`) > App-Auto-Detection > Default

**Performance:**

- macOS: NSWorkspace-API (~0.2ms) statt AppleScript (~207ms)
- Windows: win32gui + psutil (~1ms)

## Custom Prompts

Prompts können über `~/.pulsescribe/prompts.toml` angepasst werden:

```toml
# Custom Prompts für PulseScribe

[voice_commands]
instruction = """
Eigene Anweisungen für Voice-Commands...
"""

[prompts.email]
prompt = """
Mein angepasster Email-Prompt...
"""

[prompts.chat]
prompt = """
Mein angepasster Chat-Prompt...
"""

[app_contexts]
"Meine App" = "email"
CustomIDE = "code"
```

**Priorität:** CLI > ENV > Custom-TOML > Hardcoded Defaults

**UI:** Settings → Prompts Tab zum Bearbeiten im GUI

## Sprach-Commands

Voice-Commands werden vom LLM in der Refine-Pipeline interpretiert (nur mit `--refine`):

| Befehl (DE/EN)                        | Ergebnis |
| ------------------------------------- | -------- |
| "neuer Absatz" / "new paragraph"      | `\n\n`   |
| "neue Zeile" / "new line"             | `\n`     |
| "Punkt" / "period"                    | `.`      |
| "Komma" / "comma"                     | `,`      |
| "Fragezeichen" / "question mark"      | `?`      |
| "Ausrufezeichen" / "exclamation mark" | `!`      |
| "Doppelpunkt" / "colon"               | `:`      |
| "Semikolon" / "semicolon"             | `;`      |

**Implementierung:** `refine/prompts.py` + `utils/custom_prompts.py` → Voice-Commands werden automatisch in alle Prompts eingefügt via `get_prompt_for_context(context, voice_commands=True)`. Custom Prompts aus `~/.pulsescribe/prompts.toml` haben Priorität.

## Builds (PyInstaller)

### macOS App Bundle

```bash
pip install pyinstaller
pyinstaller build_app.spec --clean
# Output: dist/PulseScribe.app
```

**Besonderheiten:**

- `utils/paths.py`: `get_resource_path()` für Bundle-kompatible Pfade
- `utils/permissions.py`: Mikrofon-Berechtigung mit Alert-Dialog
- **Accessibility-Problem bei unsignierten Bundles:** Siehe README.md → Troubleshooting

### Windows EXE + Installer

```powershell
# API-only Build (Cloud-APIs, ~30MB, schnell)
.\build_windows.ps1 -Clean -Installer

# Local Build mit CUDA Whisper (~4GB, langsam)
.\build_windows.ps1 -Clean -Installer -Local

# Output:
#   dist/PulseScribe/PulseScribe.exe          (portable)
#   dist/PulseScribe-Setup-1.2.0.exe          (API-only Installer)
#   dist/PulseScribe-Setup-1.2.0-Local.exe    (Local Installer, mit -Local)
```

**Build-Varianten:**

| Flag | Inhalt | Größe | Use Case |
|------|--------|-------|----------|
| (ohne) | Cloud-APIs (Deepgram, OpenAI, Groq) | ~30MB | Empfohlen |
| `-Local` | + CUDA Whisper (faster-whisper, torch) | ~4GB | Offline-Nutzung |

**Besonderheiten:**

- Konsolen-Fenster versteckt (`--noconsole` in Spec)
- PySide6-Overlay optional (Fallback auf Tkinter)
- Installer via Inno Setup (`installer_windows.iss`)
  - Start-Menü + optionale Desktop-Verknüpfung
  - Autostart-Option (Registry)
  - Saubere Deinstallation über Windows "Apps & Features"
  - Per-User Install (keine Admin-Rechte nötig)

**Voraussetzungen:**

- [Inno Setup 6](https://jrsoftware.org/isinfo.php) für Installer-Build
- Siehe `docs/BUILDING_WINDOWS.md` für Details

### Gemeinsam

- Logs in `~/.pulsescribe/logs/` (nicht im Bundle)
- Emergency Logging in `~/.pulsescribe/startup.log` für Crash-Debugging

## Entwicklungs-Konventionen

- Python 3.10+ (Type Hints mit `|` statt `Union`)
- Keine unnötigen Abstraktionen
- Fehler → stderr, Ergebnis → stdout
- Deutsche CLI-Ausgaben (Zielgruppe)
- Atomare, kleine Commits
