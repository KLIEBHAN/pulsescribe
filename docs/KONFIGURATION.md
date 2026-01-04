# Konfigurations-Referenz

[🇺🇸 English Version](CONFIGURATION.md)

Vollständige Referenz aller PulseScribe-Konfigurationsoptionen. Einstellungen können über Umgebungsvariablen oder `~/.pulsescribe/.env` konfiguriert werden.

## Schnellstart

```bash
# Beispielkonfiguration kopieren
cp .env.example ~/.pulsescribe/.env

# Mit API-Keys bearbeiten
nano ~/.pulsescribe/.env
```

**Prioritätsreihenfolge:** CLI-Argumente > Umgebungsvariablen > `.env`-Datei > Defaults

---

## API-Keys

Mindestens ein API-Key für Cloud-Transkription erforderlich:

| Variable             | Provider                   | Key holen                                                                 |
| -------------------- | -------------------------- | ------------------------------------------------------------------------- |
| `DEEPGRAM_API_KEY`   | Deepgram (empfohlen)       | [console.deepgram.com](https://console.deepgram.com) – $200 Startguthaben |
| `OPENAI_API_KEY`     | OpenAI                     | [platform.openai.com](https://platform.openai.com/api-keys)               |
| `GROQ_API_KEY`       | Groq                       | [console.groq.com](https://console.groq.com) – kostenlose Stufe           |
| `OPENROUTER_API_KEY` | OpenRouter (für Refine)    | [openrouter.ai](https://openrouter.ai/keys)                               |
| `GEMINI_API_KEY`     | Google Gemini (für Refine) | [aistudio.google.com](https://aistudio.google.com/apikey)                 |

---

## Transkription

### Provider-Auswahl

| Variable                | Werte                                 | Default  | Beschreibung                                 |
| ----------------------- | ------------------------------------- | -------- | -------------------------------------------- |
| `PULSESCRIBE_MODE`      | `deepgram`, `openai`, `groq`, `local` | `openai` | Transkriptions-Provider                      |
| `PULSESCRIBE_MODEL`     | Provider-spezifisch                   | Auto     | Provider-Default überschreiben               |
| `PULSESCRIBE_LANGUAGE`  | `de`, `en`, `auto`, etc.              | `auto`   | Sprachcode (explizit verbessert Genauigkeit) |
| `PULSESCRIBE_STREAMING` | `true`, `false`                       | `true`   | WebSocket-Streaming für Deepgram             |

### Provider-spezifische Modelle

| Provider     | Modelle                                                    | Empfohlen           |
| ------------ | ---------------------------------------------------------- | ------------------- |
| **Deepgram** | `nova-3`, `nova-2`                                         | `nova-3`            |
| **OpenAI**   | `gpt-4o-transcribe`, `gpt-4o-mini-transcribe`, `whisper-1` | `gpt-4o-transcribe` |
| **Groq**     | `whisper-large-v3`, `distil-whisper-large-v3-en`           | `whisper-large-v3`  |
| **Lokal**    | `tiny`, `base`, `small`, `medium`, `large`, `turbo`        | `turbo`             |

---

## LLM-Nachbearbeitung (Refine)

Entfernt Füllwörter, korrigiert Grammatik, formatiert Absätze:

| Variable                      | Werte                                    | Default               | Beschreibung                   |
| ----------------------------- | ---------------------------------------- | --------------------- | ------------------------------ |
| `PULSESCRIBE_REFINE`          | `true`, `false`                          | `false`               | LLM-Nachbearbeitung aktivieren |
| `PULSESCRIBE_REFINE_PROVIDER` | `groq`, `openai`, `openrouter`, `gemini` | `openai`              | LLM-Provider                   |
| `PULSESCRIBE_REFINE_MODEL`    | Provider-spezifisch                      | `openai/gpt-oss-120b` | Modell für Refine              |

### Refine-Modelle nach Provider

| Provider       | Empfohlene Modelle                                         |
| -------------- | ---------------------------------------------------------- |
| **Groq**       | `llama-3.3-70b-versatile`, `mixtral-8x7b-32768`            |
| **OpenAI**     | `gpt-4o`, `gpt-4o-mini`                                    |
| **OpenRouter** | `openai/gpt-4o`, `anthropic/claude-3.5-sonnet`             |
| **Gemini**     | `gemini-3-flash-preview` (default), `gemini-3-pro-preview` |

### Gemini Thinking Level

Gemini 3 Modelle verwenden automatische `thinking_level`-Optimierung für die Transkript-Nachbearbeitung:

| Modell                               | Thinking Level | Hinweise                                          |
| ------------------------------------ | -------------- | ------------------------------------------------- |
| **Flash** (`gemini-3-flash-preview`) | `minimal`      | Schnellste Latenz, ideal für schnelle Korrekturen |
| **Pro** (`gemini-3-pro-preview`)     | `low`          | `minimal` wird von Pro nicht unterstützt          |

Dies wird automatisch basierend auf dem Modellnamen konfiguriert – keine manuelle Konfiguration nötig.

### Kontext-Awareness

| Variable                   | Werte                              | Beschreibung                                    |
| -------------------------- | ---------------------------------- | ----------------------------------------------- |
| `PULSESCRIBE_CONTEXT`      | `email`, `chat`, `code`, `default` | Kontext erzwingen (überschreibt Auto-Erkennung) |
| `PULSESCRIBE_APP_CONTEXTS` | JSON                               | Eigene App-Kontext-Zuordnungen                  |

**Auto-Erkennung:** PulseScribe erkennt die aktive App und passt den Schreibstil an:

- **email:** Mail, Outlook, Spark → Formell, vollständige Sätze
- **chat:** Slack, Discord, Messages → Locker, kurz und knapp
- **code:** VS Code, Cursor, Terminal → Technisch, Begriffe beibehalten

Beispiel für eigene Zuordnungen:

```bash
PULSESCRIBE_APP_CONTEXTS='{"MeineApp": "chat", "MeineIDE": "code"}'
```

---

## Hotkeys

### Dual-Hotkey-Modus (Empfohlen)

| Variable                    | Beschreibung                             | Beispiel            |
| --------------------------- | ---------------------------------------- | ------------------- |
| `PULSESCRIBE_TOGGLE_HOTKEY` | Drücken-zum-Starten, Drücken-zum-Stoppen | `f19`, `ctrl+alt+r` |
| `PULSESCRIBE_HOLD_HOTKEY`   | Halten-zum-Aufnehmen (Push-to-Talk)      | `fn`, `ctrl+win`    |

Beide Hotkeys können gleichzeitig aktiv sein.

### Legacy Single-Hotkey-Modus

| Variable                  | Beschreibung                                       |
| ------------------------- | -------------------------------------------------- |
| `PULSESCRIBE_HOTKEY`      | Einzelner Hotkey (überschrieben durch TOGGLE/HOLD) |
| `PULSESCRIBE_HOTKEY_MODE` | `toggle` oder `hold`                               |

### Unterstützte Hotkey-Formate

| Format          | Beispiele                                   |
| --------------- | ------------------------------------------- |
| Funktionstasten | `f1`, `f12`, `f19`                          |
| Einzeltasten    | `fn`, `capslock`, `space`, `tab`, `esc`     |
| Kombinationen   | `cmd+shift+r`, `ctrl+alt+space`, `ctrl+win` |

### Plattform-Defaults

| Plattform   | Toggle       | Hold               |
| ----------- | ------------ | ------------------ |
| **macOS**   | (keiner)     | `fn` (Globe-Taste) |
| **Windows** | `ctrl+alt+r` | `ctrl+win`         |

---

## UI & Verhalten

| Variable                        | Werte           | Default | Beschreibung                                         |
| ------------------------------- | --------------- | ------- | ---------------------------------------------------- |
| `PULSESCRIBE_OVERLAY`           | `true`, `false` | `true`  | Animiertes Overlay anzeigen                          |
| `PULSESCRIBE_DOCK_ICON`         | `true`, `false` | `true`  | Dock-Icon anzeigen (macOS)                           |
| `PULSESCRIBE_SHOW_RTF`          | `true`, `false` | `false` | Real-Time Factor nach Transkription anzeigen         |
| `PULSESCRIBE_CLIPBOARD_RESTORE` | `true`, `false` | `false` | Vorherige Zwischenablage nach Paste wiederherstellen |

---

## Lokaler Modus

Siehe [LOKALE_BACKENDS.md](LOKALE_BACKENDS.md) für detaillierte Konfiguration des lokalen Modus.

### Kurzreferenz

| Variable                    | Werte                                           | Default | Beschreibung      |
| --------------------------- | ----------------------------------------------- | ------- | ----------------- |
| `PULSESCRIBE_LOCAL_BACKEND` | `whisper`, `faster`, `mlx`, `lightning`, `auto` | `auto`  | Lokales Backend   |
| `PULSESCRIBE_LOCAL_MODEL`   | `tiny`...`large`, `turbo`                       | `turbo` | Modellgröße       |
| `PULSESCRIBE_DEVICE`        | `auto`, `mps`, `cpu`, `cuda`                    | `auto`  | Rechengerät       |
| `PULSESCRIBE_LOCAL_WARMUP`  | `true`, `false`, `auto`                         | `auto`  | Warmup beim Start |

---

## Dateipfade

| Pfad                                  | Beschreibung                        |
| ------------------------------------- | ----------------------------------- |
| `~/.pulsescribe/`                     | User-Konfigurationsverzeichnis      |
| `~/.pulsescribe/.env`                 | User-Einstellungen (Priorität 1)    |
| `~/.pulsescribe/logs/pulsescribe.log` | Haupt-Logdatei (rotierend, max 1MB) |
| `~/.pulsescribe/startup.log`          | Emergency Startup-Log               |
| `~/.pulsescribe/vocabulary.json`      | Custom Vocabulary                   |
| `~/.pulsescribe/prompts.toml`         | Custom Prompts                      |

---

## Custom Vocabulary

Erkennung von Fachbegriffen verbessern in `~/.pulsescribe/vocabulary.json`:

```json
{
  "keywords": ["Anthropic", "Claude", "Kubernetes", "OAuth", "GraphQL"]
}
```

**Unterstützt von:** Deepgram, Lokales Whisper
**Nicht unterstützt:** OpenAI API (Refine für Korrekturen nutzen)

---

## Custom Prompts

LLM-Prompts anpassen in `~/.pulsescribe/prompts.toml`:

```toml
[voice_commands]
instruction = """
Eigene Voice-Command-Anweisungen...
"""

[prompts.email]
prompt = """
Eigener Email-Kontext-Prompt...
"""

[prompts.chat]
prompt = """
Eigener Chat-Kontext-Prompt...
"""

[app_contexts]
"MeineApp" = "email"
MeineIDE = "code"
```

**Priorität:** CLI > ENV > Custom TOML > Hardcoded Defaults

---

## Beispielkonfigurationen

### Schnellstes Setup (Deepgram + Groq)

```bash
DEEPGRAM_API_KEY=dein_key
GROQ_API_KEY=dein_groq_key

PULSESCRIBE_MODE=deepgram
PULSESCRIBE_LANGUAGE=de
PULSESCRIBE_REFINE=true
PULSESCRIBE_REFINE_PROVIDER=groq
```

### Datenschutz-fokussiert (Nur Lokal)

```bash
PULSESCRIBE_MODE=local
PULSESCRIBE_LOCAL_BACKEND=mlx
PULSESCRIBE_LOCAL_MODEL=turbo
PULSESCRIBE_LANGUAGE=de
# Keine API-Keys nötig
```

### Höchste Qualität (OpenAI)

```bash
OPENAI_API_KEY=dein_key

PULSESCRIBE_MODE=openai
PULSESCRIBE_MODEL=gpt-4o-transcribe
PULSESCRIBE_REFINE=true
PULSESCRIBE_REFINE_PROVIDER=openai
```
