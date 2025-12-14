# Claude Code Projektanweisungen

## Projekt-Übersicht

**whisper_go** – Minimalistische Spracheingabe für macOS, inspiriert von [Wispr Flow](https://wisprflow.ai).

Siehe [docs/VISION.md](docs/VISION.md) für Roadmap und langfristige Ziele.

## Architektur

```
whisper_go/
├── transcribe.py          # CLI Orchestrierung (Wrapper)
├── whisper_daemon.py      # Unified Daemon (Hotkey + Recording + UI)
├── start_daemon.command   # macOS Login Item für Auto-Start
├── build_app.spec         # PyInstaller Spec für macOS App Bundle
├── config.py              # Zentrale Konfiguration (Pfade, Konstanten)
├── requirements.txt       # Dependencies
├── README.md              # Benutzer-Dokumentation
├── CLAUDE.md              # Diese Datei
├── docs/                  # Dokumentation (Vision, Deepgram, etc.)
├── audio/                 # Audio-Aufnahme und -Handling
├── providers/             # Transkriptions-Provider (Deepgram, OpenAI, etc.)
├── refine/                # LLM-Nachbearbeitung und Kontext
│   └── prompts.py         # Prompt-Templates (Consolidated)
├── ui/                    # User Interface Components
│   ├── menubar.py         # MenuBar Controller (mit Quit-Menü)
│   └── overlay.py         # Overlay Controller & SoundWave
├── utils/                 # Utilities (Daemon, Logging, Hotkey)
│   ├── paths.py           # Pfad-Helper für PyInstaller Bundle
│   └── permissions.py     # macOS Berechtigungs-Checks (Mikrofon)
└── tests/                 # Unit & Integration Tests
```

## Kern-Datei: `transcribe.py`

**Funktionen:**

| Funktion            | Zweck                                      |
| ------------------- | ------------------------------------------ |
| `transcribe()`      | Zentrale API – orchestriert Provider       |
| `run_daemon_mode()` | Raycast-Modus: Aufnahme → Transkript-Datei |
| `parse_args()`      | CLI-Argument-Handling                      |

**Design-Entscheidungen:**

- **Modular:** Nutzt `providers.*`, `audio.*`, `refine.*`, `utils.*`
- **Lean:** Orchestrator statt Monolith (~1000 LOC weniger)
- **Kompatibel:** Alle bestehenden CLI-Flags funktionieren weiter
- **Entry-Point:** Bleibt die zentrale Anlaufstelle für Skripte
- **Lazy Imports:** `openai`, `whisper`, `sounddevice` werden erst bei Bedarf importiert
- **Double-Fork:** Daemonisierung für saubere Prozess-Trennung

## Unified Daemon: `whisper_daemon.py`

Konsolidiert alle Komponenten in einem Prozess (empfohlen für tägliche Nutzung):

**Komponenten:**

| Klasse              | Modul            | Zweck                                           |
| ------------------- | ---------------- | ----------------------------------------------- |
| `MenuBarController` | `ui.menubar`     | Menübar-Status via NSStatusBar (🎤 🔴 ⏳ ✅ ❌) |
| `OverlayController` | `ui.overlay`     | Animiertes Overlay am unteren Bildschirmrand    |
| `SoundWaveView`     | `ui.overlay`     | Animierte Schallwellen-Visualisierung           |
| `WhisperDaemon`     | `whisper_daemon` | Hauptklasse: Orchestriert Hotkey, Audio & UI    |

**Architektur:**

- **Main-Thread:** Hotkey-Listener (`utils.hotkey`) + UI Event Loop
- **Worker-Thread:** Deepgram-Streaming via `providers.deepgram_stream`
- **Orchestration:** Daemon steuert UI-Feedback basierend auf Recording-State

## CLI-Interface

```bash
# Datei transkribieren
python transcribe.py audio.mp3
python transcribe.py audio.mp3 --mode local --model large

# Mikrofon-Aufnahme
python transcribe.py --record --copy --language de
```

## Dependencies

| Paket            | Zweck                                     |
| ---------------- | ----------------------------------------- |
| `openai`         | API-Modus + LLM-Refine (OpenRouter)       |
| `openai-whisper` | Lokaler Modus                             |
| `deepgram-sdk`   | Deepgram Nova-3 Transkription (REST + WS) |
| `groq`           | Groq Whisper + LLM-Refine                 |
| `sounddevice`    | Mikrofon-Aufnahme                         |
| `soundfile`      | WAV-Export                                |
| `pyperclip`      | Zwischenablage                            |
| `python-dotenv`  | .env Konfiguration                        |
| `rumps`          | Menübar-App                               |
| `quickmachotkey` | Globale Hotkeys (Carbon API, kein TCC)    |

**Externe:**

- `ffmpeg` (für lokalen Modus)
- `portaudio` (für Mikrofon auf macOS)

## Konfiguration (ENV-Variablen)

| Variable                     | Beschreibung                                          |
| ---------------------------- | ----------------------------------------------------- |
| `WHISPER_GO_MODE`            | Default-Modus: `openai`, `local`, `deepgram`, `groq`  |
| `WHISPER_GO_MODEL`           | Transkriptions-Modell (überschreibt Provider-Default) |
| `WHISPER_GO_STREAMING`       | WebSocket-Streaming für Deepgram: `true`/`false`      |
| `WHISPER_GO_REFINE`          | LLM-Nachbearbeitung: `true`/`false`                   |
| `WHISPER_GO_REFINE_MODEL`    | Modell für Refine (default: `gpt-5-nano`)             |
| `WHISPER_GO_REFINE_PROVIDER` | Provider: `openai`, `openrouter` oder `groq`          |
| `WHISPER_GO_CONTEXT`         | Kontext-Override: `email`/`chat`/`code`               |
| `WHISPER_GO_APP_CONTEXTS`    | Custom App-Mappings (JSON)                            |
| `WHISPER_GO_OVERLAY`         | Untertitel-Overlay aktivieren: `true`/`false`         |
| `WHISPER_GO_DOCK_ICON`       | Dock-Icon anzeigen: `true`/`false` (default: `true`)  |
| `OPENAI_API_KEY`             | Für API-Modus und OpenAI-Refine                       |
| `DEEPGRAM_API_KEY`           | Für Deepgram-Modus (REST + Streaming)                 |
| `GROQ_API_KEY`               | Für Groq-Modus und Groq-Refine                        |
| `OPENROUTER_API_KEY`         | Für OpenRouter-Refine                                 |

## Dateipfade

| Pfad                                | Beschreibung                             |
| ----------------------------------- | ---------------------------------------- |
| `~/.whisper_go/`                    | User-Konfigurationsverzeichnis           |
| `~/.whisper_go/.env`                | User-spezifische ENV-Datei (Priorität 1) |
| `~/.whisper_go/logs/whisper_go.log` | Haupt-Logdatei (rotierend, max 1MB)      |
| `~/.whisper_go/startup.log`         | Emergency-Log für Startup-Fehler         |
| `~/.whisper_go/vocabulary.json`     | Custom Vocabulary für Transkription      |
| `~/.whisper_go/prompts.toml`        | Custom Prompts für LLM-Nachbearbeitung   |

## Transkriptions-Modi

| Modus                     | Provider | Methode   | Latenz | Beschreibung                             |
| ------------------------- | -------- | --------- | ------ | ---------------------------------------- |
| `openai`                  | OpenAI   | REST      | ~2-3s  | GPT-4o Transcribe, höchste Qualität      |
| `deepgram`                | Deepgram | WebSocket | ~300ms | **Streaming** (Default), minimale Latenz |
| `deepgram --no-streaming` | Deepgram | REST      | ~2-3s  | Fallback ohne Streaming                  |
| `groq`                    | Groq     | REST      | ~1s    | Whisper auf LPU, sehr schnell            |
| `local`                   | Whisper  | Lokal     | ~5-10s | Offline, keine API-Kosten                |

## Kontext-Awareness

Die LLM-Nachbearbeitung passt den Prompt automatisch an den Nutzungskontext an:

| Kontext   | Stil                            | Apps (Beispiele)         |
| --------- | ------------------------------- | ------------------------ |
| `email`   | Formell, vollständige Sätze     | Mail, Outlook, Spark     |
| `chat`    | Locker, kurz und knapp          | Slack, Discord, Messages |
| `code`    | Technisch, Begriffe beibehalten | VS Code, Cursor, iTerm   |
| `default` | Standard-Korrektur              | Alle anderen             |

**Priorität:** CLI (`--context`) > ENV (`WHISPER_GO_CONTEXT`) > App-Auto-Detection > Default

**Performance:** NSWorkspace-API (~0.2ms) statt AppleScript (~207ms)

## Custom Prompts

Prompts können über `~/.whisper_go/prompts.toml` angepasst werden:

```toml
# Custom Prompts für whisper_go

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

**Implementierung:** `refine/prompts.py` + `utils/custom_prompts.py` → Voice-Commands werden automatisch in alle Prompts eingefügt via `get_prompt_for_context(context, voice_commands=True)`. Custom Prompts aus `~/.whisper_go/prompts.toml` haben Priorität.

## App Bundle (PyInstaller)

Build einer nativen macOS App:

```bash
pip install pyinstaller
pyinstaller build_app.spec --clean
# Output: dist/WhisperGo.app
```

**Besonderheiten:**

- `utils/paths.py`: `get_resource_path()` für Bundle-kompatible Pfade
- `utils/permissions.py`: Mikrofon-Berechtigung mit Alert-Dialog
- `config.py`: Logs in `~/.whisper_go/logs/` (nicht im Bundle)
- Emergency Logging in `~/.whisper_go/startup.log` für Crash-Debugging
- **Accessibility-Problem bei unsignierten Bundles:** Siehe README.md → Troubleshooting

## Entwicklungs-Konventionen

- Python 3.10+ (Type Hints mit `|` statt `Union`)
- Keine unnötigen Abstraktionen
- Fehler → stderr, Ergebnis → stdout
- Deutsche CLI-Ausgaben (Zielgruppe)
- Atomare, kleine Commits
