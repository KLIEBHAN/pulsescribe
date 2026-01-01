# PulseScribe

[![Tests](https://github.com/KLIEBHAN/pulsescribe/actions/workflows/test.yml/badge.svg)](https://github.com/KLIEBHAN/pulsescribe/actions/workflows/test.yml)
[![codecov](https://codecov.io/gh/KLIEBHAN/pulsescribe/graph/badge.svg)](https://codecov.io/gh/KLIEBHAN/pulsescribe)

[🇺🇸 English Version](README.md)

## Inhaltsverzeichnis

- [Schnellstart](#schnellstart)
- [CLI-Nutzung](#cli-nutzung)
- [Konfiguration](#konfiguration)
- [Erweiterte Features](#erweiterte-features)
- [Hotkey Integration](#hotkey-integration)
- [Provider-Vergleich](#provider-vergleich)
- [Modell-Referenz](#modell-referenz)
- [Troubleshooting](#troubleshooting)
- [Development](#development)
  - [Windows-Unterstützung](#windows-unterstützung)
  - [macOS App Bundle erstellen](#macos-app-bundle-erstellen)
- [Weitere Dokumentation](#weitere-dokumentation)

---

Spracheingabe für macOS und Windows – inspiriert von [Wispr Flow](https://wisprflow.ai). Transkribiert Audio mit OpenAI Whisper über API, Deepgram, Groq oder lokal.

**Features:** Echtzeit-Streaming (Deepgram) · Mehrere Provider (OpenAI, Deepgram, Groq, lokal inkl. MLX/Metal auf Apple Silicon) · LLM-Nachbearbeitung · Kontext-Awareness · Custom Vocabulary · Live-Preview Overlay · Menübar-Feedback

> **Performance:** Ultra-Fast-Startup mit ~170ms bis Ready-Sound dank parallelem Mikrofon- und WebSocket-Init. Audio wird während der Aufnahme transkribiert – Ergebnis erscheint sofort nach dem Stoppen.

<p align="center">
  <img src="docs/assets/demo.gif" alt="PulseScribe Demo" width="700">
</p>

### Provider im Überblick

| Provider     | Latenz    | Methode   | Besonderheit                                            |
| ------------ | --------- | --------- | ------------------------------------------------------- |
| **Deepgram** | ~300ms ⚡ | WebSocket | Echtzeit-Streaming, empfohlen                           |
| **Groq**     | ~1s       | REST      | Whisper auf LPU, sehr schnell                           |
| **OpenAI**   | ~2-3s     | REST      | GPT-4o, höchste Qualität                                |
| **Lokal**    | variiert  | Whisper   | Offline, keine API-Kosten (MLX/Metal auf Apple Silicon) |

### Begriffe

| Begriff | Bedeutung |
|---------|-----------|
| **LPU** | Language Processing Unit – Groqs Custom-Chip für ultra-schnelle Inferenz |
| **VAD** | Voice Activity Detection – automatische Sprach-Start/Stop-Erkennung |
| **RTF** | Real-Time Factor – Verarbeitungszeit / Audiodauer (niedriger = schneller) |
| **Refine** | LLM-Nachbearbeitung zur Bereinigung von Transkriptionen |
| **Streaming** | WebSocket-basierte Echtzeit-Transkription während der Aufnahme |

## Schnellstart

In unter 2 Minuten einsatzbereit:

```bash
# 1. Repository klonen
git clone https://github.com/KLIEBHAN/pulsescribe.git && cd pulsescribe

# 2. Dependencies installieren
pip install -r requirements.txt

# 3. API-Key setzen (Deepgram: 200$ Startguthaben)
export DEEPGRAM_API_KEY="dein_key"

# 4. Erste Aufnahme
python transcribe.py --record --copy --mode deepgram
```

**Windows-Support:**  
Unter Windows starten Sie den Daemon via `python pulsescribe_windows.py`. Unterstützt Deepgram (Streaming), Groq, OpenAI und Local (REST).

### Empfohlene `.env` Konfiguration

PulseScribe lädt Einstellungen aus `~/.pulsescribe/.env` (empfohlen; wird von Settings-UI und Daemon genutzt).  
Für Development wird zusätzlich eine lokale `.env` im Projektverzeichnis unterstützt.

```bash
# Empfohlen (funktioniert für Daemon / App Bundle)
cp .env.example ~/.pulsescribe/.env
```

Beispiel `~/.pulsescribe/.env`:

```bash
# API-Keys
DEEPGRAM_API_KEY=...
GROQ_API_KEY=...
OPENAI_API_KEY=...
OPENROUTER_API_KEY=...

# Transkription
PULSESCRIBE_MODE=deepgram
PULSESCRIBE_LANGUAGE=de

# LLM-Nachbearbeitung
PULSESCRIBE_REFINE=true
PULSESCRIBE_REFINE_PROVIDER=groq
PULSESCRIBE_REFINE_MODEL=openai/gpt-oss-120b
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
| `--model NAME`                         | Modell (CLI > `PULSESCRIBE_MODEL` env > Provider-Default)                     |
| `--record`, `-r`                       | Mikrofon-Aufnahme statt Datei                                                 |
| `--copy`, `-c`                         | Ergebnis in Zwischenablage                                                    |
| `--language CODE`                      | Sprachcode z.B. `de`, `en`                                                    |
| `--format FORMAT`                      | Output: `text`, `json`, `srt`, `vtt` (nur API-Modus)                          |
| `--refine`                             | LLM-Nachbearbeitung aktivieren                                                |
| `--no-refine`                          | LLM-Nachbearbeitung deaktivieren (überschreibt env)                           |
| `--refine-model`                       | Modell für Nachbearbeitung (default: `openai/gpt-oss-120b`)                   |
| `--refine-provider`                    | LLM-Provider: `groq`, `openai` oder `openrouter`                              |
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

# Gemini (für --refine mit gemini)
export GEMINI_API_KEY="..."
```

### Standard-Einstellungen

```bash
# Transkriptions-Modus (openai, local, deepgram, groq)
export PULSESCRIBE_MODE="deepgram"

# Transkriptions-Modell (überschreibt Provider-Default)
export PULSESCRIBE_MODEL="nova-3"

# Device für lokales Whisper (auto, mps, cpu, cuda)
# Standard: auto → nutzt MPS auf Apple Silicon, sonst CPU/CUDA
export PULSESCRIBE_DEVICE="auto"

# FP16 für lokales Whisper erzwingen (true/false)
# Standard: CPU/MPS → false (stabil), CUDA → true
export PULSESCRIBE_FP16="false"

# Backend für lokales Whisper (whisper, faster, mlx, auto)
# whisper = openai-whisper (PyTorch, nutzt MPS/GPU)
# faster  = faster-whisper (CTranslate2, sehr schnell auf CPU)
# mlx     = mlx-whisper (MLX/Metal, Apple Silicon, optional)
# auto    = faster falls installiert, sonst whisper
export PULSESCRIBE_LOCAL_BACKEND="whisper"

# Lokales Modell überschreiben (nur für lokalen Modus)
# Standard: Provider-Default (turbo)
# export PULSESCRIBE_LOCAL_MODEL="turbo"

# Compute-Type für faster-whisper (optional)
# Default: int8 auf CPU, float16 auf CUDA
# export PULSESCRIBE_LOCAL_COMPUTE_TYPE="int8"

# Faster-whisper Threads (optional)
# 0 Threads = auto (alle Kerne)
# export PULSESCRIBE_LOCAL_CPU_THREADS=0
# export PULSESCRIBE_LOCAL_NUM_WORKERS=1

# Faster-whisper Optionen (optional)
# Standard bei faster: without_timestamps=true, vad_filter=false
# export PULSESCRIBE_LOCAL_WITHOUT_TIMESTAMPS="true"
# export PULSESCRIBE_LOCAL_VAD_FILTER="false"

# Optional: schnelleres lokales Decoding (mehr Speed, leicht weniger Robustheit)
# Standard: true bei faster-whisper, false bei openai-whisper
# export PULSESCRIBE_LOCAL_FAST="true"  # setzt beam_size=1, best_of=1, temperature=0.0
# Feintuning:
# export PULSESCRIBE_LOCAL_BEAM_SIZE=1
# export PULSESCRIBE_LOCAL_BEST_OF=1
# export PULSESCRIBE_LOCAL_TEMPERATURE=0.0

# Optional: Local Warmup (reduziert "cold start" beim ersten lokalen Call)
# Default: auto (Warmup nur bei openai-whisper auf MPS). Werte: true/false (nicht gesetzt = auto)
# export PULSESCRIBE_LOCAL_WARMUP="true"

# WebSocket-Streaming für Deepgram (default: true)
export PULSESCRIBE_STREAMING="true"

# LLM-Nachbearbeitung
export PULSESCRIBE_REFINE="true"
export PULSESCRIBE_REFINE_MODEL="openai/gpt-oss-120b"
export PULSESCRIBE_REFINE_PROVIDER="groq"  # oder openai, openrouter
```

### System-Abhängigkeiten

Für bestimmte Modi werden zusätzliche Tools benötigt:

```bash
# Lokaler Modus (Datei-Transkription)
# Benötigt für `PULSESCRIBE_LOCAL_BACKEND=whisper` und `mlx`, wenn Audiodateien transkribiert werden.
brew install ffmpeg          # macOS
sudo apt install ffmpeg      # Ubuntu/Debian

# Mikrofon-Aufnahme (macOS)
brew install portaudio
```

## Erweiterte Features

Über die Basis-Transkription hinaus bietet pulsescribe intelligente Nachbearbeitung und Anpassung.

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
export PULSESCRIBE_APP_CONTEXTS='{"MyApp": "chat"}'
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

Eigene Begriffe für bessere Erkennung in `~/.pulsescribe/vocabulary.json`:

```json
{
  "keywords": ["Anthropic", "Claude", "Kubernetes", "OAuth"]
}
```

Unterstützt von Deepgram und lokalem Whisper. Die OpenAI API unterstützt kein Custom Vocabulary – dort hilft die LLM-Nachbearbeitung.

## Hotkey Integration

Für systemweite Spracheingabe per Hotkey – der Hauptanwendungsfall von pulsescribe.

### Unified Daemon (empfohlen)

Der `pulsescribe_daemon.py` kombiniert alle Komponenten in einem Prozess:

- Hotkey-Listener (QuickMacHotKey)
- Microphone Recording + Deepgram Streaming
- Menübar-Status (🎤 🔴 ⏳ ✅ ❌) - via `ui/menubar.py`
- Overlay mit Animationen - via `ui/overlay.py`
- Auto-Paste

```bash
# Manueller Start
python pulsescribe_daemon.py

# Mit CLI-Optionen
python pulsescribe_daemon.py --hotkey cmd+shift+r --debug

# Als Login Item (Doppelklick oder zu Anmeldeobjekten hinzufügen)
open start_daemon.command
```

> **Für Toggle-Hotkeys ist keine Accessibility-Berechtigung nötig.** QuickMacHotKey nutzt die native Carbon-API (`RegisterEventHotKey`).  
> **Hold‑Mode nutzt Quartz Event Taps und benötigt Eingabemonitoring** unter macOS.

### Settings UI (Menübar)

Über das Menübar-Icon → **Settings...** kannst du Provider-Keys, Modus, Local Backend/Modell und erweiterte Local-Performance-Settings (Device, Warmup, Fast-Decoding, faster-whisper Compute/Threads, etc.) konfigurieren.  
Einstellungen werden in `~/.pulsescribe/.env` gespeichert und live übernommen (Hotkey-Änderungen greifen sofort).

### Konfiguration

In `.env` oder als Umgebungsvariable:

```bash
# Hotkeys (default: Fn/Globe als Hold)
#
# Optional: Toggle + Hold parallel nutzen.
# Wenn gesetzt, überschreibt dies PULSESCRIBE_HOTKEY / PULSESCRIBE_HOTKEY_MODE.
#
# Empfohlener Default: Fn/Globe als Push‑to‑Talk (Hold).
# PULSESCRIBE_HOLD_HOTKEY=fn
# Optional: separaten Toggle‑Hotkey ergänzen (z.B. F19).
# PULSESCRIBE_TOGGLE_HOTKEY=f19
#
# Legacy (Single Hotkey):
PULSESCRIBE_HOTKEY=fn
PULSESCRIBE_HOTKEY_MODE=hold

# Dock-Icon (default: true) – auf false setzen für Menubar-only Modus
PULSESCRIBE_DOCK_ICON=true
```

**Unterstützte Hotkeys:**

| Format            | Beispiel                                |
| ----------------- | --------------------------------------- |
| Funktionstasten   | `f19`, `f1`, `f12`                      |
| Einzeltaste       | `fn`, `capslock`, `space`, `tab`, `esc` |
| Tastenkombination | `cmd+shift+r`                           |

**Empfohlene Hotkey‑Konfiguration (macOS):**

- **Fn/Globe als Hold‑to‑Record:** `PULSESCRIBE_HOLD_HOTKEY=fn`.  
  Sehr schneller Push‑to‑Talk Workflow mit einer Taste. Benötigt Bedienungshilfen/Input‑Monitoring.
- **CapsLock‑Alternative:** CapsLock geht direkt als Toggle‑Hotkey, kollidiert aber oft mit der Großschreibung.  
  Für einen konfliktfreien „Ein‑Tasten‑Toggle“ CapsLock per **Karabiner‑Elements** auf `F19` mappen und `PULSESCRIBE_TOGGLE_HOTKEY=f19` setzen.

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

### Windows-Unterstützung

PulseScribe enthält Windows-Unterstützung mit einem dedizierten Daemon:

```bash
# Dependencies installieren (venv empfohlen)
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt

# Optional: GPU-beschleunigtes Overlay (empfohlen)
pip install PySide6

# Windows-Daemon starten
python pulsescribe_windows.py

# Oder die Batch-Datei nutzen (auch für Autostart geeignet)
start_daemon.bat

# Mit Optionen (Defaults: Toggle=Ctrl+Alt+R, Hold=Ctrl+Win)
python pulsescribe_windows.py --debug                      # Defaults nutzen
python pulsescribe_windows.py --toggle-hotkey "f19" --debug # Nur Toggle anpassen
python pulsescribe_windows.py --hold-hotkey "ctrl+alt+space" --refine # Nur Hold anpassen
```

**Features:**
- System-Tray-Icon mit farbkodiertem Status (grau/orange/rot/gelb/cyan/grün)
- Globale Hotkeys via pynput (Toggle und/oder Hold-Mode, unterstützt F1-F24)
- Audio-Aufnahme via sounddevice mit Pre-Warming für schnellen Start
- Deepgram WebSocket-Streaming (~300ms Latenz) oder REST-API-Fallback
- Auto-Paste via `Ctrl+V`-Simulation
- LLM-Nachbearbeitung (Groq, OpenAI, OpenRouter)
- App-Kontext-Erkennung (Outlook → email, VS Code → code, Discord → chat)
- Windows-System-Sounds (DeviceConnect, DeviceDisconnect, Notification.SMS)
- GPU-beschleunigtes Overlay mit PySide6 (fällt auf Tkinter zurück wenn nicht installiert)
- Echtzeit-Wellenform-Visualisierung mit Interim-Text-Vorschau

**Autostart:**
1. `Win+R` drücken, `shell:startup` eingeben
2. Verknüpfung zu `start_daemon.bat` im geöffneten Ordner erstellen

**CLI-Optionen:**

| Flag | Beschreibung |
|------|--------------|
| `--toggle-hotkey` | Toggle-Hotkey (drücken-sprechen-drücken), Default: `ctrl+alt+r` |
| `--hold-hotkey` | Hold-Hotkey (halten-sprechen-loslassen), Default: `ctrl+win` |
| `--no-paste` | Auto-Paste deaktivieren, nur in Zwischenablage kopieren |
| `--no-streaming` | REST-API statt WebSocket-Streaming verwenden |
| `--no-overlay` | Animiertes Overlay deaktivieren |
| `--refine` | LLM-Nachbearbeitung aktivieren |
| `--refine-provider` | LLM-Provider: `groq`, `openai`, `openrouter` |
| `--refine-model` | LLM-Modell überschreiben |
| `--context` | Kontext erzwingen: `email`, `chat`, `code`, `default` |
| `--debug` | Debug-Logging aktivieren |

**Konfiguration:** Gleiche `.env`-Datei wie macOS (`~/.pulsescribe/.env` oder Projektverzeichnis):

```bash
DEEPGRAM_API_KEY=dein_key
PULSESCRIBE_LANGUAGE=de

# Hotkeys (konsistent mit macOS)
# Defaults: Toggle=Ctrl+Alt+R, Hold=Ctrl+Win
# Mit Umgebungsvariablen überschreiben:
# PULSESCRIBE_TOGGLE_HOTKEY=f19
# PULSESCRIBE_HOLD_HOTKEY=ctrl+alt+space
# Beide Hotkeys sind standardmäßig gleichzeitig aktiv

# Optional: Streaming deaktivieren (default: true)
# PULSESCRIBE_STREAMING=false

# Optional: Overlay deaktivieren (default: true)
# PULSESCRIBE_OVERLAY=false

# Optional: LLM-Refine
PULSESCRIBE_REFINE=true
PULSESCRIBE_REFINE_PROVIDER=groq
GROQ_API_KEY=dein_groq_key
```

**Windows-EXE erstellen:**

```bash
pip install pyinstaller
pyinstaller build_windows.spec --clean
# Output: dist/PulseScribe.exe
```

## Provider-Vergleich

| Modus      | Provider | Methode   | Latenz    | Besonderheit                                       |
| ---------- | -------- | --------- | --------- | -------------------------------------------------- |
| `deepgram` | Deepgram | WebSocket | ~300ms ⚡ | Echtzeit-Streaming (empfohlen)                     |
| `groq`     | Groq     | REST      | ~1s       | Whisper auf LPU, sehr schnell                      |
| `openai`   | OpenAI   | REST      | ~2-3s     | GPT-4o Transcribe, höchste Qualität                |
| `local`    | Whisper  | Lokal     | variiert  | Offline, keine API-Kosten (Whisper / Faster / MLX) |

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
# Unified Daemon nutzt Streaming standardmäßig.
# REST-Fallback (nur Daemon):
PULSESCRIBE_STREAMING=false
```

### Groq-Modelle

| Modell                       | Beschreibung                        |
| ---------------------------- | ----------------------------------- |
| `whisper-large-v3`           | Whisper Large v3, ~300x Echtzeit ⭐ |
| `distil-whisper-large-v3-en` | Nur Englisch, noch schneller        |

Groq nutzt LPU-Chips (Language Processing Units) für besonders schnelle Inferenz.

### Lokale Modelle

Der lokale Modus unterstützt jetzt drei Backends:

- **`whisper` (Standard):** openai‑whisper auf PyTorch. Nutzt auf M‑Series Macs automatisch MPS (`PULSESCRIBE_DEVICE=auto`). Beste Kompatibilität/Qualität.
- **`faster`:** faster‑whisper (CTranslate2). Sehr schnell auf CPU und mit weniger Speicherbedarf. Unter macOS läuft es auf CPU (kein MPS/Metal). Default‑`compute_type` ist `int8` auf CPU und `float16` auf CUDA. Aktivieren mit `PULSESCRIBE_LOCAL_BACKEND=faster`.
- **`mlx`:** mlx‑whisper (MLX/Metal). GPU‑beschleunigtes lokales Backend auf Apple Silicon. Installieren mit `pip install mlx-whisper` und aktivieren via `PULSESCRIBE_LOCAL_BACKEND=mlx`.

Hinweise:

- Modellname `turbo` wird bei faster‑whisper zu `large-v3-turbo` gemappt.
- Für maximale Geschwindigkeit (mit leicht weniger Robustheit) `PULSESCRIBE_LOCAL_FAST=true` oder kleinere `PULSESCRIBE_LOCAL_BEAM_SIZE`/`PULSESCRIBE_LOCAL_BEST_OF` wählen.
- Für längere Aufnahmen unter `faster` kannst du Durchsatz via `PULSESCRIBE_LOCAL_CPU_THREADS` und `PULSESCRIBE_LOCAL_NUM_WORKERS` tunen.
- Für `mlx` wird `PULSESCRIBE_LOCAL_BEAM_SIZE` ignoriert (Beam Search ist in mlx‑whisper nicht implementiert).

#### Schnellstart (Offline‑Diktat)

Apple Silicon (empfohlenes lokales Backend):

```bash
pip install mlx-whisper
export PULSESCRIBE_MODE=local
export PULSESCRIBE_LOCAL_BACKEND=mlx
export PULSESCRIBE_LOCAL_MODEL=large   # oder: turbo
export PULSESCRIBE_LANGUAGE=de         # optional
python pulsescribe_daemon.py --debug
```

#### Apple Silicon: MLX Modellnamen

Mit `PULSESCRIBE_LOCAL_BACKEND=mlx` unterstützt `PULSESCRIBE_LOCAL_MODEL` sowohl kurze Namen als auch vollständige Hugging‑Face Repo‑IDs:

- `large` → `mlx-community/whisper-large-v3-mlx`
- `turbo` → `mlx-community/whisper-large-v3-turbo`
- `medium` → `mlx-community/whisper-medium`
- `small` → `mlx-community/whisper-small-mlx`
- `base` → `mlx-community/whisper-base-mlx`
- `tiny` → `mlx-community/whisper-tiny`

Wenn du vorher `whisper-large-v3` probiert hast und eine 404 bekommst, nutze `large`/`large-v3` oder die volle Repo‑ID `mlx-community/whisper-large-v3-mlx`.

#### Warmup / cold start

Wenn der Daemon im `local`‑Modus läuft, wird das lokale Modell im Hintergrund vorab geladen, um die erste Latenz zu reduzieren.  
Optional kannst du zusätzlich ein Warmup via `PULSESCRIBE_LOCAL_WARMUP=true` aktivieren (am nützlichsten für `whisper` auf MPS). Wenn du währenddessen schon aufnimmst, wird nichts „verworfen“ — die erste lokale Transkription kann nur trotzdem noch etwas Cold‑Start‑Overhead enthalten.

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

| Problem                                    | Lösung                                                                                                                                                                  |
| ------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Modul nicht installiert                    | `pip install -r requirements.txt`                                                                                                                                       |
| API-Key fehlt                              | `export DEEPGRAM_API_KEY="..."` (oder OPENAI/GROQ)                                                                                                                      |
| Mikrofon geht nicht (macOS)                | `brew install portaudio && pip install --force-reinstall sounddevice`                                                                                                   |
| Mikrofon-Berechtigung                      | Zugriff erlauben unter Systemeinstellungen → Datenschutz → Mikrofon                                                                                                     |
| ffmpeg fehlt                               | `brew install ffmpeg` (macOS) oder `sudo apt install ffmpeg` (Ubuntu) — nötig für lokale Datei-Transkription (`whisper`/`mlx`)                                          |
| MLX Modell-Download 404                    | `PULSESCRIBE_LOCAL_MODEL=large` oder volle Repo‑ID nutzen (z.B. `mlx-community/whisper-large-v3-mlx`)                                                                   |
| Beam Search nicht implementiert (mlx)      | `PULSESCRIBE_LOCAL_BEAM_SIZE` entfernen (wird bei `mlx` ignoriert) oder Backend wechseln                                                                                |
| Transkription langsam                      | Wechsel zu `--mode groq`/`deepgram` oder lokal `PULSESCRIBE_LOCAL_BACKEND=mlx` (Apple Silicon) / `faster` (CPU) und `PULSESCRIBE_LOCAL_FAST=true` bzw. kleineres Modell |
| Deepgram-Streaming schneidet letztes Wort ab | Auf neueste Version updaten; Streaming leert die Audio-Queue vor dem Shutdown. Workaround: `PULSESCRIBE_STREAMING=false` (REST)                                        |
| Daemon startet nicht                       | Prüfe `~/.pulsescribe/startup.log` für Emergency-Logs                                                                                                                   |
| Auto-Paste funktioniert nicht (App Bundle) | Siehe [Auto-Paste Troubleshooting](#auto-paste-troubleshooting-app-bundle)                                                                                              |

### Auto-Paste Troubleshooting (App Bundle)

Wenn Auto-Paste in `PulseScribe.app` nicht funktioniert (Text wird kopiert, aber nicht eingefügt):

**Zwischenablage:** PulseScribe stellt nach einem erfolgreichen Paste deine vorherige Zwischenablage wieder her. Wenn Paste fehlschlägt, bleibt das Transkript in der Zwischenablage, damit du manuell `CMD+V` nutzen kannst.

**Symptom:** Log zeigt `AXIsProcessTrusted = False` obwohl App in Bedienungshilfen aktiviert ist.

**Ursache:** Unsignierte PyInstaller-Bundles ändern bei jedem Neubuild ihren Hash. macOS erkennt die "neue" App nicht als berechtigt.

**Lösung:**

1. Systemeinstellungen → Datenschutz & Sicherheit → Bedienungshilfen
2. `PulseScribe` **entfernen** (Minus-Button)
3. `PulseScribe` **neu hinzufügen** (Plus-Button oder App per Drag & Drop)

> **Tipp:** Nach jedem `pyinstaller build_app.spec` muss dieser Schritt wiederholt werden, solange die App nicht signiert ist.

### Log-Dateien

Logs werden in `~/.pulsescribe/logs/` gespeichert:

```bash
# Haupt-Log
~/.pulsescribe/logs/pulsescribe.log

# Emergency Startup-Log (falls Daemon nicht startet)
~/.pulsescribe/startup.log
```

**Diagnostics-Report:** Menübar → **Export Diagnostics…** erstellt ein Zip unter `~/.pulsescribe/diagnostics/` (API-Keys maskiert, Log-Tail redacted).

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

Um eine eigenständige `PulseScribe.app` zu erstellen:

```bash
# PyInstaller installieren (falls noch nicht vorhanden)
pip install pyinstaller

# Vollständiger Build (alle Provider inkl. lokaler Whisper-Backends)
./build_app.sh

# Schlanker Build (nur Cloud: Deepgram, OpenAI, Groq - keine lokalen Backends)
./build_app.sh --slim

# Output: dist/PulseScribe.app
```

**Build-Varianten:**

| Variante            | Größe   | Provider                              | Anwendungsfall                      |
| ------------------- | ------- | ------------------------------------- | ----------------------------------- |
| **Full** (Standard) | ~1 GB   | Alle (Deepgram, OpenAI, Groq, Lokal)  | Offline-Transkription benötigt      |
| **Slim** (`--slim`) | ~300 MB | Nur Cloud (Deepgram, OpenAI, Groq)    | Nur Cloud-Nutzung, kleinerer Download |

> **Hinweis:** Der Slim-Build enthält keine lokalen Whisper-Backends (faster-whisper, mlx-whisper, lightning). Für Offline-Transkription den Full-Build verwenden.

**Optional: Code-Signierung für stabile Accessibility-Berechtigungen**

```bash
codesign --force --deep --sign - dist/PulseScribe.app
```

> **Hinweis:** Ohne Signierung muss die App nach jedem Neubuild in Systemeinstellungen → Datenschutz & Sicherheit → Bedienungshilfen neu autorisiert werden. Siehe [Auto-Paste Troubleshooting](#auto-paste-troubleshooting-app-bundle).

### DMG erstellen (für Distribution empfohlen)

```bash
# Dev (ad-hoc signiert)
./build_dmg.sh

# Release (Developer ID + Notarization)
export CODESIGN_IDENTITY="Developer ID Application: Dein Name (TEAMID)"
export NOTARY_PROFILE="whispergo-notary"
./build_dmg.sh 1.0.0 --notarize
```

Siehe `docs/BUILDING_MACOS.md` für die Notarization-Einrichtung.

## Weitere Dokumentation

| Dokument | Beschreibung |
|----------|--------------|
| [Konfigurations-Referenz](docs/KONFIGURATION.md) | Alle Einstellungen, API-Keys, Hotkeys, Umgebungsvariablen |
| [Lokale Backends](docs/LOKALE_BACKENDS.md) | Offline-Transkription mit Whisper, MLX, Lightning |
| [Sicherheit & Datenschutz](docs/SICHERHEIT.md) | Datenverarbeitung, Berechtigungen, API-Key-Speicherung |
| [Netzwerk-Anforderungen](docs/NETZWERK.md) | Endpunkte, Firewall-Regeln, Proxy-Konfiguration, Offline-Modus |
| [Vision & Roadmap](docs/VISION.md) | Projektziele und geplante Features |
| [macOS Build](docs/BUILDING_MACOS.md) | App-Bundle und DMG-Erstellung |
| [Windows Build](docs/BUILDING_WINDOWS.md) | EXE und Installer-Erstellung |
| [CLAUDE.md](CLAUDE.md) | Architektur-Referenz für Entwickler |
