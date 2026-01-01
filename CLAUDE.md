# Claude Code Projektanweisungen

## Projekt-Übersicht

**PulseScribe** – Minimalistische Spracheingabe für macOS und Windows, inspiriert von [Wispr Flow](https://wisprflow.ai).

**Dokumentation:**
- [README.md](README.md) – Benutzer-Dokumentation
- [docs/CONFIGURATION.md](docs/CONFIGURATION.md) – Alle ENV-Variablen
- [docs/VISION.md](docs/VISION.md) – Roadmap und langfristige Ziele
- [docs/SECURITY.md](docs/SECURITY.md) – Sicherheit und Datenschutz

## Architektur

### Projektstruktur

```
pulsescribe/
├── transcribe.py            # CLI Entry-Point
├── pulsescribe_daemon.py    # macOS Daemon (NSApplication)
├── pulsescribe_windows.py   # Windows Daemon (pystray + pynput)
├── config.py                # Zentrale Konfiguration (Konstanten, Defaults)
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

### Kern-Dateien

| Datei                    | Zweck                                             |
| ------------------------ | ------------------------------------------------- |
| `transcribe.py`          | CLI-Orchestrator, nutzt providers/audio/refine    |
| `pulsescribe_daemon.py`  | macOS: Hotkey + UI + Streaming in einem Prozess   |
| `pulsescribe_windows.py` | Windows: System-Tray + Overlay + State-Machine    |
| `config.py`              | Zentrale Konstanten (Sample-Rates, Timeouts, etc) |

## Daemons

### macOS: `pulsescribe_daemon.py`

| Klasse              | Modul                | Zweck                                           |
| ------------------- | -------------------- | ----------------------------------------------- |
| `MenuBarController` | `ui.menubar`         | Menübar-Status via NSStatusBar (🎤 🔴 ⏳ ✅ ❌) |
| `OverlayController` | `ui.overlay`         | Animiertes Overlay am unteren Bildschirmrand    |
| `PulseScribeDaemon` | `pulsescribe_daemon` | Hauptklasse: Orchestriert Hotkey, Audio & UI    |

**Threading:** Main-Thread (Hotkey + UI Event Loop) + Worker-Thread (Deepgram-Streaming)

### Windows: `pulsescribe_windows.py`

| Klasse                     | Modul                 | Zweck                                          |
| -------------------------- | --------------------- | ---------------------------------------------- |
| `PySide6OverlayController` | `ui.overlay_pyside6`  | GPU-beschleunigtes Overlay (Fallback: Tkinter) |
| `pystray.Icon`             | extern                | System-Tray-Icon mit Farbstatus                |
| `PulseScribeWindows`       | `pulsescribe_windows` | Hauptklasse: State-Machine + Orchestrierung    |

**Features:** Pre-Warming, LOADING-State, Native Clipboard (ctypes), Windows System-Sounds

### Animation-Architektur: `ui/animation.py`

```
ui/animation.py (Single Source of Truth)
├── AnimationLogic (Klasse)
│   ├── update_level() + update_agc()
│   └── calculate_bar_normalized(i, t, state) → 0.0-1.0
│
├── overlay_windows.py  ← nutzt AnimationLogic
├── overlay_pyside6.py  ← nutzt AnimationLogic
└── overlay.py (macOS)  ← nutzt AnimationLogic (außer RECORDING)
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
| `faster-whisper` | Schnelleres lokales Backend (CTranslate2) |

### macOS-only

| Paket                   | Zweck                                  |
| ----------------------- | -------------------------------------- |
| `rumps`                 | Menübar-App (NSStatusBar)              |
| `quickmachotkey`        | Globale Hotkeys (Carbon API, kein TCC) |
| `pyobjc-*`              | Cocoa-Bindings (NSWorkspace, etc.)     |
| `lightning-whisper-mlx` | Schnellstes Backend auf Apple Silicon  |

### Windows-only

| Paket     | Zweck                                 |
| --------- | ------------------------------------- |
| `PySide6` | GPU-beschleunigtes Overlay (optional) |
| `pywin32` | Windows API (win32gui, win32process)  |
| `psutil`  | Prozess-Info für App-Detection        |
| `Pillow`  | Icons für pystray                     |

**Externe:** `ffmpeg` (lokaler Modus), `portaudio` (macOS: `brew install portaudio`)

## Dateipfade

| Pfad                                  | Beschreibung                        |
| ------------------------------------- | ----------------------------------- |
| `~/.pulsescribe/`                     | User-Konfigurationsverzeichnis      |
| `~/.pulsescribe/.env`                 | User-spezifische ENV-Datei          |
| `~/.pulsescribe/logs/pulsescribe.log` | Haupt-Logdatei (rotierend, max 1MB) |
| `~/.pulsescribe/startup.log`          | Emergency-Log für Startup-Fehler    |
| `~/.pulsescribe/vocabulary.json`      | Custom Vocabulary                   |
| `~/.pulsescribe/prompts.toml`         | Custom Prompts                      |

## Build-Dateien

| Datei                                      | Zweck                    | Dokumentation                                    |
| ------------------------------------------ | ------------------------ | ------------------------------------------------ |
| `build_app.sh` / `build_app.spec`          | macOS App Bundle         | [docs/BUILDING_MACOS.md](docs/BUILDING_MACOS.md) |
| `build_dmg.sh`                             | macOS DMG-Erstellung     | [docs/BUILDING_MACOS.md](docs/BUILDING_MACOS.md) |
| `build_windows.ps1` / `build_windows.spec` | Windows EXE + Installer  | [docs/BUILDING_WINDOWS.md](docs/BUILDING_WINDOWS.md) |
| `installer_windows.iss`                    | Inno Setup Script        | [docs/BUILDING_WINDOWS.md](docs/BUILDING_WINDOWS.md) |

## Entwicklungs-Konventionen

- Python 3.10+ (Type Hints mit `|` statt `Union`)
- Keine unnötigen Abstraktionen
- Fehler → stderr, Ergebnis → stdout
- Atomare, kleine Commits
- **PR-Workflow:** Jede Änderung in eigenem Branch + Pull Request

## Weiterführende Dokumentation

| Dokument | Inhalt |
|----------|--------|
| [docs/CONFIGURATION.md](docs/CONFIGURATION.md) | Alle ENV-Variablen und Einstellungen |
| [docs/CLI_REFERENCE.md](docs/CLI_REFERENCE.md) | CLI-Optionen für `transcribe.py` |
| [docs/LOCAL_BACKENDS.md](docs/LOCAL_BACKENDS.md) | Lokale Whisper-Backends |
| [docs/BUILDING_MACOS.md](docs/BUILDING_MACOS.md) | macOS Build-Anleitung |
| [docs/BUILDING_WINDOWS.md](docs/BUILDING_WINDOWS.md) | Windows Build-Anleitung |
