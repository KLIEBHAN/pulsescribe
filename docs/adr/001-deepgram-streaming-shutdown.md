# ADR-001: Deepgram Streaming Shutdown Optimierung

**Status:** Accepted
**Datum:** 2024-12-08
**Kontext:** Performance-Optimierung der Streaming-Pipeline

## Problem

Nach dem Beenden einer Aufnahme dauerte es 5-10 Sekunden, bis das Transkript verfügbar war. Die Analyse der Logs zeigte:

```
18:29:22 Finale Transkripte empfangen
18:29:32 Streaming abgeschlossen  ← 10 Sekunden Verzögerung!
```

## Ursachenanalyse

### 1. Der Deepgram SDK Context Manager

Das Deepgram Python SDK (v5.3.0) verwendet intern einen `async with` Context Manager:

```python
# deepgram/listen/v1/client.py, Zeile 423
async with websockets_client_connect(ws_url, extra_headers=headers) as protocol:
    yield AsyncV1SocketClient(websocket=protocol)
```

### 2. Das websockets-Library Verhalten

Der `__aexit__` von `websockets.connect()` wartet auf einen sauberen WebSocket Close-Handshake:

- Client sendet Close-Frame
- Server bestätigt mit Close-Frame
- Wenn Server nicht antwortet: Timeout (bis zu 10s)

### 3. Deepgram Control Messages

Das Deepgram Protokoll unterstützt zwei relevante Control Messages:

| Message       | Zweck                                                                                    |
| ------------- | ---------------------------------------------------------------------------------------- |
| `Finalize`    | Server verarbeitet gepuffertes Audio, sendet finale Transkripte mit `from_finalize=True` |
| `CloseStream` | Server schließt Verbindung sofort                                                        |

## Offizieller Weg vs. Unsere Optimierung

### Was die Deepgram Docs empfehlen

Laut [offizieller Dokumentation](https://developers.deepgram.com/docs/close-stream) reicht **CloseStream alleine**:

> "Use the CloseStream message to close the WebSocket stream. This forces the server to
> immediately process any unprocessed audio data and return the final transcription results."

### Warum wir trotzdem Finalize + CloseStream nutzen

Unsere Tests zeigten: CloseStream alleine ist **langsamer** (~3s vs. ~1s), weil:

1. CloseStream triggert Verarbeitung UND wartet auf Server-Close
2. Finalize triggert Verarbeitung und gibt uns ein **Signal** (`from_finalize=True`)
3. Mit diesem Signal können wir sofort CloseStream senden, ohne auf Verarbeitung zu warten

**Beide Ansätze sind API-konform** - wir nutzen nur ein zusätzliches Feature für bessere Performance.

## Entscheidung

Wir senden **explizit Finalize + CloseStream** bevor der Context Manager endet:

```python
# 1. Finalize: Letzte Transkripte holen
await connection.send_control(ListenV1ControlMessage(type="Finalize"))
await asyncio.wait_for(finalize_done.wait(), timeout=2.0)

# 2. CloseStream: Verbindung sofort beenden
await connection.send_control(ListenV1ControlMessage(type="CloseStream"))
```

### Warum beide Messages?

- **Nur Finalize:** Verbindung bleibt offen, `__aexit__` wartet auf Timeout
- **Nur CloseStream:** Funktioniert, aber ~3s langsamer (Server verarbeitet erst, dann Close)
- **Finalize + CloseStream:** Schnellste Option (~2s statt ~10s)

## Ergebnis

| Metrik                   | Vorher | Nachher | Verbesserung   |
| ------------------------ | ------ | ------- | -------------- |
| Stop → Transkript fertig | 5-10s  | ~2s     | ~70% schneller |
| Gesamt-Pipeline          | 15-22s | 7-8s    | ~60% schneller |

## Update: Saubere Lösung (Dezember 2024)

Die ursprüngliche Lösung (Finalize + CloseStream) reduzierte die Latenz von 10s auf ~2-5s.
Aber der `__aexit__` des SDK Context Managers blockierte immer noch.

### Das eigentliche Problem

Das Deepgram SDK leitet den `close_timeout` Parameter **nicht** an `websockets.connect()` weiter:

```python
# SDK-Code (deepgram/listen/v1/client.py)
async with websockets_client_connect(ws_url, extra_headers=headers) as protocol:
    yield AsyncV1SocketClient(websocket=protocol)
# ↑ Kein close_timeout → Default 10s!
```

### Erste Lösung: Monkey-Patch (verworfen)

```python
# Funktioniert, aber nutzt private API
if hasattr(connection, "_websocket"):
    connection._websocket.close_timeout = 0.5
```

**Probleme:**

- `_websocket` ist private API (kann sich ändern)
- Keine Warnung, wenn Patch nicht mehr greift
- Code-Smell

### Finale Lösung: Eigener Context Manager

Wir umgehen den SDK Context Manager und verbinden direkt mit `websockets.connect()`:

```python
@asynccontextmanager
async def _create_deepgram_connection(api_key, *, model, ...):
    # Query-Parameter aufbauen (wie SDK)
    params = httpx.QueryParams()
    params = params.add("model", model)
    # ...

    async with websockets_connect(
        ws_url,
        extra_headers={"Authorization": f"Token {api_key}"},
        close_timeout=0.5,  # ← Der Schlüssel!
    ) as protocol:
        yield AsyncV1SocketClient(websocket=protocol)
```

**Vorteile:**

- Keine private API (`_websocket`)
- Explizit dokumentiert und nachvollziehbar
- SDK-Update-resistent (nutzt nur öffentliche `AsyncV1SocketClient` Klasse)
- `close_timeout` direkt konfigurierbar

### Ergebnis nach Update

| Metrik                   | Original | Mit Finalize | Mit eigenem CM |
| ------------------------ | -------- | ------------ | -------------- |
| Stop → Transkript fertig | 5-10s    | ~2-5s        | **<1s**        |
| Gesamt-Pipeline          | 15-22s   | 7-11s        | **~6s**        |

## Lessons Learned

1. **Logging ist essentiell:** Ohne detaillierte Timestamps hätten wir den Flaschenhals nicht gefunden
2. **SDK-Internals verstehen:** Das "SDK-Problem" war eigentlich websockets-Library-Verhalten
3. **Protokoll-Features nutzen:** Finalize + CloseStream sind dokumentiert, aber nicht offensichtlich

## Referenzen

- [Deepgram Finalize Docs](https://developers.deepgram.com/docs/finalize) - Flush stream, Verbindung bleibt offen
- [Deepgram CloseStream Docs](https://developers.deepgram.com/docs/close-stream) - Offizieller Shutdown-Weg
- [Deepgram Python SDK v5.3.0](https://github.com/deepgram/deepgram-python-sdk)
- [websockets Library](https://websockets.readthedocs.io/)
- PR #20: `perf/deepgram-closestream`
