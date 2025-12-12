# whisper_go

[![Tests](https://github.com/KLIEBHAN/whisper_go/actions/workflows/test.yml/badge.svg)](https://github.com/KLIEBHAN/whisper_go/actions/workflows/test.yml)
[![codecov](https://codecov.io/gh/KLIEBHAN/whisper_go/graph/badge.svg)](https://codecov.io/gh/KLIEBHAN/whisper_go)

[🇺🇸 English Version](README.md)

Spracheingabe für macOS – inspiriert von [Wispr Flow](https://wisprflow.ai). Transkribiert Audio mit OpenAI Whisper über API, Deepgram, Groq oder lokal.

**Features:** Echtzeit-Streaming (Deepgram) · Mehrere Provider (OpenAI, Deepgram, Groq, lokal) · LLM-Nachbearbeitung · Kontext-Awareness · Custom Vocabulary · Raycast-Hotkeys · Live-Preview Overlay · Menübar-Feedback

> **Performance:** Ultra-Fast-Startup mit ~170ms bis Ready-Sound dank parallelem Mikrofon- und WebSocket-Init. Audio wird während der Aufnahme transkribiert – Ergebnis erscheint sofort nach dem Stoppen.

### Provider im Überblick

| Provider     | Latenz    | Methode   | Besonderheit                  |
| ------------ | --------- | --------- | ----------------------------- |
| **Deepgram** | ~300ms ⚡ | WebSocket | Echtzeit-Streaming, empfohlen |
| **Groq**     | ~1s       | REST      | Whisper auf LPU, sehr schnell |
| **OpenAI**   | ~2-3s     | REST      | GPT-4o, höchste Qualität      |
| **Lokal**    | ~5-10s    | Whisper   | Offline, keine API-Kosten (optional schnelleres Backend) |

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

### Empfohlene `.env` Konfiguration

Erstelle eine `.env`-Datei im Projektverzeichnis für dauerhafte Einstellungen:

```bash
# API-Keys
DEEPGRAM_API_KEY=...
GROQ_API_KEY=...
OPENAI_API_KEY=...
OPENROUTER_API_KEY=...

# Transkription
WHISPER_GO_MODE=deepgram
WHISPER_GO_LANGUAGE=de

# LLM-Nachbearbeitung
WHISPER_GO_REFINE=true
WHISPER_GO_REFINE_PROVIDER=groq
WHISPER_GO_REFINE_MODEL=openai/gpt-oss-120b
```

**Warum diese Einstellungen?**

| Einstellung                        | Begründung                                             |
| ---------------------------------- | ------------------------------------------------------ |
| `MODE=deepgram`                    | Schnellste Option (~300ms) durch WebSocket-Streaming   |
| `REFINE_PROVIDER=groq`             | Kostenlose/günstige LLM-Inferenz auf LPU-Hardware      |
| `REFINE_MODEL=openai/gpt-oss-120b` | Open-Source GPT-Alternative mit exzellenter Qualität   |
| `LANGUAGE=de`                      | Explizite Sprache verbessert Transkriptionsgenauigkeit |

> **Tipp:** Für systemweite Hotkeys siehe [Hotkey Integration](#hotkey-integration).

## CLI-Nutzung

Zwei Hauptfunktionen: Audiodateien transkribieren oder direkt vom Mikrofon aufnehmen.

### Audiodatei transkribieren

```bash
python transcribe.py audio.mp3                        # Standard (API-Modus)
python transcribe.py audio.mp3 --mode openai          # OpenAI GPT-4o Transcribe
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

| Option                                 | Beschreibung                                                                  |
| -------------------------------------- | ----------------------------------------------------------------------------- |
| `--mode openai\|local\|deepgram\|groq` | Transkriptions-Provider (default: `openai`)                                   |
| `--model NAME`                         | Modell (CLI > `WHISPER_GO_MODEL` env > Provider-Default)                      |
| `--record`, `-r`                       | Mikrofon-Aufnahme statt Datei                                                 |
| `--copy`, `-c`                         | Ergebnis in Zwischenablage                                                    |
| `--language CODE`                      | Sprachcode z.B. `de`, `en`                                                    |
| `--format FORMAT`                      | Output: `text`, `json`, `srt`, `vtt` (nur API-Modus)                          |
| `--no-streaming`                       | WebSocket-Streaming deaktivieren (nur Deepgram)                               |
| `--refine`                             | LLM-Nachbearbeitung aktivieren                                                |
| `--no-refine`                          | LLM-Nachbearbeitung deaktivieren (überschreibt env)                           |
| `--refine-model`                       | Modell für Nachbearbeitung (default: `gpt-5-nano`)                            |
| `--refine-provider`                    | LLM-Provider: `openai`, `openrouter`, `groq`                                  |
| `--context`                            | Kontext für Nachbearbeitung: `email`, `chat`, `code`, `default` (auto-detect) |

## Konfiguration

Alle Einstellungen können per Umgebungsvariable oder `.env`-Datei gesetzt werden. CLI-Argumente haben immer Vorrang.

### API-Keys

Je nach gewähltem Modus wird ein API-Key benötigt:

```bash
# OpenAI (für --mode openai und --refine mit openai)
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
# Transkriptions-Modus (openai, local, deepgram, groq)
export WHISPER_GO_MODE="deepgram"

# Transkriptions-Modell (überschreibt Provider-Default)
export WHISPER_GO_MODEL="nova-3"

# Device für lokales Whisper (auto, mps, cpu, cuda)
# Standard: auto → nutzt MPS auf Apple Silicon, sonst CPU/CUDA
export WHISPER_GO_DEVICE="auto"

# FP16 für lokales Whisper erzwingen (true/false)
# Standard: CPU/MPS → false (stabil), CUDA → true
export WHISPER_GO_FP16="false"

# Backend für lokales Whisper (whisper, faster, auto)
# whisper = openai-whisper (PyTorch, nutzt MPS/GPU)
# faster  = faster-whisper (CTranslate2, sehr schnell auf CPU)
# auto    = faster falls installiert, sonst whisper
export WHISPER_GO_LOCAL_BACKEND="whisper"

# Lokales Modell überschreiben (nur für lokalen Modus)
# Standard: Provider-Default (turbo)
# export WHISPER_GO_LOCAL_MODEL="turbo"

# Compute-Type für faster-whisper (optional)
# Default: int8 auf CPU, float16 auf CUDA
# export WHISPER_GO_LOCAL_COMPUTE_TYPE="int8"

# Faster-whisper Threads (optional)
# 0 Threads = auto (alle Kerne)
# export WHISPER_GO_LOCAL_CPU_THREADS=0
# export WHISPER_GO_LOCAL_NUM_WORKERS=1

# Faster-whisper Optionen (optional)
# Standard bei faster: without_timestamps=true, vad_filter=false
# export WHISPER_GO_LOCAL_WITHOUT_TIMESTAMPS="true"
# export WHISPER_GO_LOCAL_VAD_FILTER="false"

# Optional: schnelleres lokales Decoding (mehr Speed, leicht weniger Robustheit)
# Standard: true bei faster-whisper, false bei openai-whisper
# export WHISPER_GO_LOCAL_FAST="true"  # setzt beam_size=1, best_of=1, temperature=0.0
# Feintuning:
# export WHISPER_GO_LOCAL_BEAM_SIZE=1
# export WHISPER_GO_LOCAL_BEST_OF=1
# export WHISPER_GO_LOCAL_TEMPERATURE=0.0

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

### Real-Time Audio Feedback

Das Overlay reagiert in Echtzeit auf die Stimme mit einer dynamischen Schallwellen-Visualisierung:

- **Listening (🌸 Rosa):** System wartet auf Spracheingabe.
- **Recording (🔴 Rot):** Sprache erkannt, Aufnahme läuft. Die Balken visualisieren die Lautstärke.
- **Transcribing (🟠 Orange):** Aufnahme beendet, Text wird verarbeitet.

Dank integrierter Voice Activity Detection (VAD) schaltet der Status sofort um, sobald gesprochen wird.

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
- Menübar-Status (🎤 🔴 ⏳ ✅ ❌) - via `ui/menubar.py`
- Overlay mit Animationen - via `ui/overlay.py`
- Auto-Paste

```bash
# Manueller Start
python whisper_daemon.py

# Mit CLI-Optionen
python whisper_daemon.py --hotkey cmd+shift+r --debug

# Als Login Item (Doppelklick oder zu Anmeldeobjekten hinzufügen)
open start_daemon.command
```

> **Für Toggle-Hotkeys ist keine Accessibility-Berechtigung nötig.** QuickMacHotKey nutzt die native Carbon-API (`RegisterEventHotKey`).  
> **Hold‑Mode nutzt pynput und benötigt Bedienungshilfen** unter macOS.

### Konfiguration

In `.env` oder als Umgebungsvariable:

```bash
# Hotkeys (default: Fn/Globe als Hold)
#
# Optional: Toggle + Hold parallel nutzen.
# Wenn gesetzt, überschreibt dies WHISPER_GO_HOTKEY / WHISPER_GO_HOTKEY_MODE.
#
# Empfohlener Default: Fn/Globe als Push‑to‑Talk (Hold).
# WHISPER_GO_HOLD_HOTKEY=fn
# Optional: separaten Toggle‑Hotkey ergänzen (z.B. F19).
# WHISPER_GO_TOGGLE_HOTKEY=f19
#
# Legacy (Single Hotkey):
WHISPER_GO_HOTKEY=fn
WHISPER_GO_HOTKEY_MODE=hold

# Dock-Icon (default: true) – auf false setzen für Menubar-only Modus
WHISPER_GO_DOCK_ICON=true
```

**Unterstützte Hotkeys:**

| Format            | Beispiel              |
| ----------------- | --------------------- |
| Funktionstasten   | `f19`, `f1`, `f12`    |
| Einzeltaste       | `fn`, `capslock`, `space`, `tab`, `esc` |
| Tastenkombination | `cmd+shift+r`         |

**Empfohlene Hotkey‑Konfiguration (macOS):**

- **Fn/Globe als Hold‑to‑Record:** `WHISPER_GO_HOLD_HOTKEY=fn`.  
  Sehr schneller Push‑to‑Talk Workflow mit einer Taste. Benötigt Bedienungshilfen/Input‑Monitoring.
- **CapsLock‑Alternative:** CapsLock geht direkt als Toggle‑Hotkey, kollidiert aber oft mit der Großschreibung.  
  Für einen konfliktfreien „Ein‑Tasten‑Toggle“ CapsLock per **Karabiner‑Elements** auf `F19` mappen und `WHISPER_GO_TOGGLE_HOTKEY=f19` setzen.

### Nutzung

**Hold‑Mode (Default / Push‑to‑Talk):**

- Fn/Globe gedrückt halten → Aufnahme läuft solange gehalten
- Fn/Globe loslassen → Transkript wird eingefügt

**Toggle‑Mode (Optional, z.B. mit F19):**

- F19 drücken → Aufnahme startet
- F19 nochmal drücken → Transkript wird eingefügt

### Visuelles Feedback

Das Overlay zeigt den aktuellen Status durch Farben und Animationen an:

| Status           | Farbe      | Animation | Bedeutung                           |
| ---------------- | ---------- | --------- | ----------------------------------- |
| **Listening**    | 🌸 Rosa    | Atmen     | Hotkey gedrückt, wartet auf Sprache |
| **Recording**    | 🔴 Rot     | Wellen    | Sprache erkannt, Aufnahme läuft     |
| **Transcribing** | 🟠 Orange  | Laden     | Finalisierung der Transkription     |
| **Refining**     | 💜 Violett | Pulsieren | LLM-Nachbearbeitung läuft           |
| **Done**         | ✅ Grün    | Hüpfen    | Fertig, Text eingefügt              |
| **Error**        | ❌ Rot     | Blinken   | Fehler aufgetreten                  |

Beides ist integriert und startet automatisch mit dem Daemon.

## Provider-Vergleich

| Modus      | Provider | Methode   | Latenz    | Besonderheit                        |
| ---------- | -------- | --------- | --------- | ----------------------------------- |
| `deepgram` | Deepgram | WebSocket | ~300ms ⚡ | Echtzeit-Streaming (empfohlen)      |
| `groq`     | Groq     | REST      | ~1s       | Whisper auf LPU, sehr schnell       |
| `openai`   | OpenAI   | REST      | ~2-3s     | GPT-4o Transcribe, höchste Qualität |
| `local`    | Whisper  | Lokal     | ~5-10s    | Offline, keine API-Kosten (optional schnelleres Backend) |

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

Der lokale Modus unterstützt jetzt zwei Backends:

- **`whisper` (Standard):** openai‑whisper auf PyTorch. Nutzt auf M‑Series Macs automatisch MPS (`WHISPER_GO_DEVICE=auto`). Beste Kompatibilität/Qualität.
- **`faster`:** faster‑whisper (CTranslate2). Sehr schnell auf CPU (oft 2–4× schneller) und mit weniger Speicherbedarf. Default‑`compute_type` ist `int8` auf CPU und `float16` auf CUDA. Aktivieren mit `WHISPER_GO_LOCAL_BACKEND=faster`.

Hinweise:

- Modellname `turbo` wird bei faster‑whisper zu `large-v3-turbo` gemappt.
- Für maximale Geschwindigkeit (mit leicht weniger Robustheit) `WHISPER_GO_LOCAL_FAST=true` oder kleinere `BEAM_SIZE`/`BEST_OF` wählen.
- Für längere Aufnahmen unter `faster` kannst du Durchsatz via `WHISPER_GO_LOCAL_CPU_THREADS` und `WHISPER_GO_LOCAL_NUM_WORKERS` tunen.

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

| Problem                                    | Lösung                                                                     |
| ------------------------------------------ | -------------------------------------------------------------------------- |
| Modul nicht installiert                    | `pip install -r requirements.txt`                                          |
| API-Key fehlt                              | `export DEEPGRAM_API_KEY="..."` (oder OPENAI/GROQ)                         |
| Mikrofon geht nicht (macOS)                | `brew install portaudio && pip install --force-reinstall sounddevice`      |
| Mikrofon-Berechtigung                      | Zugriff erlauben unter Systemeinstellungen → Datenschutz → Mikrofon        |
| ffmpeg fehlt                               | `brew install ffmpeg` (macOS) oder `sudo apt install ffmpeg` (Ubuntu)      |
| Transkription langsam                      | Wechsel zu `--mode groq`/`deepgram` oder lokal `WHISPER_GO_LOCAL_BACKEND=faster`, `WHISPER_GO_LOCAL_FAST=true` bzw. kleineres Modell |
| Daemon startet nicht                       | Prüfe `~/.whisper_go/startup.log` für Emergency-Logs                       |
| Auto-Paste funktioniert nicht (App Bundle) | Siehe [Auto-Paste Troubleshooting](#auto-paste-troubleshooting-app-bundle) |

### Auto-Paste Troubleshooting (App Bundle)

Wenn Auto-Paste in `WhisperGo.app` nicht funktioniert (Text wird kopiert, aber nicht eingefügt):

**Symptom:** Log zeigt `AXIsProcessTrusted = False` obwohl App in Bedienungshilfen aktiviert ist.

**Ursache:** Unsignierte PyInstaller-Bundles ändern bei jedem Neubuild ihren Hash. macOS erkennt die "neue" App nicht als berechtigt.

**Lösung:**

1. Systemeinstellungen → Datenschutz & Sicherheit → Bedienungshilfen
2. `WhisperGo` **entfernen** (Minus-Button)
3. `WhisperGo` **neu hinzufügen** (Plus-Button oder App per Drag & Drop)

> **Tipp:** Nach jedem `pyinstaller build_app.spec` muss dieser Schritt wiederholt werden, solange die App nicht signiert ist.

### Log-Dateien

Logs werden in `~/.whisper_go/logs/` gespeichert:

```bash
# Haupt-Log
~/.whisper_go/logs/whisper_go.log

# Emergency Startup-Log (falls Daemon nicht startet)
~/.whisper_go/startup.log
```

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

### macOS App Bundle erstellen

Um eine eigenständige `WhisperGo.app` zu erstellen:

```bash
# PyInstaller installieren (falls noch nicht vorhanden)
pip install pyinstaller

# App bauen
pyinstaller build_app.spec

# Output: dist/WhisperGo.app
```

**Optional: Code-Signierung für stabile Accessibility-Berechtigungen**

```bash
codesign --force --deep --sign - dist/WhisperGo.app
```

> **Hinweis:** Ohne Signierung muss die App nach jedem Neubuild in Systemeinstellungen → Datenschutz & Sicherheit → Bedienungshilfen neu autorisiert werden. Siehe [Auto-Paste Troubleshooting](#auto-paste-troubleshooting-app-bundle).
