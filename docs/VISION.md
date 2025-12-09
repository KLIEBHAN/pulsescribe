# whisper_go Vision

> Eine minimalistische, Open-Source Alternative zu [Wispr Flow](https://wisprflow.ai) – systemweite Spracheingabe für macOS.

---

## Das Problem

Tippen ist langsam. Gedanken fließen schneller als Finger tippen können.

Bestehende Diktat-Tools sind:

| Tool             | Problem                              |
| ---------------- | ------------------------------------ |
| **Wispr Flow**   | $12/Monat, Cloud-only, closed source |
| **Dragon**       | Veraltet, komplex, teuer             |
| **macOS Diktat** | Eingeschränkt, nicht in allen Apps   |

## Die Lösung

**whisper_go** – ein schlankes Tool das:

1. Per **Hotkey** aktiviert wird
2. Sprache in **Text** umwandelt (via Whisper)
3. Text automatisch **einfügt**

Kein Electron. Kein Cloud-Lock-in. Kein Abo.

---

## Kern-Prinzipien

| Prinzip            | Bedeutung                    |
| ------------------ | ---------------------------- |
| **Minimalistisch** | Eine Sache gut machen        |
| **Offline-first**  | Lokale Modelle als Default   |
| **Atomar**         | Kleine, fokussierte Releases |
| **Open Source**    | Transparent, erweiterbar     |

---

## Roadmap

### Phase 1: Foundation ✅

- [x] CLI-Tool für Transkription (`transcribe.py`)
- [x] API- und lokaler Modus
- [x] Mikrofon-Aufnahme mit Enter-Toggle
- [x] Zwischenablage-Integration (`--copy`)

### Phase 2: System-Integration ✅

- [x] Raycast Extension für Hotkey-Aktivierung
- [x] Auto-Paste nach Transkription
- [x] Akustisches Feedback bei Aufnahmestart (`play_ready_sound`)
- [x] Menübar-Feedback (`menubar.py` mit rumps)

### Phase 3: Smart Features ✅

- [x] LLM-Nachbearbeitung (Füllwörter entfernen, Formatierung)
- [x] Multi-Provider Support (OpenAI, OpenRouter)
- [x] Deepgram Nova-3 Integration (schneller als Whisper API)
- [x] Deepgram WebSocket-Streaming (Echtzeit-Transkription)
- [x] Kontext-Awareness (Email formal, Chat casual, Code technisch)
- [x] Custom Vocabulary (Namen, Fachbegriffe)

### Phase 4: Native App ✅

- [x] macOS Menübar-App (`menubar.py` mit rumps)
- [x] Konfigurierbare Hotkeys (via Raycast System-Hotkey)
- [x] Live-Preview Overlay (`overlay.py` mit PyObjC)
- [x] Animierte Schallwellen-Visualisierung
- [x] Sprach-Commands ("neuer Absatz", "Punkt") via LLM-Refine

### Phase 4.5: Quality & Testing ✅

- [x] Unit-Tests mit pytest (145 Tests, ~0.5s)
- [x] CI/CD Pipeline (GitHub Actions auf macOS)
- [x] Code Coverage mit Codecov
- [x] Parametrisierte Tests für Wartbarkeit
- [x] Zombie-Prozess Prevention (Double-Fork Daemon)

### Phase 5: Multi-Platform ← aktuell

- [x] **Native Hotkeys (macOS)** – Raycast-Unabhängigkeit ✅
  - Hotkey-Registrierung via [QuickMacHotKey](https://github.com/glyph/QuickMacHotKey) (Carbon API)
  - Konfigurierbare Tastenkombinationen (z. B. F19, Cmd+Shift+R)
  - Keine Accessibility-Berechtigung erforderlich
  - Raycast wird optional (für Nutzer, die es bevorzugen)
- [ ] Plattform-Abstraktion und Projektstruktur, siehe unten
- [ ] **Native Hotkeys (Windows/Linux)** – Cross-Platform
  - Geplant: [pynput](https://pynput.readthedocs.io/) für Windows und Linux
  - Gleiche UX wie macOS-Implementierung
- [ ] **Windows Support** – Priorisiert, siehe [WINDOWS_ANALYSIS.md](./WINDOWS_ANALYSIS.md)
  - Aufwand: 120–150h (vollständige Feature-Parität)
  - Kritische Komponenten: Daemon/IPC, Overlay UI, Hotkeys
- [ ] Linux Support
- [ ] iOS Keyboard (optional)

---

## Architektur

```
┌───────────────────────────────────────────────────────────┐
│                      whisper_go                           │
├──────────────┬────────────────────────────────────────────┤
│ Trigger      │ Raycast / Hotkey / CLI                     │
├──────────────┼────────────────────────────────────────────┤
│ Audio        │ sounddevice → WAV (+ Ready-Sound)          │
├──────────────┼────────────────────────────────────────────┤
│ Transkription│ Deepgram Nova-3 / OpenAI API / Whisper     │
├──────────────┼────────────────────────────────────────────┤
│ Feedback     │ Overlay (PyObjC) / Menübar (rumps)         │
├──────────────┼────────────────────────────────────────────┤
│ Nachbearbeit.│ GPT-5 / OpenRouter (Claude, Llama, etc.)   │
├──────────────┼────────────────────────────────────────────┤
│ Output       │ Clipboard → Auto-Paste                     │
└──────────────┴────────────────────────────────────────────┘
```

---

## Nicht-Ziele

Bewusst ausgeschlossen, um Fokus zu halten:

- ❌ Sprachsteuerung ("öffne Safari")
- ❌ Meeting-Transkription (> 5 Min)
- ❌ Team/Enterprise Features
- ❌ Eigenes Modell-Training

> **Update:** Echtzeit-Streaming ist jetzt via Deepgram WebSocket verfügbar (~300ms Latenz).

---

## Tech-Stack

| Layer   | Technologie                                 | Warum                        |
| ------- | ------------------------------------------- | ---------------------------- |
| Core    | Python 3.10+                                | Whisper-Integration, einfach |
| Audio   | sounddevice                                 | Cross-platform, low-level    |
| STT     | Deepgram / OpenAI / Whisper                 | Flexibel, best-of-breed      |
| LLM     | OpenAI / OpenRouter / Groq                  | Multi-Provider für Refine    |
| Hotkey  | QuickMacHotKey (macOS) / pynput (Win/Linux) | Native, konfigurierbar       |
| Menübar | rumps                                       | Native macOS Menübar         |
| Overlay | PyObjC                                      | Native macOS UI, 60fps       |
| Test    | pytest + GitHub Actions                     | CI/CD mit Coverage           |

---

## Erfolgs-Metriken

| Metrik        | Ziel                 | Status                         |
| ------------- | -------------------- | ------------------------------ |
| Latenz        | < 2s (Hotkey → Text) | ✅ ~300ms (Deepgram Streaming) |
| Genauigkeit   | > 95% (DE/EN)        | ✅ Erreicht mit Nova-3         |
| RAM (Idle)    | < 100 MB             | ✅ Kein Daemon im Idle         |
| Onboarding    | < 1 Minute           | ✅ Schnellstart in README      |
| Test-Coverage | > 60% (Core)         | ✅ 145 Tests, CI/CD aktiv      |

---

## Inspiration

- [Wispr Flow](https://wisprflow.ai) – UX-Vorbild
- [Talon Voice](https://talonvoice.com) – Accessibility-fokussiert
- [OpenAI Whisper](https://github.com/openai/whisper) – Die Engine

---

## Modularisierung & Cross-Platform

> **Status:** Genehmigt – Umsetzung in 3 PRs geplant

### Ziel

Refactoring der Codebase für bessere Wartbarkeit und Cross-Platform-Support (Windows/Linux).

### Projektstruktur

```
whisper_go/
├── transcribe.py                  # CLI Entry Point (Wrapper)
├── whisper_daemon.py              # Unified Daemon
├── hotkey_daemon.py               # Standalone Hotkey-Daemon
├── prompts.py                     # LLM-Prompts
│
├── whisper_platform/                      # 🔑 Plattform-Abstraktion Layer
│   ├── __init__.py                # Platform-Detection + Factory
│   ├── base.py                    # Protocol-Definitionen
│   ├── sound.py                   # Sound-Playback (CoreAudio/winsound)
│   ├── clipboard.py               # Clipboard (pbcopy/win32)
│   ├── app_detection.py           # App-Detection (NSWorkspace/win32gui)
│   ├── hotkey.py                  # Hotkeys (QuickMacHotKey/pynput)
│   └── daemon.py                  # Daemon/IPC (fork+SIGUSR1/Named Pipes)
│
├── providers/                     # Transkriptions-Provider
│   ├── __init__.py                # Factory + Protocol
│   ├── base.py                    # TranscriptionProvider Protocol
│   ├── openai.py                  # OpenAI Whisper API
│   ├── deepgram.py                # Deepgram REST
│   ├── deepgram_stream.py         # Deepgram WebSocket Streaming
│   ├── groq.py                    # Groq Whisper
│   └── local.py                   # Lokales Whisper-Modell
│
├── audio/                         # Audio-Handling
│   ├── recording.py               # Mikrofon-Aufnahme (sounddevice)
│   └── playback.py                # Sound-Feedback (via platform/)
│
├── refine/                        # LLM-Nachbearbeitung
│   ├── llm.py                     # Refine-Logik
│   ├── prompts.py                 # Prompt-Templates
│   └── context.py                 # Kontext-Detection
│
└── utils/                         # Utilities
    ├── logging.py                 # Logging-Setup
    ├── timing.py                  # Zeitmessung
    └── paths.py                   # Platform-aware Pfade
```

### Plattform-Abstraktion

Protocol-basierte Interfaces für plattformspezifische Funktionalität:

```python
class SoundPlayer(Protocol):
    def play(self, name: str) -> None: ...

class ClipboardHandler(Protocol):
    def copy(self, text: str) -> bool: ...

class AppDetector(Protocol):
    def get_frontmost_app(self) -> str | None: ...

class DaemonController(Protocol):
    def start(self, command: list[str]) -> int: ...
    def stop(self, pid: int) -> bool: ...
```

### Implementierungsplan

| PR       | Inhalt                                              | Aufwand | Status           |
| -------- | --------------------------------------------------- | ------- | ---------------- |
| **PR 1** | `whisper_platform/` Layer + `providers/` Extraktion | 12-16h  | ✅ Abgeschlossen |
| **PR 2** | `audio/`, `refine/`, `utils/` + Streaming           | 10-14h  | 📋 Geplant       |
| **PR 3** | CLI Modernisierung + Cleanup                        | 6-8h    | 📋 Geplant       |

#### PR 1 Details (Abgeschlossen)

- `whisper_platform/`: Factory, Protocols, Sound, Clipboard, App-Detection, Daemon, Hotkey
- `providers/`: OpenAI, Deepgram (REST), Groq, Local
- `transcribe()` nutzt jetzt `providers.get_provider()`
- ~290 Zeilen aus `transcribe.py` entfernt

#### PR 2 Details (Geplant)

- **`audio/recording.py`**: Mikrofon-Aufnahme mit sounddevice
- **`providers/deepgram_stream.py`**: WebSocket-Streaming (nur Protokoll)
- **`refine/`**: LLM-Nachbearbeitung extrahieren
- **`utils/`**: Logging, Timing, Paths

> **Hinweis Streaming:** Das Deepgram-Streaming (`_deepgram_stream_core`) wird in PR 2
> zusammen mit Audio-Recording extrahiert. Die ~400 Zeilen Streaming-Code vermischen
> aktuell Provider-Logik, Audio-Aufnahme und Orchestrierung. Für saubere Trennung
> muss beides gleichzeitig refactored werden.
>
> **Hinweis Recording:** Aktuell existiert eine Code-Duplizierung für Audio-Recording
> zwischen `whisper_daemon.py` (`_recording_worker`) und `transcribe.py`. Diese wird
> in PR 2 durch die zentrale `audio/recording.py` Komponente aufgelöst.

### Rückwärtskompatibilität

- ✅ CLI-Interface bleibt **100% kompatibel**
- ✅ Alle bestehenden Befehle funktionieren weiterhin
- ✅ `transcribe.py` bleibt als Entry Point erhalten

---

_Stand: Dezember 2025_
