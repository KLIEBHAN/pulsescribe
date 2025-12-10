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

### Aktueller Fokus: Phase 5 (Multi-Platform)

- [x] **Native Hotkeys (macOS)** – Raycast-Unabhängigkeit ✅
  - Hotkey-Registrierung via [QuickMacHotKey](https://github.com/glyph/QuickMacHotKey) (Carbon API)
  - Raycast ist optional
- [ ] **Windows Support** – Priorisiert (siehe [WINDOWS_ANALYSIS.md](./WINDOWS_ANALYSIS.md))
- [ ] **Linux Support**
- [ ] **CLI Modernisierung** (Migration auf `typer`)

### Abgeschlossene Meilensteine (Phases 1-4) ✅

- **Foundation:** CLI-Tool, Audio-Aufnahme, Zwischenablage
- **System-Integration:** Menübar-Feedback, Auto-Paste
- **Smart Features:** LLM-Refine, Deepgram Streaming (~300ms Latenz), Kontext-Awareness
- **Native App:** Menübar-App, Overlay UI, Schallwellen-Visualisierung
- **App Bundle:** PyInstaller-basierte macOS App (`WhisperGo.app`)
- **Quality:** 198 Tests, CI/CD, Modularisierung

---

## Architektur

```
┌───────────────────────────────────────────────────────────┐
│                      whisper_go                           │
├──────────────┬────────────────────────────────────────────┤
│ Trigger      │ Hotkey (Global) / Raycast / CLI            │
├──────────────┼────────────────────────────────────────────┤
│ Audio        │ sounddevice → WAV                          │
├──────────────┼────────────────────────────────────────────┤
│ Transkription│ Deepgram Nova-3 / OpenAI API / Whisper     │
├──────────────┼────────────────────────────────────────────┤
│ Feedback     │ Overlay (PyObjC) / Menübar (rumps)         │
├──────────────┼────────────────────────────────────────────┤
│ Nachbearbeit.│ GPT-5 / OpenRouter (Refine & Actions)      │
├──────────────┼────────────────────────────────────────────┤
│ Output       │ Clipboard → Auto-Paste                     │
└──────────────┴────────────────────────────────────────────┘
```

### Projektstruktur (Clean Architecture)

Das Projekt ist vollständig modularisiert:

```
whisper_go/
├── transcribe.py                  # CLI Entry Point
├── whisper_daemon.py              # Unified Daemon (Orchestrator)
├── build_app.spec                 # PyInstaller Spec für WhisperGo.app
├── config.py                      # Zentrale Konfiguration
├── providers/                     # Transkriptions-Provider (Deepgram, OpenAI, etc.)
├── audio/                         # Audio-Handling (Recording)
├── refine/                        # LLM-Nachbearbeitung & Prompts
├── ui/                            # Native UI (Menübar & Overlay)
├── utils/                         # Utilities (Logging, Hotkey, Daemon, Permissions)
└── whisper_platform/              # OS-Abstraktionsschicht
```

### User-Daten

Alle User-spezifischen Daten in `~/.whisper_go/`:

```
~/.whisper_go/
├── .env                           # User-Konfiguration (API Keys, etc.)
├── logs/whisper_go.log            # Rotierendes Log (max 1MB, 3 Backups)
├── startup.log                    # Emergency-Log für Crash-Debugging
└── vocabulary.json                # Custom Vocabulary
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
