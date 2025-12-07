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
- [ ] Menübar-Feedback (optional)

### Phase 3: Smart Features ← aktuell

- [x] LLM-Nachbearbeitung (Füllwörter entfernen, Formatierung)
- [x] Multi-Provider Support (OpenAI, OpenRouter)
- [x] Deepgram Nova-3 Integration (schneller als Whisper API)
- [x] Kontext-Awareness (Email formal, Chat casual, Code technisch)
- [ ] Custom Vocabulary (Namen, Fachbegriffe)

### Phase 4: Native App

- [ ] macOS Menübar-App (rumps oder Swift)
- [ ] Konfigurierbare Hotkeys
- [ ] Sprach-Commands ("neuer Absatz", "Punkt")

### Phase 5: Multi-Platform

- [ ] Linux Support
- [ ] Windows Support
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
│ Nachbearbeit.│ GPT-5 / OpenRouter (Claude, Llama, etc.)   │
├──────────────┼────────────────────────────────────────────┤
│ Output       │ Clipboard → Auto-Paste                     │
└──────────────┴────────────────────────────────────────────┘
```

---

## Nicht-Ziele

Bewusst ausgeschlossen, um Fokus zu halten:

- ❌ Echtzeit-Streaming während des Sprechens
- ❌ Sprachsteuerung ("öffne Safari")
- ❌ Meeting-Transkription (> 5 Min)
- ❌ Team/Enterprise Features
- ❌ Eigenes Modell-Training

---

## Tech-Stack

| Layer  | Technologie                 | Warum                        |
| ------ | --------------------------- | ---------------------------- |
| Core   | Python 3.10+                | Whisper-Integration, einfach |
| Audio  | sounddevice                 | Cross-platform, low-level    |
| STT    | Deepgram / OpenAI / Whisper | Flexibel, best-of-breed      |
| LLM    | OpenAI / OpenRouter         | Multi-Provider für Refine    |
| Hotkey | Raycast Extension           | Native macOS Integration     |
| GUI    | rumps (geplant)             | Native macOS Menübar         |

---

## Erfolgs-Metriken

| Metrik      | Ziel                 |
| ----------- | -------------------- |
| Latenz      | < 2s (Hotkey → Text) |
| Genauigkeit | > 95% (DE/EN)        |
| RAM (Idle)  | < 100 MB             |
| Onboarding  | < 1 Minute           |

---

## Inspiration

- [Wispr Flow](https://wisprflow.ai) – UX-Vorbild
- [Talon Voice](https://talonvoice.com) – Accessibility-fokussiert
- [OpenAI Whisper](https://github.com/openai/whisper) – Die Engine

---

_Stand: Dezember 2025_
