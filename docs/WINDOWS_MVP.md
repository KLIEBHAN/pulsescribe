# Windows MVP Definition

> **Status:** Draft
> **Ziel:** Funktionsfähige Windows-Version mit minimalem Scope
> **Referenz:** [ADR-002](adr/002-windows-strategy-port-vs-separate.md)

---

## MVP-Scope: "Es funktioniert"

### Must Have (MVP)

| Feature            | Beschreibung                                                | Aufwand             |
| ------------------ | ----------------------------------------------------------- | ------------------- |
| **Hotkey**         | Globaler Hotkey startet/stoppt Aufnahme (z.B. `Ctrl+Alt+R`) | 8-12h               |
| **Recording**      | Mikrofon-Aufnahme via `sounddevice`                         | 2-4h (verifizieren) |
| **Transcription**  | Deepgram-Streaming oder REST                                | 2-4h (verifizieren) |
| **Clipboard**      | Ergebnis in Zwischenablage kopieren                         | 2-4h                |
| **Auto-Paste**     | Optional: `Ctrl+V` simulieren                               | 4-6h                |
| **Tray-Icon**      | Minimales Status-Feedback (Recording/Done/Error)            | 6-8h                |
| **Sound-Feedback** | Start/Stop/Done Sounds                                      | 2-4h                |

**Gesamt MVP:** ~30-45h

### Nice to Have (Post-MVP)

| Feature       | Beschreibung                        | Aufwand               |
| ------------- | ----------------------------------- | --------------------- |
| Overlay       | Visuelles Feedback während Aufnahme | 15-25h                |
| App-Detection | Kontext-Awareness (Email/Chat/Code) | 4-6h                  |
| LLM-Refine    | Nachbearbeitung via OpenRouter/Groq | 2-4h (Core existiert) |
| Settings-GUI  | Konfigurationsfenster               | 10-15h                |
| Installer     | MSI/NSIS mit Autostart              | 8-12h                 |

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

### Phase 1: Architektur-Fixes (4-6h)

- [ ] **P0:** `utils/permissions.py` → Conditional Import
- [ ] **P1:** `refine/context.py` → Fallback entfernen
- [ ] **Verify:** `whisper_platform/` Windows-Klassen sind vollständig

### Phase 2: Core-Verifikation (4-6h)

- [ ] `sounddevice` Recording auf Windows testen
- [ ] Deepgram-Streaming auf Windows testen
- [ ] `pyperclip` Clipboard auf Windows testen

### Phase 3: Windows Entry-Point (12-16h)

- [ ] `pulsescribe_windows.py` erstellen
- [ ] Hotkey-Integration (`pynput`)
- [ ] Tray-Icon (`pystray`)
- [ ] Sound-Feedback (`winsound`)
- [ ] State-Machine (Idle → Recording → Transcribing → Done)

### Phase 4: Integration & Test (8-12h)

- [ ] End-to-End Test: Hotkey → Record → Transcribe → Paste
- [ ] Edge-Cases: Kein Mikrofon, Netzwerk-Fehler
- [ ] PyInstaller EXE erstellen

---

## Exit-Kriterien (MVP Done)

- [ ] Globaler Hotkey startet/stoppt Aufnahme zuverlässig
- [ ] Deepgram-Streaming funktioniert reproduzierbar
- [ ] Ergebnis landet in Clipboard
- [ ] Auto-Paste funktioniert (optional)
- [ ] Tray-Icon zeigt Status (Recording/Done/Error)
- [ ] Sound-Feedback bei Start/Stop/Done
- [ ] EXE startet ohne Fehler (SmartScreen-Warning akzeptabel)

---

## Dependencies (Windows)

```txt
# requirements-windows.txt
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
pynput            # Globale Hotkeys
pyperclip         # Clipboard (Fallback)
pywin32           # Windows API (optional, für win32gui)
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
