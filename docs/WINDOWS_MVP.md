# Windows MVP Definition

> **Status:** ✅ MVP Complete (2025-12-22)
> **Ziel:** Funktionsfähige Windows-Version mit minimalem Scope
> **Referenz:** [ADR-002](adr/002-windows-strategy-port-vs-separate.md)

---

## MVP-Scope: "Es funktioniert"

### Must Have (MVP) ✅

| Feature            | Beschreibung                                                        | Status |
| ------------------ | ------------------------------------------------------------------- | ------ |
| **Hotkey**         | Toggle + Hold Hotkeys (konsistent mit macOS), z.B. `Ctrl+Alt+R`     | ✅ Done |
| **Recording**      | Mikrofon-Aufnahme via `sounddevice`                         | ✅ Done |
| **Transcription**  | Deepgram (Stream), Groq (REST), OpenAI, Local               | ✅ Done |
| **Clipboard**      | Ergebnis in Zwischenablage kopieren                         | ✅ Done |
| **Auto-Paste**     | `Ctrl+V` simulieren via pynput                              | ✅ Done |
| **Tray-Icon**      | Status-Feedback (Idle/Recording/Transcribing/Refining/Done) | ✅ Done |
| **Sound-Feedback** | Windows System-Sounds (DeviceConnect/Disconnect/SMS)        | ✅ Done |

### Post-MVP Features

| Feature           | Beschreibung                        | Status    |
| ----------------- | ----------------------------------- | --------- |
| **LLM-Refine**        | Nachbearbeitung via Groq/OpenAI     | ✅ Done    |
| **App-Detection**     | Kontext-Awareness (Email/Chat/Code) | ✅ Done    |
| **WebSocket Stream**  | Echtzeit-Transkription (~300ms)     | ✅ Done    |
| **Overlay**           | Visuelles Feedback während Aufnahme | ✅ Done    |
| **Installer**         | Inno Setup mit Autostart            | ✅ Done    |
| Settings-GUI      | Konfigurationsfenster               | ⏳ Offen  |

### Out of Scope (v1)

- Glass/Acrylic Overlay-Effekte
- Vollständige UI-Parität mit macOS
- Code-Signing (für MVP ohne Reputation)

---

## Architektur-Voraussetzungen

### Status: Core-Trennung

Die Analyse zeigt: **Core ist zu ~95% sauber**, aber es gibt 2 kritische Fixes:

#### 🔴 P0: `utils/permissions.py` - Top-Level Import

**Problem:** Zeile 9-16 importiert `AVFoundation` auf Top-Level → bricht auf Windows

```python
# AKTUELL (SCHLECHT):
from AVFoundation import (
    AVCaptureDevice,
    AVMediaTypeAudio,
    ...
)
```

**Fix:** Conditional Import oder nach `whisper_platform/` verschieben

#### 🟠 P1: `refine/context.py` - Redundanter Fallback

**Problem:** Zeile 29-40 hat Fallback auf direkten `AppKit`-Import

```python
# AKTUELL (REDUNDANT):
except ImportError:
    from AppKit import NSWorkspace  # Fallback
```

**Fix:** Nur `whisper_platform.app_detection` nutzen, Fallback entfernen

### Clean Components ✅

| Modul                         | Status                            |
| ----------------------------- | --------------------------------- |
| `providers/*`                 | ✅ Keine macOS-Imports            |
| `refine/*` (außer context.py) | ✅ Keine macOS-Imports            |
| `audio/recording.py`          | ✅ Nutzt whisper_platform korrekt |
| `config.py`                   | ✅ Keine macOS-Imports            |
| `transcribe.py`               | ✅ Delegiert an whisper_platform  |
| `whisper_platform/*`          | ✅ Saubere Trennung mit Factories |

---

## Windows Entry-Point

### Neuer Daemon: `pulsescribe_windows.py`

Separater Entry-Point statt `pulsescribe_daemon.py` zu portieren:

```
pulsescribe/
├── pulsescribe_daemon.py      # macOS (NSApplication Loop)
├── pulsescribe_windows.py     # Windows (neu)
└── whisper_platform/
    ├── daemon.py              # WindowsDaemonController existiert
    └── ...
```

### Struktur (Vorschlag)

