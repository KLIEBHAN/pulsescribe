# Sicherheit & Datenschutz

[🇺🇸 English Version](SECURITY.md)

Dieses Dokument beschreibt, wie PulseScribe mit deinen Daten umgeht, welche Berechtigungen benötigt werden und Best Practices für die Sicherheit.

## Datenverarbeitung

### Audiodaten

| Aspekt | Verhalten |
|--------|-----------|
| **Speicherung** | Audio wird **standardmäßig nicht lokal gespeichert** |
| **Übertragung** | Direkt zum gewählten Provider gestreamt (Deepgram/OpenAI/Groq) |
| **Lokaler Modus** | Mit `PULSESCRIBE_MODE=local` bleiben Daten auf deinem Gerät |
| **Aufbewahrung** | Prüfe die Datenschutzrichtlinie deines Providers |

### Transkripte

| Aspekt | Verhalten |
|--------|-----------|
| **Zwischenablage** | Nach Transkription in System-Clipboard kopiert |
| **Logs** | Können in Debug-Logs erscheinen (wenn `--debug` aktiviert) |
| **Speicherung** | Werden von PulseScribe nicht dauerhaft gespeichert |

### Log-Dateien

Logs werden in `~/.pulsescribe/logs/` gespeichert:

```
~/.pulsescribe/
├── logs/
│   └── pulsescribe.log    # Rotierend, max 1MB, 3 Backups
└── startup.log            # Emergency Startup-Log
```

**Log-Inhalte:**
- Zeitstempel und Statusmeldungen
- Provider-Antworten (ohne vollständige Transkripte im Normalmodus)
- Fehlermeldungen und Stack-Traces

**Logs enthalten NICHT:**
- API-Keys (im Diagnostics-Export maskiert)
- Rohe Audiodaten

## API-Key-Speicherung

API-Keys werden als **Klartext** in `~/.pulsescribe/.env` gespeichert:

```bash
~/.pulsescribe/.env
├── DEEPGRAM_API_KEY=dg_...
├── OPENAI_API_KEY=sk-...
├── GROQ_API_KEY=gsk_...
└── OPENROUTER_API_KEY=sk-or-...
```

### Sicherheitsempfehlungen

1. **Dateiberechtigungen:** Stelle sicher, dass nur du die Datei lesen kannst:
   ```bash
   chmod 600 ~/.pulsescribe/.env
   ```

2. **Niemals committen:** `.env` zu `.gitignore` hinzufügen (bereits in diesem Repo)

3. **Minimale Rechte:** API-Keys mit minimalen erforderlichen Berechtigungen erstellen

4. **Regelmäßig rotieren:** API-Keys periodisch neu generieren

> **Hinweis:** OS-Keychain-Integration ist für ein zukünftiges Release geplant.

## Erforderliche Berechtigungen

### macOS

| Berechtigung | Grund | Aktivierung |
|--------------|-------|-------------|
| **Mikrofon** | Audioaufnahme | Systemeinstellungen → Datenschutz & Sicherheit → Mikrofon → PulseScribe aktivieren |
| **Bedienungshilfen** | Tastatur-Simulation für Auto-Paste (Cmd+V) | Systemeinstellungen → Datenschutz & Sicherheit → Bedienungshilfen → PulseScribe hinzufügen |
| **Eingabeüberwachung** | Hold-to-Record Hotkeys (Quartz Event Taps) | Systemeinstellungen → Datenschutz & Sicherheit → Eingabeüberwachung → PulseScribe aktivieren |

**Hinweise:**
- **Toggle-Hotkeys** (Drücken-zum-Starten, Drücken-zum-Stoppen) benötigen **keine** Bedienungshilfen/Eingabeüberwachung – sie nutzen die Carbon API (`RegisterEventHotKey`)
- **Hold-Hotkeys** (Push-to-Talk) benötigen Eingabeüberwachung
- Nach dem Neubau einer unsignierten App musst du in den Bedienungshilfen neu autorisieren

### Windows

| Berechtigung | Grund | Aktivierung |
|--------------|-------|-------------|
| **Mikrofon** | Audioaufnahme | Wird bei erster Nutzung über Windows-Dialog gewährt |

**Hinweise:**
- Keine besonderen Berechtigungen für globale Hotkeys erforderlich
- Einige Unternehmensumgebungen blockieren möglicherweise globale Hotkey-Listener

## Netzwerksicherheit

Siehe [NETZWERK.md](NETZWERK.md) für:
- Erforderliche Endpunkte und Ports
- Proxy-Konfiguration
- Firewall-Regeln
- Details zum Offline-Modus

## Provider-Sicherheit

| Provider | Datenverarbeitung | Datenschutzrichtlinie |
|----------|-------------------|----------------------|
| **Deepgram** | Audio wird verarbeitet, standardmäßig nicht gespeichert | [deepgram.com/privacy](https://deepgram.com/privacy) |
| **OpenAI** | Prüfe API-Datennutzungsrichtlinie | [openai.com/policies/privacy-policy](https://openai.com/policies/privacy-policy) |
| **Groq** | Prüfe Datenaufbewahrungseinstellungen | [groq.com/privacy-policy](https://groq.com/privacy-policy) |
| **Lokal** | Gesamte Verarbeitung auf dem Gerät | Keine externe Übertragung |

> **Empfehlung:** Für sensible Daten `PULSESCRIBE_MODE=local` verwenden, um alles auf deinem Gerät zu behalten.

## Diagnostics-Export

Die Funktion "Diagnostics exportieren" (Menübar → Export Diagnostics…) erstellt eine ZIP-Datei mit:

- Systeminformationen
- Bereinigte Konfiguration (API-Keys maskiert)
- Aktuelle Log-Einträge (letzte 100 Zeilen)

**Im Export maskiert:**
- Alle API-Keys durch `***REDACTED***` ersetzt
- Benutzerpfade wo möglich anonymisiert

## Sicherheits-Best-Practices

1. **Lokalen Modus für sensible Inhalte verwenden**
   ```bash
   PULSESCRIBE_MODE=local
   ```

2. **Auto-Paste in sensiblen Apps deaktivieren**
   - `--no-paste` Flag oder Nur-Clipboard-Modus verwenden

3. **Logs vor dem Teilen prüfen**
   - `~/.pulsescribe/logs/` auf sensible Inhalte überprüfen

4. **PulseScribe aktuell halten**
   - Sicherheitsfixes sind in Updates enthalten

5. **Gute API-Key-Hygiene**
   - Verschiedene Keys für verschiedene Zwecke
   - Regelmäßige Rotation
   - Nutzungs-Dashboards überwachen

## Sicherheitsprobleme melden

Für Sicherheitslücken bitte **kein** öffentliches GitHub-Issue öffnen.

Stattdessen die Maintainer direkt per E-Mail kontaktieren oder GitHubs private Vulnerability-Reporting-Funktion nutzen.

