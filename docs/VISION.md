# PulseScribe Vision

> Eine minimalistische, Open-Source Alternative zu [Wispr Flow](https://wisprflow.ai) – systemweite Spracheingabe für macOS und Windows.

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

**PulseScribe** – ein schlankes Tool das:

1. Per **Hotkey** aktiviert wird
2. Sprache in **Text** umwandelt (via Whisper)
3. Text automatisch **einfügt**

Kein Electron. Kein Cloud-Lock-in. Kein Abo.

---

## Kern-Prinzipien

| Prinzip            | Bedeutung                    |
| ------------------ | ---------------------------- |
| **Minimalistisch** | Eine Sache gut machen        |
| **Offline-fähig**  | Lokale Modelle als Option    |
| **Atomar**         | Kleine, fokussierte Releases |
| **Open Source**    | Transparent, erweiterbar     |

---

## Roadmap

### Aktueller Fokus: Phase 6 (Polish & Linux)

- [x] **Native Hotkeys (macOS)** ✅
  - Hotkey-Registrierung via [QuickMacHotKey](https://github.com/glyph/QuickMacHotKey) (Carbon API)
- [x] **Windows Support** ✅ – MVP Complete
  - System-Tray, Hotkeys, Settings-GUI, Mica-Effekt
- [ ] **Linux Support** – Geplant
- [x] **CLI Modernisierung** (Migration auf `typer`) ✅

### Abgeschlossene Meilensteine (Phases 1-5) ✅

- **Foundation:** CLI-Tool, Audio-Aufnahme, Zwischenablage
- **System-Integration:** Menübar-Feedback, Auto-Paste
- **Smart Features:** LLM-Refine, Deepgram Streaming (~300ms Latenz), Kontext-Awareness
- **Native App (macOS):** Menübar-App, Overlay UI, Schallwellen-Visualisierung
- **App Bundle:** PyInstaller-basierte macOS App (`PulseScribe.app`)
- **Windows MVP:** System-Tray, PySide6 Settings-GUI, Mica-Effekt, Installer
- **Quality:** Tests, CI/CD, Modularisierung

---

## Architektur

```
┌───────────────────────────────────────────────────────────┐
│                      pulsescribe                          │
├──────────────┬────────────────────────────────────────────┤
│ Trigger      │ Hotkey (Global) / CLI                      │
├──────────────┼────────────────────────────────────────────┤
│ Audio        │ sounddevice → WAV                          │
├──────────────┼────────────────────────────────────────────┤
│ Transkription│ Deepgram Nova-3 / OpenAI API / Whisper     │
├──────────────┼────────────────────────────────────────────┤
│ Feedback     │ Overlay (PyObjC) / Menübar (rumps)         │
├──────────────┼────────────────────────────────────────────┤
│ Nachbearbeit.│ GPT-4o / Groq / OpenRouter (Refine)        │
├──────────────┼────────────────────────────────────────────┤
│ Output       │ Clipboard → Auto-Paste                     │
└──────────────┴────────────────────────────────────────────┘
```

### Projektstruktur (Clean Architecture)

Das Projekt ist vollständig modularisiert:

```
pulsescribe/
├── transcribe.py           # CLI Entry Point
├── pulsescribe_daemon.py   # macOS Daemon (NSApplication)
├── pulsescribe_windows.py  # Windows Daemon (pystray + PySide6)
├── config.py               # Zentrale Konfiguration
├── audio/                  # Audio-Handling (Recording)
├── cli/                    # CLI-Typdefinitionen (Enums für Mode, Context, Provider)
├── providers/              # Transkriptions-Provider (Deepgram, OpenAI, Groq, Local)
├── refine/                 # LLM-Nachbearbeitung & Prompts
├── ui/                     # Native UI (Menübar, Overlay, Settings, Onboarding)
├── utils/                  # Utilities (Logging, Hotkey, Preferences, etc.)
├── whisper_platform/       # OS-Abstraktionsschicht (Clipboard, Sound, Hotkey)
└── tests/                  # Unit & Integration Tests
```

Für die vollständige Struktur siehe [CLAUDE.md](../CLAUDE.md).

### User-Daten

Alle User-spezifischen Daten in `~/.pulsescribe/`:

```
~/.pulsescribe/
├── .env                    # User-Konfiguration (API Keys, etc.)
├── logs/pulsescribe.log    # Rotierendes Log (max 1MB, 3 Backups)
├── startup.log             # Emergency-Log für Crash-Debugging
├── vocabulary.json         # Custom Vocabulary
└── prompts.toml            # Custom Prompts
```

---

## Erfolgs-Metriken

| Metrik        | Ziel                 | Status                         |
| ------------- | -------------------- | ------------------------------ |
| Latenz        | < 2s (Hotkey → Text) | ✅ ~300ms (Deepgram Streaming) |
| Genauigkeit   | > 95% (DE/EN)        | ✅ Erreicht mit Nova-3         |
| RAM (Idle)    | < 100 MB             | ✅ Kein Daemon im Idle         |
| Onboarding    | < 1 Minute           | ✅ Schnellstart in README      |
| Test-Coverage | > 60% (Core)         | ✅ 198 Tests, CI/CD aktiv      |

---

## Inspiration

- [Wispr Flow](https://wisprflow.ai) – UX-Vorbild
- [Talon Voice](https://talonvoice.com) – Accessibility-fokussiert
- [OpenAI Whisper](https://github.com/openai/whisper) – Die Engine

---

_Stand: Dezember 2025_