```python
# pulsescribe_windows.py

import sys
if sys.platform != "win32":
    raise RuntimeError("This script is Windows-only")

from whisper_platform import (
    get_hotkey_listener,
    get_clipboard,
    get_sound_player,
)
from providers.deepgram_stream import transcribe_with_deepgram_stream
from audio.recording import AudioRecorder

class PulseScribeWindows:
    """Windows-Daemon mit Tray-Icon und Hotkey."""

    def __init__(self):
        self.hotkey = get_hotkey_listener()
        self.clipboard = get_clipboard()
        self.sound = get_sound_player()
        self.tray = None  # pystray

    def run(self):
        # Tray-Icon starten
        # Hotkey-Listener starten
        # Event-Loop
        pass
```

---

## Implementation Roadmap

### Phase 1: Architektur-Fixes ✅

- [x] **P0:** `utils/permissions.py` → Conditional Import
- [x] **P1:** `refine/context.py` → Windows App-Detection aktiviert
- [x] **Verify:** `whisper_platform/` Windows-Klassen vollständig

### Phase 2: Core-Verifikation ✅

- [x] `sounddevice` Recording auf Windows getestet
- [x] Deepgram REST API auf Windows getestet
- [x] `pyperclip` Clipboard auf Windows getestet
- [x] `paste_transcript()` mit pynput Ctrl+V getestet

### Phase 3: Windows Entry-Point ✅

- [x] `pulsescribe_windows.py` erstellt
- [x] Hotkey-Integration (`pynput`)
- [x] Tray-Icon (`pystray`) mit Farbcodes
- [x] Sound-Feedback (Windows System-Sounds)
- [x] State-Machine (Idle → Listening → Recording → Transcribing → Refining → Done)

### Phase 4: Integration & Test ✅

- [x] End-to-End Test: Hotkey → Record → Transcribe → Paste
- [x] LLM-Refine Integration (Groq, OpenAI, OpenRouter)
- [x] App-Kontext-Erkennung (case-insensitive)
- [x] PyInstaller Spec (`build_windows.spec`)

---

## Exit-Kriterien (MVP Done) ✅

- [x] Globaler Hotkey startet/stoppt Aufnahme zuverlässig
- [x] Deepgram REST API funktioniert reproduzierbar
- [x] Ergebnis landet in Clipboard
- [x] Auto-Paste funktioniert (Ctrl+V via pynput)
- [x] Tray-Icon zeigt Status (Idle/Recording/Transcribing/Refining/Done/Error)
- [x] Sound-Feedback bei Start/Stop/Done (Windows System-Sounds)
- [x] LLM-Refine mit Groq/OpenAI/OpenRouter
- [x] App-Kontext-Erkennung (Outlook → email, VS Code → code)
- [x] PyInstaller Spec für EXE-Build

---

## Dependencies (Windows)

```txt
# Core (identisch mit macOS)
openai>=1.0.0
deepgram-sdk>=3.0.0
groq>=0.4.0
sounddevice
soundfile
python-dotenv
numpy

# Windows-spezifisch
pystray           # Tray-Icon
Pillow            # Icons für pystray
pynput            # Globale Hotkeys + Ctrl+V Simulation
pyperclip         # Clipboard
pywin32           # Windows API (win32gui, win32process)
psutil            # Prozess-Info für App-Detection
```

---

## Risiken

| Risiko                            | Wahrscheinlichkeit | Mitigation                         |
| --------------------------------- | ------------------ | ---------------------------------- |
| Hotkey-Konflikte mit anderen Apps | Mittel             | Konfigurierbarer Hotkey            |
| Antivirus blockiert EXE           | Mittel             | Dokumentation, später Code-Signing |
| PortAudio-Probleme auf Windows    | Niedrig            | sounddevice bringt Binaries mit    |
| pynput braucht Admin-Rechte?      | Niedrig            | Testen, ggf. keyboard-Library      |

---

## Geschätzter Gesamtaufwand

| Phase               | Aufwand | Kumulativ  |
| ------------------- | ------- | ---------- |
| Architektur-Fixes   | 4-6h    | 4-6h       |
| Core-Verifikation   | 4-6h    | 8-12h      |
| Windows Entry-Point | 12-16h  | 20-28h     |
| Integration & Test  | 8-12h   | 28-40h     |
| **Buffer (+20%)**   | 6-8h    | **34-48h** |

**Realistisch:** ~40h für funktionalen MVP (ohne Installer/Signing)

---

_Erstellt: 2025-12-15_
_MVP Complete: 2025-12-22_
