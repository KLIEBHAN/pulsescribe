# Netzwerk-Anforderungen

[🇺🇸 English Version](NETWORK.md)

Dieses Dokument beschreibt die Netzwerk-Anforderungen für PulseScribe, einschließlich erforderlicher Endpunkte, Proxy-Konfiguration und Details zum Offline-Modus.

## Erforderliche Endpunkte

### Transkriptions-Provider

| Provider | Endpunkt | Port | Protokoll |
|----------|----------|------|-----------|
| **Deepgram** | `api.deepgram.com` | 443 | HTTPS / WSS |
| **OpenAI** | `api.openai.com` | 443 | HTTPS |
| **Groq** | `api.groq.com` | 443 | HTTPS |

### LLM-Refine-Provider

| Provider | Endpunkt | Port | Protokoll |
|----------|----------|------|-----------|
| **OpenAI** | `api.openai.com` | 443 | HTTPS |
| **Groq** | `api.groq.com` | 443 | HTTPS |
| **OpenRouter** | `openrouter.ai` | 443 | HTTPS |

### Modell-Downloads (Lokaler Modus)

| Backend | Endpunkt | Zweck |
|---------|----------|-------|
| **Whisper/MLX** | `huggingface.co` | Download der Modell-Gewichte |
| **Lightning** | `huggingface.co` | Download der Modell-Gewichte |

## Firewall-Konfiguration

### Minimum erforderlich (Cloud-Modus)

Ausgehende HTTPS-Verbindungen (Port 443) erlauben zu:
```
api.deepgram.com
api.openai.com
api.groq.com
openrouter.ai
```

### Für lokalen Modus (Setup)

Zusätzlich erlauben:
```
huggingface.co
*.hf.co
```

> **Hinweis:** Nach dem initialen Modell-Download funktioniert der lokale Modus komplett offline.

## Proxy-Konfiguration

PulseScribe respektiert Standard-Umgebungsvariablen für Proxy-Konfiguration:

```bash
# HTTP/HTTPS Proxy
export HTTP_PROXY=http://proxy.example.com:8080
export HTTPS_PROXY=http://proxy.example.com:8080

# Kein Proxy für bestimmte Hosts
export NO_PROXY=localhost,127.0.0.1
```

### WebSocket-Proxy (Deepgram-Streaming)

Für Deepgrams WebSocket-Streaming muss dein Proxy unterstützen:
- WebSocket-Upgrade (HTTP 101)
- WSS (WebSocket Secure) Verbindungen

Falls WebSocket-Proxying fehlschlägt, fällt PulseScribe automatisch auf REST-API zurück:
```bash
PULSESCRIBE_STREAMING=false
```

## Offline-Modus

### Vollständiger Offline-Betrieb

Mit `PULSESCRIBE_MODE=local` funktioniert PulseScribe **nach initialem Setup** komplett offline:

```bash
# Einmaliges Setup (erfordert Internet)
pip install -r requirements.txt
export PULSESCRIBE_MODE=local
export PULSESCRIBE_LOCAL_BACKEND=mlx  # oder: whisper, faster, lightning
python pulsescribe_daemon.py  # Lädt Modell beim ersten Start herunter

# Nach Modell-Download: funktioniert offline
```

### Modell-Cache-Speicherorte

| Backend | Cache-Speicherort |
|---------|-------------------|
| **whisper** | `~/.cache/whisper/` |
| **faster-whisper** | `~/.cache/huggingface/` |
| **mlx-whisper** | `~/.cache/huggingface/` |
| **lightning** | `~/.pulsescribe/lightning_models/` |

### Offline-Einschränkungen

- **LLM-Refine:** Benötigt Netzwerk (noch keine lokale LLM-Unterstützung)
- **Modell-Downloads:** Erster Start erfordert Internet
- **Custom Vocabulary:** Funktioniert offline (lokal gespeichert)

## Verbindungsverhalten

### Deepgram-Streaming

| Szenario | Verhalten |
|----------|-----------|
| Verbindung während Aufnahme verloren | Automatischer Wiederverbindungsversuch |
| Server-Timeout | Fallback auf REST-API |
| DNS-Fehler | Fehler angezeigt, Aufnahme gestoppt |

### Timeouts

| Operation | Timeout |
|-----------|---------|
| WebSocket-Verbindung | 10 Sekunden |
| REST-API-Aufruf | 30 Sekunden |
| Modell-Download | Kein Timeout (Fortschritt angezeigt) |

## Bandbreitennutzung

### Typische Nutzung (Deepgram-Streaming)

| Audio-Dauer | Upload | Download |
|-------------|--------|----------|
| 10 Sekunden | ~160 KB | ~5 KB |
| 1 Minute | ~960 KB | ~10 KB |
| 5 Minuten | ~4,8 MB | ~20 KB |

> Audio wird mit 16kHz Mono, 16-Bit PCM gestreamt (~32 KB/Sek).

### Modell-Download-Größen

| Modell | Größe |
|--------|-------|
| tiny | ~75 MB |
| base | ~150 MB |
| small | ~500 MB |
| medium | ~1,5 GB |
| large/large-v3 | ~3 GB |
| turbo | ~1,5 GB |

## Fehlerbehebung

### Verbindungsprobleme

| Problem | Lösung |
|---------|--------|
| `Connection refused` | Firewall prüfen, Endpunkt-Erreichbarkeit verifizieren |
| `SSL certificate error` | CA-Zertifikate aktualisieren, Systemzeit prüfen |
| `Timeout` | Proxy-Einstellungen prüfen, REST-Fallback versuchen |
| `DNS resolution failed` | Netzwerkverbindung prüfen |

### Konnektivität testen

```bash
# Deepgram testen
curl -I https://api.deepgram.com

# OpenAI testen
curl -I https://api.openai.com

# Groq testen
curl -I https://api.groq.com

# Mit Proxy testen
curl -I --proxy http://proxy:8080 https://api.deepgram.com
```

### Diagnose-Logs

Netzwerkprobleme werden in `~/.pulsescribe/logs/pulsescribe.log` protokolliert:

```bash
# Aktuelle Netzwerkfehler anzeigen
grep -i "connection\|timeout\|error" ~/.pulsescribe/logs/pulsescribe.log | tail -20
```

