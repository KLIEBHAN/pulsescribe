# whisper_go

Einfaches CLI-Tool zum Transkribieren von Audio mit OpenAI Whisper – sowohl über die API als auch lokal.

## Features

- **API-Modus**: Nutzt OpenAI's Cloud-API (schnell, keine GPU nötig)
- **Lokaler Modus**: Nutzt das Open-Source Whisper-Modell offline
- **Mikrofon-Aufnahme**: Enter drücken → sprechen → Enter → fertig
- **Zwischenablage**: Transkript wird automatisch kopiert (bei `--record`)

## Installation

```bash
# Repository klonen / in Verzeichnis wechseln
cd whisper_go

# Dependencies installieren
pip install -r requirements.txt
```

### Zusätzliche Abhängigkeiten

**Für API-Modus (OpenAI):**

```bash
export OPENAI_API_KEY="dein_api_key"
```

**Für Deepgram-Modus:**

```bash
export DEEPGRAM_API_KEY="dein_api_key"
```

> **Tipp:** Deepgram bietet 200$ Startguthaben für neue Accounts. [console.deepgram.com](https://console.deepgram.com)

**Standard-Modus festlegen (optional):**

```bash
# In .env oder Shell-Config
export WHISPER_GO_MODE="deepgram"  # api, local, oder deepgram
```

**LLM-Nachbearbeitung (Flow-Style):**

```bash
# In .env aktivieren
export WHISPER_GO_REFINE="true"

# Optional: OpenRouter statt OpenAI nutzen
export WHISPER_GO_REFINE_PROVIDER="openrouter"
export OPENROUTER_API_KEY="sk-or-..."

# Optional: Backend-Provider für OpenRouter festlegen
export OPENROUTER_PROVIDER_ORDER="Together,DeepInfra"
```

> Entfernt Füllwörter (ähm, also), korrigiert Grammatik und formatiert in saubere Absätze. Unterstützt OpenAI (default) und [OpenRouter](https://openrouter.ai) für Zugang zu hunderten Modellen. Bei OpenRouter kann das Backend-Routing über `OPENROUTER_PROVIDER_ORDER` gesteuert werden.

**Kontext-Awareness (automatisch):**

Die LLM-Nachbearbeitung erkennt automatisch die aktive App und passt den Schreibstil an:

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

# Eigene App-Mappings (JSON)
export WHISPER_GO_APP_CONTEXTS='{"MyApp": "chat"}'
```

**Custom Vocabulary (Namen & Fachbegriffe):**

Eigene Begriffe für bessere Erkennung in `~/.whisper_go/vocabulary.json`:

```json
{
  "keywords": ["Anthropic", "Claude", "Kubernetes", "OAuth"]
}
```

Unterstützt von Deepgram (Nova-3: `keyterm`, Nova-2: `keywords`) und lokalem Whisper (`initial_prompt`). Die OpenAI API unterstützt kein Custom Vocabulary – dort hilft nur die LLM-Nachbearbeitung.

**Für lokalen Modus:**

```bash
# macOS
brew install ffmpeg

# Ubuntu/Debian
sudo apt install ffmpeg
```

**Für Mikrofon-Aufnahme (macOS):**

```bash
brew install portaudio
```

## Raycast Integration (Hotkey)

Für systemweite Spracheingabe per Hotkey – wie [Wispr Flow](https://wisprflow.ai):

```bash
cd whisper-go-raycast
npm install && npm run dev
```

**Setup in Raycast:**

1. "Toggle Recording" suchen
2. ⌘+K → "Assign Hotkey" → **Double-Tap Right Option (⌥⌥)** empfohlen
3. Python-/Script-Pfad werden automatisch erkannt

**Nutzung (Toggle-Modus):**

- ⌥⌥ (doppelt tippen) → Aufnahme startet
- ⌥⌥ (doppelt tippen) → Transkript wird eingefügt

### Push-to-Talk mit Karabiner-Elements

Für echtes Push-to-Talk (Taste halten = Aufnahme, loslassen = einfügen):

1. [Karabiner-Elements](https://karabiner-elements.pqrs.org/) installieren
2. Rule importieren:
   ```bash
   cp scripts/karabiner-ptt.json ~/.config/karabiner/assets/complex_modifications/
   ```
3. In Karabiner: Preferences → Complex Modifications → Add rule → "Whisper Go Push-to-Talk"

**Nutzung (Push-to-Talk):**

- **Hyper+A** (⌘⌃⌥⇧+A) halten → Aufnahme läuft
- **Hyper+A** loslassen → Transkript wird eingefügt

> **Tipp:** Caps Lock als Hyper-Key (⌘⌃⌥⇧) mappen, dann ist es nur Caps+A.
> Die Karabiner-Rule sendet F19. In Raycast muss F19 als Hotkey für "Toggle Recording" gesetzt sein.

### Menübar-Feedback (optional)

Visuelles Feedback in der macOS-Menüleiste während der Aufnahme:

```bash
# Installieren + Autostart einrichten (empfohlen)
./scripts/install_menubar.sh

# Oder manuell starten
pip install rumps
python menubar.py
```

| Icon | Status              |
| ---- | ------------------- |
| 🎤   | Bereit (Idle)       |
| 🔴   | Aufnahme läuft      |
| ⏳   | Transkription läuft |
| ✅   | Erfolgreich         |
| ❌   | Fehler              |

**Autostart-Verwaltung:**

```bash
./scripts/install_menubar.sh status    # Status prüfen
./scripts/install_menubar.sh uninstall # Deinstallieren
```

> Die Menübar-App zeigt nur den Status an. Start/Stop erfolgt weiterhin über Raycast.

## CLI-Nutzung

### Audiodatei transkribieren

```bash
# Mit OpenAI API (default)
python transcribe.py audio.mp3

# Lokal
python transcribe.py audio.mp3 --mode local

# Lokal mit größerem Modell
python transcribe.py audio.mp3 --mode local --model large
```

### Mikrofon-Aufnahme

```bash
python transcribe.py --record
python transcribe.py --record --copy   # direkt in Zwischenablage
```

**Workflow:**

1. Enter drücken → Aufnahme startet
2. Sprechen
3. Enter drücken → Aufnahme stoppt
4. Transkript erscheint (mit `--copy` auch in Zwischenablage)

### Optionen

| Option                        | Beschreibung                                                                                                                    |
| ----------------------------- | ------------------------------------------------------------------------------------------------------------------------------- |
| `--mode api\|local\|deepgram` | API (default), lokales Whisper oder Deepgram                                                                                    |
| `--record`, `-r`              | Mikrofon-Aufnahme statt Datei                                                                                                   |
| `--copy`, `-c`                | Ergebnis in Zwischenablage kopieren                                                                                             |
| `--model NAME`                | Modellname (API: `gpt-4o-transcribe`; Deepgram: `nova-3`, `nova-2`; Lokal: `tiny`, `base`, `small`, `medium`, `large`, `turbo`) |
| `--language CODE`             | Sprachcode z.B. `de`, `en`                                                                                                      |
| `--format FORMAT`             | Output-Format: `text`, `json`, `srt`, `vtt` (nur API)                                                                           |
| `--refine`                    | LLM-Nachbearbeitung aktivieren (auch via `WHISPER_GO_REFINE` env)                                                               |
| `--no-refine`                 | LLM-Nachbearbeitung deaktivieren (überschreibt env)                                                                             |
| `--refine-model MODEL`        | Modell für Nachbearbeitung (default: `gpt-5-nano`, auch via `WHISPER_GO_REFINE_MODEL` env)                                      |
| `--refine-provider`           | LLM-Provider: `openai` (default) oder `openrouter` (auch via `WHISPER_GO_REFINE_PROVIDER` env)                                  |
| `--context`                   | Kontext für Nachbearbeitung: `email`, `chat`, `code`, `default` (auto-detect wenn nicht gesetzt)                                |

### Beispiele

```bash
# Einfachster Aufruf (API-Modus)
python transcribe.py audio.mp3

# Deutsche Sprache, SRT-Untertitel
python transcribe.py interview.mp3 --language de --format srt

# Schnelle lokale Transkription
python transcribe.py meeting.wav --mode local --model tiny

# Deepgram mit automatischer Formatierung
python transcribe.py audio.mp3 --mode deepgram --language de

# Aufnahme auf Deutsch, direkt in Zwischenablage
python transcribe.py --record --language de --copy
```

## Modell-Auswahl

### API-Modelle (OpenAI)

- `gpt-4o-transcribe` (Standard) – Beste Qualität
- `gpt-4o-mini-transcribe` – Schneller, günstiger
- `whisper-1` – Original Whisper

### Deepgram-Modelle

- `nova-3` (Standard) – Neuestes Modell, beste Qualität
- `nova-2` – Bewährtes Modell, etwas günstiger

**Features:** `smart_format` ist aktiviert – automatische Formatierung von Datum, Währung und Absätzen.

### Lokale Modelle

| Modell | Parameter | VRAM   | Geschwindigkeit |
| ------ | --------- | ------ | --------------- |
| tiny   | 39M       | ~1 GB  | Sehr schnell    |
| base   | 74M       | ~1 GB  | Schnell         |
| small  | 244M      | ~2 GB  | Mittel          |
| medium | 769M      | ~5 GB  | Langsam         |
| large  | 1550M     | ~10 GB | Sehr langsam    |
| turbo  | 809M      | ~6 GB  | Schnell & gut   |

## Troubleshooting

**"Modul nicht installiert"**

```bash
pip install -r requirements.txt
```

**"OPENAI_API_KEY nicht gesetzt"**

```bash
export OPENAI_API_KEY="sk-..."
```

**Mikrofon funktioniert nicht (macOS)**

```bash
brew install portaudio
pip install --force-reinstall sounddevice
```

**ffmpeg fehlt**

```bash
# macOS
brew install ffmpeg

# Ubuntu
sudo apt install ffmpeg
```
