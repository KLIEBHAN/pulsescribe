# whisper_go

[![Tests](https://github.com/KLIEBHAN/whisper_go/actions/workflows/test.yml/badge.svg)](https://github.com/KLIEBHAN/whisper_go/actions/workflows/test.yml)
[![codecov](https://codecov.io/gh/KLIEBHAN/whisper_go/graph/badge.svg)](https://codecov.io/gh/KLIEBHAN/whisper_go)

Spracheingabe für macOS – inspiriert von [Wispr Flow](https://wisprflow.ai). Transkribiert Audio mit OpenAI Whisper über API, Deepgram, Groq oder lokal.

**Features:** Echtzeit-Streaming (Deepgram) · Mehrere Provider (OpenAI, Deepgram, Groq, lokal) · LLM-Nachbearbeitung · Kontext-Awareness · Custom Vocabulary · Raycast-Hotkeys · Live-Preview Overlay · Menübar-Feedback

> **Performance:** Ultra-Fast-Startup mit ~170ms bis Ready-Sound dank parallelem Mikrofon- und WebSocket-Init. Audio wird während der Aufnahme transkribiert – Ergebnis erscheint sofort nach dem Stoppen.

## Schnellstart

In unter 2 Minuten einsatzbereit:

```bash
# 1. Repository klonen
git clone https://github.com/KLIEBHAN/whisper_go.git && cd whisper_go

# 2. Dependencies installieren
pip install -r requirements.txt

# 3. API-Key setzen (Deepgram: 200$ Startguthaben)
export DEEPGRAM_API_KEY="dein_key"

# 4. Erste Aufnahme
python transcribe.py --record --copy --mode deepgram
```

> **Tipp:** Kopiere `.env.example` nach `.env` für dauerhafte Konfiguration. Für systemweite Hotkeys siehe [Raycast Integration](#raycast-integration).

## CLI-Nutzung

Zwei Hauptfunktionen: Audiodateien transkribieren oder direkt vom Mikrofon aufnehmen.

### Audiodatei transkribieren

```bash
python transcribe.py audio.mp3                        # Standard (API-Modus)
python transcribe.py audio.mp3 --mode deepgram        # Deepgram Nova-3
python transcribe.py audio.mp3 --mode groq            # Groq (schnellste Option)
python transcribe.py audio.mp3 --mode local           # Offline mit lokalem Whisper
```

### Mikrofon-Aufnahme

```bash
python transcribe.py --record                         # Aufnehmen und ausgeben
python transcribe.py --record --copy                  # Direkt in Zwischenablage
python transcribe.py --record --refine                # Mit LLM-Nachbearbeitung
```

**Workflow:** Enter → Sprechen → Enter → Transkript erscheint

### Alle Optionen

| Option                              | Beschreibung                                                                  |
| ----------------------------------- | ----------------------------------------------------------------------------- |
| `--mode api\|local\|deepgram\|groq` | Transkriptions-Provider (default: `api`)                                      |
| `--model NAME`                      | Modell (CLI > `WHISPER_GO_MODEL` env > Provider-Default)                      |
| `--record`, `-r`                    | Mikrofon-Aufnahme statt Datei                                                 |
| `--copy`, `-c`                      | Ergebnis in Zwischenablage                                                    |
| `--language CODE`                   | Sprachcode z.B. `de`, `en`                                                    |
| `--format FORMAT`                   | Output: `text`, `json`, `srt`, `vtt` (nur API-Modus)                          |
| `--no-streaming`                    | WebSocket-Streaming deaktivieren (nur Deepgram)                               |
| `--refine`                          | LLM-Nachbearbeitung aktivieren                                                |
| `--no-refine`                       | LLM-Nachbearbeitung deaktivieren (überschreibt env)                           |
| `--refine-model`                    | Modell für Nachbearbeitung (default: `gpt-5-nano`)                            |
| `--refine-provider`                 | LLM-Provider: `openai`, `openrouter`, `groq`                                  |
| `--context`                         | Kontext für Nachbearbeitung: `email`, `chat`, `code`, `default` (auto-detect) |

## Konfiguration

Alle Einstellungen können per Umgebungsvariable oder `.env`-Datei gesetzt werden. CLI-Argumente haben immer Vorrang.

### API-Keys

Je nach gewähltem Modus wird ein API-Key benötigt:

```bash
# OpenAI (für --mode api und --refine mit openai)
export OPENAI_API_KEY="sk-..."

# Deepgram (für --mode deepgram) – 200$ Startguthaben
export DEEPGRAM_API_KEY="..."

# Groq (für --mode groq und --refine mit groq) – kostenlose Credits
export GROQ_API_KEY="gsk_..."

# OpenRouter (Alternative für --refine) – Hunderte Modelle
export OPENROUTER_API_KEY="sk-or-..."
```

### Standard-Einstellungen

```bash
# Transkriptions-Modus (api, local, deepgram, groq)
export WHISPER_GO_MODE="deepgram"

# Transkriptions-Modell (überschreibt Provider-Default)
export WHISPER_GO_MODEL="nova-3"

# WebSocket-Streaming für Deepgram (default: true)
export WHISPER_GO_STREAMING="true"

# LLM-Nachbearbeitung
export WHISPER_GO_REFINE="true"
export WHISPER_GO_REFINE_MODEL="gpt-5-nano"
export WHISPER_GO_REFINE_PROVIDER="openai"  # oder openrouter, groq
```

### System-Abhängigkeiten

Für bestimmte Modi werden zusätzliche Tools benötigt:

```bash
# Lokaler Modus (ffmpeg für Audio-Konvertierung)
brew install ffmpeg          # macOS
sudo apt install ffmpeg      # Ubuntu/Debian

# Mikrofon-Aufnahme (macOS)
brew install portaudio
```

## Erweiterte Features

Über die Basis-Transkription hinaus bietet whisper_go intelligente Nachbearbeitung und Anpassung.

### LLM-Nachbearbeitung

Entfernt Füllwörter (ähm, also, quasi), korrigiert Grammatik und formatiert in saubere Absätze:

```bash
python transcribe.py --record --refine
```

Unterstützte Provider: OpenAI (default), [OpenRouter](https://openrouter.ai), [Groq](https://groq.com)

### Kontext-Awareness

Die Nachbearbeitung erkennt automatisch die aktive App und passt den Schreibstil an:

| Kontext   | Apps                      | Stil                            |
| --------- | ------------------------- | ------------------------------- |
| `email`   | Mail, Outlook, Spark      | Formell, vollständige Sätze     |
| `chat`    | Slack, Discord, Messages  | Locker, kurz und knapp          |
| `code`    | VS Code, Cursor, Terminal | Technisch, Begriffe beibehalten |
| `default` | Alle anderen              | Standard-Korrektur              |

```bash
# Automatische Erkennung (Standard)
python transcribe.py --record --refine

# Manueller Override
python transcribe.py --record --refine --context email

# Eigene App-Mappings
export WHISPER_GO_APP_CONTEXTS='{"MyApp": "chat"}'
```

### Sprach-Commands

Steuere Formatierung durch gesprochene Befehle (automatisch aktiv mit `--refine`):

| Deutsch          | Englisch           | Ergebnis |
| ---------------- | ------------------ | -------- |
| "neuer Absatz"   | "new paragraph"    | Absatz   |
| "neue Zeile"     | "new line"         | Umbruch  |
| "Punkt"          | "period"           | `.`      |
| "Komma"          | "comma"            | `,`      |
| "Fragezeichen"   | "question mark"    | `?`      |
| "Ausrufezeichen" | "exclamation mark" | `!`      |
| "Doppelpunkt"    | "colon"            | `:`      |
| "Semikolon"      | "semicolon"        | `;`      |

```bash
# Beispiel
python transcribe.py --record --refine
# Spreche: "Hallo Punkt wie geht es dir Fragezeichen"
# Ergebnis: "Hallo. Wie geht es dir?"
```

> **Hinweis:** Sprach-Commands werden vom LLM interpretiert – sie funktionieren nur mit `--refine`.

### Custom Vocabulary

Eigene Begriffe für bessere Erkennung in `~/.whisper_go/vocabulary.json`:

```json
{
  "keywords": ["Anthropic", "Claude", "Kubernetes", "OAuth"]
}
```

Unterstützt von Deepgram und lokalem Whisper. Die OpenAI API unterstützt kein Custom Vocabulary – dort hilft die LLM-Nachbearbeitung.

## Hotkey Integration

Für systemweite Spracheingabe per Hotkey – der Hauptanwendungsfall von whisper_go.

### Unified Daemon (empfohlen)

Der `whisper_daemon.py` kombiniert alle Komponenten in einem Prozess:
- Hotkey-Listener (QuickMacHotKey)
- Microphone Recording + Deepgram Streaming
- Menübar-Status (🎤 🔴 ⏳ ✅ ❌)
- Overlay mit Animationen
- Auto-Paste

```bash
# Manueller Start
python whisper_daemon.py

# Mit CLI-Optionen
python whisper_daemon.py --hotkey cmd+shift+r --debug

# Als Login Item (Doppelklick oder zu Anmeldeobjekten hinzufügen)
open start_daemon.command
```

> **Keine Accessibility-Berechtigung erforderlich!** QuickMacHotKey nutzt die native Carbon-API (`RegisterEventHotKey`).

### Alternative: Hotkey-Daemon + Raycast

Für Raycast-Integration oder wenn du Overlay/Menübar separat steuern möchtest:

```bash
# Hotkey-Daemon als LaunchAgent installieren
./scripts/install_hotkey_daemon.sh
```

### Konfiguration

In `.env` oder als Umgebungsvariable:

```bash
# Hotkey (default: F19)
WHISPER_GO_HOTKEY=f19

# Modus: toggle (PTT nicht unterstützt mit QuickMacHotKey)
WHISPER_GO_HOTKEY_MODE=toggle
```

**Unterstützte Hotkeys:**

| Format            | Beispiel              |
| ----------------- | --------------------- |
| Funktionstasten   | `f19`, `f1`, `f12`    |
| Einzeltaste       | `space`, `tab`, `esc` |
| Tastenkombination | `cmd+shift+r`         |

### Nutzung

**Toggle-Mode:**

- F19 drücken → Aufnahme startet
- F19 nochmal drücken → Transkript wird eingefügt

### Visuelles Feedback

Der Unified Daemon zeigt automatisch:

| Komponente | Beschreibung |
|------------|--------------|
| **Menübar** | Status-Icon (🎤 🔴 ⏳ ✅ ❌) + Live-Preview |
| **Overlay** | Animierte Schallwellen am unteren Bildschirmrand |

Beides ist integriert und startet automatisch mit dem Daemon.

## Provider-Vergleich

| Modus      | Provider | Methode   | Latenz    | Besonderheit                        |
| ---------- | -------- | --------- | --------- | ----------------------------------- |
| `deepgram` | Deepgram | WebSocket | ~300ms ⚡ | Echtzeit-Streaming (empfohlen)      |
| `groq`     | Groq     | REST      | ~1s       | Whisper auf LPU, sehr schnell       |
| `api`      | OpenAI   | REST      | ~2-3s     | GPT-4o Transcribe, höchste Qualität |
| `local`    | Whisper  | Lokal     | ~5-10s    | Offline, keine API-Kosten           |

> **Empfehlung:** `--mode deepgram` für den täglichen Gebrauch. Die Streaming-Architektur sorgt für minimale Wartezeit zwischen Aufnahme-Stopp und Text-Einfügen.

## Modell-Referenz

### API-Modelle (OpenAI)

| Modell                   | Beschreibung         |
| ------------------------ | -------------------- |
| `gpt-4o-transcribe`      | Beste Qualität ⭐    |
| `gpt-4o-mini-transcribe` | Schneller, günstiger |
| `whisper-1`              | Original Whisper     |

### Deepgram-Modelle

| Modell   | Beschreibung                       |
| -------- | ---------------------------------- |
| `nova-3` | Neuestes Modell, beste Qualität ⭐ |
| `nova-2` | Bewährtes Modell, günstiger        |

`smart_format` ist aktiviert – automatische Formatierung von Datum, Währung und Absätzen.

#### Echtzeit-Streaming (Standard)

Deepgram nutzt standardmäßig **WebSocket-Streaming** für minimale Latenz:

- Audio wird **während der Aufnahme** transkribiert, nicht erst danach
- Ergebnis erscheint **sofort** nach dem Stoppen (statt 2-3s Wartezeit)
- Ideal für die Hotkey-Integration

```bash
# Streaming (Standard)
python transcribe.py --record --mode deepgram

# REST-Fallback (falls Streaming Probleme macht)
python transcribe.py --record --mode deepgram --no-streaming
# oder via ENV:
WHISPER_GO_STREAMING=false
```

### Groq-Modelle

| Modell                       | Beschreibung                        |
| ---------------------------- | ----------------------------------- |
| `whisper-large-v3`           | Whisper Large v3, ~300x Echtzeit ⭐ |
| `distil-whisper-large-v3-en` | Nur Englisch, noch schneller        |

Groq nutzt LPU-Chips (Language Processing Units) für besonders schnelle Inferenz.

### Lokale Modelle

| Modell | Parameter | VRAM   | Geschwindigkeit  |
| ------ | --------- | ------ | ---------------- |
| tiny   | 39M       | ~1 GB  | Sehr schnell     |
| base   | 74M       | ~1 GB  | Schnell          |
| small  | 244M      | ~2 GB  | Mittel           |
| medium | 769M      | ~5 GB  | Langsam          |
| large  | 1550M     | ~10 GB | Sehr langsam     |
| turbo  | 809M      | ~6 GB  | Schnell & gut ⭐ |

⭐ = Standard-Modell des Providers

## Troubleshooting

| Problem                     | Lösung                                                                |
| --------------------------- | --------------------------------------------------------------------- |
| Modul nicht installiert     | `pip install -r requirements.txt`                                     |
| API-Key fehlt               | `export DEEPGRAM_API_KEY="..."` (oder OPENAI/GROQ)                    |
| Mikrofon geht nicht (macOS) | `brew install portaudio && pip install --force-reinstall sounddevice` |
| ffmpeg fehlt                | `brew install ffmpeg` (macOS) oder `sudo apt install ffmpeg` (Ubuntu) |
| Transkription langsam       | Wechsel zu `--mode groq` oder `--mode deepgram` statt `local`         |

## Development

```bash
# Test-Dependencies installieren
pip install -r requirements-dev.txt

# Tests ausführen
pytest -v

# Mit Coverage-Report
pytest --cov=. --cov-report=term-missing
```

Tests laufen automatisch via GitHub Actions bei Push und Pull Requests.
