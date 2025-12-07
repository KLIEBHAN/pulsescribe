# Claude Code Projektanweisungen

## Projekt-Übersicht

**whisper_go** – Minimalistische Spracheingabe für macOS, inspiriert von [Wispr Flow](https://wisprflow.ai).

Siehe [docs/VISION.md](docs/VISION.md) für Roadmap und langfristige Ziele.

## Architektur

```
whisper_go/
├── transcribe.py          # CLI für Transkription
├── requirements.txt       # Dependencies
├── README.md              # Benutzer-Dokumentation
├── CLAUDE.md              # Diese Datei
├── docs/
│   └── VISION.md          # Produkt-Vision & Roadmap
└── whisper-go-raycast/    # Raycast Extension (Phase 2)
    ├── src/
    │   └── toggle-recording.tsx
    └── package.json
```

## Kern-Datei: `transcribe.py`

**Funktionen:**

| Funktion                     | Zweck                                          |
| ---------------------------- | ---------------------------------------------- |
| `record_audio()`             | Mikrofon-Aufnahme (interaktiv, mit ENTER)      |
| `record_audio_daemon()`      | Mikrofon-Aufnahme (Signal-basiert, Raycast)    |
| `play_ready_sound()`         | Akustisches Feedback bei Aufnahmestart (macOS) |
| `transcribe()`               | Zentrale API – wählt Modus automatisch         |
| `transcribe_with_api()`      | OpenAI API Transkription                       |
| `transcribe_with_deepgram()` | Deepgram Nova-3 Transkription                  |
| `transcribe_locally()`       | Lokales Whisper-Modell                         |
| `detect_context()`           | Kontext-Erkennung (CLI/ENV/App-Auto-Detection) |
| `_get_frontmost_app()`       | Aktive App via NSWorkspace (macOS, ~0.2ms)     |
| `_app_to_context()`          | App-Name → Kontext-Typ Mapping                 |
| `refine_transcript()`        | LLM-Nachbearbeitung (kontext-aware Prompts)    |
| `_get_refine_client()`       | Client-Factory für Refine-Provider             |
| `run_daemon_mode()`          | Raycast-Modus: Aufnahme → Transkript-Datei     |
| `parse_args()`               | CLI-Argument-Handling                          |

**Design-Entscheidungen:**

- Lazy Imports: `openai`, `whisper`, `sounddevice` werden erst bei Bedarf importiert
- Stderr für Status, Stdout nur für Output → saubere Pipe-Nutzung
- Eine Datei statt mehrere → KISS-Prinzip
- Flache Struktur mit Early Returns

## CLI-Interface

```bash
# Datei transkribieren
python transcribe.py audio.mp3
python transcribe.py audio.mp3 --mode local --model large

# Mikrofon-Aufnahme
python transcribe.py --record --copy --language de
```

## Dependencies

| Paket            | Zweck                               |
| ---------------- | ----------------------------------- |
| `openai`         | API-Modus + LLM-Refine (OpenRouter) |
| `openai-whisper` | Lokaler Modus                       |
| `deepgram-sdk`   | Deepgram Nova-3 Transkription       |
| `sounddevice`    | Mikrofon-Aufnahme                   |
| `soundfile`      | WAV-Export                          |
| `pyperclip`      | Zwischenablage                      |
| `python-dotenv`  | .env Konfiguration                  |

**Externe:**

- `ffmpeg` (für lokalen Modus)
- `portaudio` (für Mikrofon auf macOS)

## Konfiguration (ENV-Variablen)

| Variable                     | Beschreibung                              |
| ---------------------------- | ----------------------------------------- |
| `WHISPER_GO_MODE`            | Default-Modus: `api`, `local`, `deepgram` |
| `WHISPER_GO_REFINE`          | LLM-Nachbearbeitung: `true`/`false`       |
| `WHISPER_GO_REFINE_MODEL`    | Modell für Refine (default: `gpt-5-nano`) |
| `WHISPER_GO_REFINE_PROVIDER` | Provider: `openai` oder `openrouter`      |
| `WHISPER_GO_CONTEXT`         | Kontext-Override: `email`/`chat`/`code`   |
| `WHISPER_GO_APP_CONTEXTS`    | Custom App-Mappings (JSON)                |
| `OPENAI_API_KEY`             | Für API-Modus und OpenAI-Refine           |
| `DEEPGRAM_API_KEY`           | Für Deepgram-Modus                        |
| `OPENROUTER_API_KEY`         | Für OpenRouter-Refine                     |

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

## Entwicklungs-Konventionen

- Python 3.10+ (Type Hints mit `|` statt `Union`)
- Keine unnötigen Abstraktionen
- Fehler → stderr, Ergebnis → stdout
- Deutsche CLI-Ausgaben (Zielgruppe)
- Atomare, kleine Commits
