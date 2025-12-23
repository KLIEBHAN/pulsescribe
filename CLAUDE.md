# Claude Code Projektanweisungen

## Projekt-Übersicht

**PulseScribe** – Minimalistische Spracheingabe für macOS und Windows, inspiriert von [Wispr Flow](https://wisprflow.ai).

Siehe [docs/VISION.md](docs/VISION.md) für Roadmap und langfristige Ziele.
Siehe [docs/WINDOWS_MVP.md](docs/WINDOWS_MVP.md) für Windows-Port Status.

## Architektur

```
pulsescribe/
├── transcribe.py          # CLI Orchestrierung (Wrapper)
├── pulsescribe_daemon.py  # macOS Daemon (NSApplication Loop)
├── pulsescribe_windows.py # Windows Daemon (pystray + pynput)
├── start_daemon.command   # macOS Login Item für Auto-Start
├── start_daemon.bat       # Windows Batch für Auto-Start
├── build_app.spec         # PyInstaller Spec für macOS App Bundle
├── build_windows.spec     # PyInstaller Spec für Windows EXE
├── config.py              # Zentrale Konfiguration (Pfade, Konstanten)
├── requirements.txt       # Dependencies (beide Plattformen)
├── README.md              # Benutzer-Dokumentation
├── CLAUDE.md              # Diese Datei
├── docs/                  # Dokumentation (Vision, Deepgram, Windows MVP, etc.)
├── audio/                 # Audio-Aufnahme und -Handling
├── providers/             # Transkriptions-Provider (Deepgram, OpenAI, etc.)
├── refine/                # LLM-Nachbearbeitung und Kontext
│   └── prompts.py         # Prompt-Templates (Consolidated)
├── ui/                    # User Interface Components
│   ├── menubar.py         # macOS MenuBar Controller (NSStatusBar)
│   ├── overlay.py         # macOS Overlay Controller & SoundWave
│   └── overlay_pyside6.py # Windows Overlay (PySide6, GPU-beschleunigt)
├── whisper_platform/      # Plattform-Abstraktion (Factory Pattern)
│   ├── __init__.py        # Exports: get_clipboard, get_hotkey_listener, etc.
│   ├── clipboard.py       # MacOSClipboard / WindowsClipboard
│   ├── sound.py           # MacOSSound / WindowsSound
│   ├── hotkey.py          # Hotkey-Listener (QuickMacHotKey / pynput)
│   ├── app_detection.py   # Aktive App erkennen (NSWorkspace / win32gui)
│   └── paste.py           # Auto-Paste (AppleScript / pynput Ctrl+V)
├── utils/                 # Utilities (Logging, Hotkey, etc.)
│   ├── paths.py           # Pfad-Helper für PyInstaller Bundle
│   └── permissions.py     # macOS Berechtigungs-Checks (Mikrofon)
└── tests/                 # Unit & Integration Tests
```

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

### Windows: `pulsescribe_windows.py`

Separater Entry-Point mit Windows-nativen Komponenten:

| Klasse                      | Modul               | Zweck                                              |
| --------------------------- | ------------------- | -------------------------------------------------- |
| `PySide6OverlayController`  | `ui.overlay_pyside6`| GPU-beschleunigtes Overlay (Fallback: Tkinter)     |
| `pystray.Icon`              | extern              | System-Tray-Icon mit Farbstatus                    |
| `pynput.keyboard.Listener`  | extern              | Globale Hotkeys (F1-F24, Ctrl+Alt+X, etc.)         |
| `PulseScribeWindows`        | `pulsescribe_windows`| Hauptklasse: State-Machine + Orchestrierung       |

**Features:**
- Pre-Warming (SDK-Imports, DNS-Prefetch, PortAudio) für schnellen Start
- LOADING-State für akkurates UI-Feedback während Mikrofon-Init
- Native Clipboard via ctypes (kein Tkinter/pyperclip)
- Windows System-Sounds (DeviceConnect, Notification.SMS, etc.)

## CLI-Interface

```bash
# Datei transkribieren
python transcribe.py audio.mp3
python transcribe.py audio.mp3 --mode local --model large

# Mikrofon-Aufnahme
python transcribe.py --record --copy --language de
```

## Dependencies

### Shared (beide Plattformen)

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

### macOS-only

| Paket            | Zweck                                     |
| ---------------- | ----------------------------------------- |
| `rumps`          | Menübar-App (NSStatusBar)                 |
| `quickmachotkey` | Globale Hotkeys (Carbon API, kein TCC)    |
| `pyobjc-*`       | Cocoa-Bindings (NSWorkspace, etc.)        |

### Windows-only

| Paket            | Zweck                                     |
| ---------------- | ----------------------------------------- |
| `pystray`        | System-Tray-Icon                          |
| `pynput`         | Globale Hotkeys + Ctrl+V Simulation       |
| `PySide6`        | GPU-beschleunigtes Overlay (optional)     |
| `pywin32`        | Windows API (win32gui, win32process)      |
| `psutil`         | Prozess-Info für App-Detection            |
| `Pillow`         | Icons für pystray                         |

**Externe:**

- `ffmpeg` (für lokalen Modus, beide Plattformen)
- `portaudio` (macOS: `brew install portaudio`)

## Konfiguration (ENV-Variablen)

| Variable                           | Beschreibung                                                             |
| ---------------------------------- | ------------------------------------------------------------------------ |
| `PULSESCRIBE_MODE`                 | Default-Modus: `openai`, `local`, `deepgram`, `groq`                     |
| `PULSESCRIBE_MODEL`                | Transkriptions-Modell (überschreibt Provider-Default)                    |
| `PULSESCRIBE_STREAMING`            | WebSocket-Streaming für Deepgram: `true`/`false`                         |
| `PULSESCRIBE_REFINE`               | LLM-Nachbearbeitung: `true`/`false`                                      |
| `PULSESCRIBE_REFINE_MODEL`         | Modell für Refine (default: `openai/gpt-oss-120b`)                       |
| `PULSESCRIBE_REFINE_PROVIDER`      | Provider: `groq`, `openai` oder `openrouter`                             |
| `PULSESCRIBE_CONTEXT`              | Kontext-Override: `email`/`chat`/`code`                                  |
| `PULSESCRIBE_APP_CONTEXTS`         | Custom App-Mappings (JSON)                                               |
| `PULSESCRIBE_OVERLAY`              | Untertitel-Overlay aktivieren: `true`/`false`                            |
| `PULSESCRIBE_DOCK_ICON`            | Dock-Icon anzeigen: `true`/`false` (default: `true`)                     |
| `PULSESCRIBE_SHOW_RTF`             | RTF nach Transkription anzeigen: `true`/`false` (default: `false`)       |
| `PULSESCRIBE_CLIPBOARD_RESTORE`    | Clipboard nach Paste wiederherstellen: `true`/`false` (default: `false`) |
| `OPENAI_API_KEY`                   | Für API-Modus und OpenAI-Refine                                          |
| `DEEPGRAM_API_KEY`                 | Für Deepgram-Modus (REST + Streaming)                                    |
| `GROQ_API_KEY`                     | Für Groq-Modus und Groq-Refine                                           |
| `OPENROUTER_API_KEY`               | Für OpenRouter-Refine                                                    |
| `PULSESCRIBE_LOCAL_BACKEND`        | Lokales Backend: `whisper`, `faster`, `mlx`, `lightning`, `auto`         |
| `PULSESCRIBE_LOCAL_MODEL`          | Lokales Modell: `turbo`, `large`, `large-v3`, etc.                       |
| `PULSESCRIBE_LIGHTNING_BATCH_SIZE` | Batch-Size für Lightning (default: 12, höher=schneller)                  |
| `PULSESCRIBE_LIGHTNING_QUANT`      | Quantisierung für Lightning: `4bit`, `8bit`, oder leer (None)            |

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

| Befehl (DE/EN)                   | Ergebnis |
| -------------------------------- | -------- |
| "neuer Absatz" / "new paragraph" | `\n\n`   |
| "neue Zeile" / "new line"        | `\n`     |
| "Punkt" / "period"               | `.`      |
| "Komma" / "comma"                | `,`      |
| "Fragezeichen" / "question mark" | `?`      |

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

### Windows EXE

```bash
pip install pyinstaller
pyinstaller build_windows.spec --clean
# Output: dist/PulseScribe.exe
```

**Besonderheiten:**
- Konsolen-Fenster versteckt (`--noconsole` in Spec)
- PySide6-Overlay optional (Fallback auf Tkinter)

### Gemeinsam

- Logs in `~/.pulsescribe/logs/` (nicht im Bundle)
- Emergency Logging in `~/.pulsescribe/startup.log` für Crash-Debugging

## Entwicklungs-Konventionen

- Python 3.10+ (Type Hints mit `|` statt `Union`)
- Keine unnötigen Abstraktionen
- Fehler → stderr, Ergebnis → stdout
- Deutsche CLI-Ausgaben (Zielgruppe)
- Atomare, kleine Commits
