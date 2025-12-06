# Claude Code Projektanweisungen

## Projekt-Übersicht

**whisper_go** – CLI-Tool für Audio-Transkription mit OpenAI Whisper (API + lokal).

## Architektur

```
whisper_go/
├── transcribe.py      # Einziger Einstiegspunkt (CLI)
├── requirements.txt   # Dependencies
├── README.md          # Benutzer-Dokumentation
├── CLAUDE.md          # Diese Datei
└── docs/
    └── doc.md         # Ursprüngliche Planungs-Doku
```

## Kern-Datei: `transcribe.py`

**Funktionen:**

- `record_audio()` – Mikrofon-Aufnahme mit sounddevice
- `copy_to_clipboard()` – Text in Zwischenablage (pyperclip)
- `transcribe_api()` – OpenAI API Transkription
- `transcribe_local()` – Lokales Whisper-Modell
- `main()` – CLI-Argument-Handling

**Design-Entscheidungen:**

- Lazy Imports: `openai`, `whisper`, `sounddevice` werden erst bei Bedarf importiert
- Stderr für Status, Stdout nur für Output → saubere Pipe-Nutzung
- Eine Datei statt mehrere → KISS-Prinzip

## CLI-Interface

```bash
# Datei transkribieren
python transcribe.py <audio> --mode api|local [--model X] [--language X] [--format X]

# Mikrofon-Aufnahme
python transcribe.py --record --mode api|local [--model X] [--language X]
```

## Dependencies

| Paket            | Zweck             |
| ---------------- | ----------------- |
| `openai`         | API-Modus         |
| `openai-whisper` | Lokaler Modus     |
| `sounddevice`    | Mikrofon-Aufnahme |
| `soundfile`      | WAV-Export        |
| `pyperclip`      | Zwischenablage    |

**Externe:**

- `ffmpeg` (für lokalen Modus)
- `portaudio` (für Mikrofon auf macOS)

## Entwicklungs-Konventionen

- Python 3.10+ (Type Hints mit `|` statt `Union`)
- Keine unnötigen Abstraktionen
- Fehler → stderr, Ergebnis → stdout
- Deutsche CLI-Ausgaben (Zielgruppe)

## Erweiterungsmöglichkeiten

Falls gewünscht, könnten ergänzt werden:

- `--translate` Flag für Übersetzung nach Englisch
- Batch-Verarbeitung mehrerer Dateien
- Web-UI mit Streamlit
- Live-Streaming-Transkription
