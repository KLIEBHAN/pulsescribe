# whisper_go Vision

> **Ziel:** Eine minimalistische, Open-Source Alternative zu [Wispr Flow](https://wisprflow.ai) – systemweite Spracheingabe für macOS.

---

## Das Problem

Tippen ist langsam. Gedanken fließen schneller als Finger tippen können. Bestehende Diktat-Tools sind entweder:

- **Zu teuer** (Wispr Flow: $12/Monat)
- **Zu komplex** (Dragon NaturallySpeaking)
- **Zu eingeschränkt** (macOS Diktat: nur in manchen Apps)

## Die Lösung

**whisper_go** – ein schlankes Tool das:

1. Per Hotkey aktiviert wird (⌘+Shift+Space)
2. Sprache in Text umwandelt (via OpenAI Whisper)
3. Text automatisch einfügt (an Cursor-Position)

Kein Electron. Kein Cloud-Lock-in. Kein Abo.

---

## Kern-Prinzipien

| Prinzip            | Bedeutung                                        |
| ------------------ | ------------------------------------------------ |
| **Minimalistisch** | Eine Sache gut machen, nicht alles mittelmäßig   |
| **Offline-first**  | Lokale Whisper-Modelle als Default, API optional |
| **Atomar**         | Kleine, fokussierte Releases statt Big Bang      |
| **Open Source**    | Transparent, erweiterbar, community-driven       |

---

## Feature-Roadmap

### Phase 1: Foundation ✅

- [x] CLI-Tool für Transkription
- [x] API- und lokaler Modus
- [x] Mikrofon-Aufnahme
- [x] Zwischenablage-Integration

### Phase 2: System-Integration (aktuell)

- [ ] Raycast Extension für Hotkey-Aktivierung
- [ ] Auto-Paste nach Transkription
- [ ] Menübar-Status (optional)

### Phase 3: Smart Features

- [ ] LLM-Nachbearbeitung ("ähm" entfernen, Formatierung)
- [ ] Kontext-Awareness (Email vs. Chat Stil)
- [ ] Custom Vocabulary (Fachbegriffe, Namen)

### Phase 4: Polish

- [ ] Native macOS App (Swift/Python hybrid)
- [ ] Hotkey-Konfiguration
- [ ] Sprach-Shortcuts ("neuer Absatz", "Punkt")

### Phase 5: Platform

- [ ] iOS Keyboard Extension
- [ ] Linux Support
- [ ] Windows Support

---

## Architektur-Vision

```
┌─────────────────────────────────────────────────────┐
│                    whisper_go                        │
├─────────────────────────────────────────────────────┤
│  Trigger          │  Hotkey / Raycast / CLI         │
├───────────────────┼─────────────────────────────────┤
│  Audio Capture    │  sounddevice (Mikrofon)         │
├───────────────────┼─────────────────────────────────┤
│  Transcription    │  Whisper (lokal) / OpenAI API   │
├───────────────────┼─────────────────────────────────┤
│  Post-Processing  │  LLM (optional, für Cleanup)    │
├───────────────────┼─────────────────────────────────┤
│  Output           │  Clipboard → Auto-Paste         │
└─────────────────────────────────────────────────────┘
```

---

## Nicht-Ziele (bewusst ausgeschlossen)

- ❌ Echtzeit-Streaming (Transkription während Sprechen)
- ❌ Sprachsteuerung ("öffne Safari")
- ❌ Meeting-Transkription (lange Aufnahmen)
- ❌ Multi-User / Team Features
- ❌ Eigenes LLM Training

---

## Technologie-Stack

| Komponente    | Technologie                          | Grund                        |
| ------------- | ------------------------------------ | ---------------------------- |
| Core          | Python 3.10+                         | Einfach, Whisper-Integration |
| Audio         | sounddevice                          | Cross-platform, low-level    |
| Transkription | openai-whisper / OpenAI API          | State of the art             |
| Hotkey        | Raycast (Phase 2) → pynput (Phase 4) | Iterativ komplexer           |
| GUI           | rumps (Menübar)                      | Minimal, native macOS        |

---

## Erfolgs-Metriken

1. **Latenz:** < 2 Sekunden von Hotkey bis Text erscheint
2. **Genauigkeit:** > 95% Word Error Rate für Deutsch/Englisch
3. **Ressourcen:** < 100MB RAM im Idle
4. **Simplicity:** Neue User verstehen es in < 1 Minute

---

## Inspiration

- [Wispr Flow](https://wisprflow.ai) – UX-Vorbild, aber closed source & teuer
- [Talon Voice](https://talonvoice.com) – Für Accessibility, sehr mächtig
- [OpenAI Whisper](https://github.com/openai/whisper) – Die Engine unter der Haube

---

_Letzte Aktualisierung: Dezember 2024_
