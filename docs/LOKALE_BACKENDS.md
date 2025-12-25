# Lokale Transkriptions-Backends

[🇺🇸 English Version](LOCAL_BACKENDS.md)

PulseScribe unterstützt Offline-Transkription mit lokalen Whisper-Modellen. Nach dem initialen Modell-Download sind keine API-Keys oder Internetverbindung erforderlich.

## Backend-Vergleich

| Backend | Geschwindigkeit | Plattform | GPU | Ideal für |
|---------|-----------------|-----------|-----|-----------|
| **lightning** | ⚡⚡⚡⚡ | Apple Silicon | Metal | Maximale Geschwindigkeit (M1+) |
| **mlx** | ⚡⚡⚡ | Apple Silicon | Metal | Stabilität + Geschwindigkeit |
| **faster** | ⚡⚡ | Alle | Nur CPU | CPU-only Systeme |
| **whisper** | ⚡ | Alle | MPS/CUDA | Kompatibilität |

## Schnellstart

### Apple Silicon (Empfohlen)

```bash
# MLX Backend installieren
pip install mlx-whisper

# Konfigurieren
export PULSESCRIBE_MODE=local
export PULSESCRIBE_LOCAL_BACKEND=mlx
export PULSESCRIBE_LOCAL_MODEL=turbo
export PULSESCRIBE_LANGUAGE=de

# Starten
python pulsescribe_daemon.py
```

### CPU-only Systeme

```bash
# faster-whisper installieren
pip install faster-whisper

# Konfigurieren
export PULSESCRIBE_MODE=local
export PULSESCRIBE_LOCAL_BACKEND=faster
export PULSESCRIBE_LOCAL_MODEL=turbo

# Starten
python pulsescribe_daemon.py
```

---

## Backend-Details

### Lightning (`lightning-whisper-mlx`)

**~4x schneller** als Standard-MLX durch Batched Decoding.

```bash
PULSESCRIBE_LOCAL_BACKEND=lightning
```

| Variable | Werte | Default | Beschreibung |
|----------|-------|---------|--------------|
| `PULSESCRIBE_LIGHTNING_BATCH_SIZE` | 6-24 | 12 | Höher = schneller, mehr RAM |
| `PULSESCRIBE_LIGHTNING_QUANT` | `4bit`, `8bit`, (leer) | (keiner) | Quantisierung für Speichereinsparung |

**Unterstützte Modelle:** `tiny`, `base`, `small`, `medium`, `large`, `large-v2`, `large-v3`

> **Hinweis:** `turbo` wird automatisch auf `large-v3` gemappt (turbo nicht in Lightning verfügbar).

**Automatischer Fallback:** Bei Lightning-Fehlern fällt PulseScribe automatisch auf MLX zurück.

---

### MLX (`mlx-whisper`)

Native Metal-Beschleunigung für Apple Silicon. Gute Balance aus Geschwindigkeit und Stabilität.

```bash
pip install mlx-whisper
PULSESCRIBE_LOCAL_BACKEND=mlx
```

**Modellnamen-Zuordnung:**

| Kurzname | Vollständige Repo-ID |
|----------|---------------------|
| `turbo` | `mlx-community/whisper-large-v3-turbo` ⭐ |
| `large` | `mlx-community/whisper-large-v3-mlx` |
| `medium` | `mlx-community/whisper-medium` |
| `small` | `mlx-community/whisper-small-mlx` |
| `base` | `mlx-community/whisper-base-mlx` |
| `tiny` | `mlx-community/whisper-tiny` |

**Nur Englisch (destilliert, 30-40% schneller):**

| Kurzname | Vollständige Repo-ID |
|----------|---------------------|
| `large-en` | `mlx-community/distil-whisper-large-v3` |
| `medium-en` | `mlx-community/distil-whisper-medium.en` |
| `small-en` | `mlx-community/distil-whisper-small.en` |

> **Warnung:** `-en` Modelle unterstützen nur Englisch. Für Deutsch/andere Sprachen `turbo` oder `large` verwenden.

**Einschränkungen:**
- `PULSESCRIBE_LOCAL_BEAM_SIZE` wird ignoriert (Beam Search nicht implementiert)

---

### Faster-Whisper (`faster-whisper`)

CTranslate2-basiertes Backend. Sehr schnell auf CPU, geringerer Speicherbedarf.

```bash
pip install faster-whisper
PULSESCRIBE_LOCAL_BACKEND=faster
```

| Variable | Werte | Default | Beschreibung |
|----------|-------|---------|--------------|
| `PULSESCRIBE_LOCAL_COMPUTE_TYPE` | `int8`, `float16`, `float32` | `int8` (CPU) | Rechengenauigkeit |
| `PULSESCRIBE_LOCAL_CPU_THREADS` | 0-N | 0 (auto) | CPU-Threads (0 = alle Kerne) |
| `PULSESCRIBE_LOCAL_NUM_WORKERS` | 1-N | 1 | Parallele Worker |
| `PULSESCRIBE_LOCAL_WITHOUT_TIMESTAMPS` | `true`, `false` | `true` | Timestamps deaktivieren |
| `PULSESCRIBE_LOCAL_VAD_FILTER` | `true`, `false` | `false` | Voice Activity Detection |

**Hinweise:**
- Auf macOS nur CPU (keine Metal/MPS-Unterstützung)
- Modellname `turbo` wird zu `large-v3-turbo` gemappt
- Standard-`compute_type` ist `float16` auf CUDA

---

### OpenAI Whisper (`openai-whisper`)

Originale PyTorch-Implementierung. Beste Kompatibilität, nutzt MPS auf Apple Silicon.

```bash
pip install openai-whisper
PULSESCRIBE_LOCAL_BACKEND=whisper
```

| Variable | Werte | Default | Beschreibung |
|----------|-------|---------|--------------|
| `PULSESCRIBE_DEVICE` | `auto`, `mps`, `cpu`, `cuda` | `auto` | Rechengerät |
| `PULSESCRIBE_FP16` | `true`, `false` | Auto | FP16-Genauigkeit erzwingen |

**Automatische Gerätewahl:**
- Apple Silicon → MPS
- NVIDIA GPU → CUDA
- Sonst → CPU

---

## Performance-Tuning

### Schnelles Decoding

Für Geschwindigkeit aktivieren (leichter Robustheitsverlust):

```bash
PULSESCRIBE_LOCAL_FAST=true
# Entspricht:
PULSESCRIBE_LOCAL_BEAM_SIZE=1
PULSESCRIBE_LOCAL_BEST_OF=1
PULSESCRIBE_LOCAL_TEMPERATURE=0.0
```

### Feintuning-Parameter

| Variable | Bereich | Default | Beschreibung |
|----------|---------|---------|--------------|
| `PULSESCRIBE_LOCAL_BEAM_SIZE` | 1-10 | 1 | Beam-Search-Breite |
| `PULSESCRIBE_LOCAL_BEST_OF` | 1-10 | 1 | Kandidaten pro Beam |
| `PULSESCRIBE_LOCAL_TEMPERATURE` | 0.0-1.0 | 0.0 | Sampling-Temperatur |

> **Hinweis:** Höhere Werte = bessere Qualität, langsamere Geschwindigkeit.

### Warmup

Erste-Nutzung-Latenz durch Modell-Vorladung reduzieren:

```bash
PULSESCRIBE_LOCAL_WARMUP=true   # Immer Warmup
PULSESCRIBE_LOCAL_WARMUP=false  # Nie Warmup
PULSESCRIBE_LOCAL_WARMUP=auto   # Default: Warmup für openai-whisper auf MPS
```

---

## Modellgrößen

| Modell | Parameter | VRAM | Geschwindigkeit | Qualität |
|--------|-----------|------|-----------------|----------|
| `tiny` | 39M | ~1 GB | ⚡⚡⚡⚡ | ★★☆☆☆ |
| `base` | 74M | ~1 GB | ⚡⚡⚡ | ★★★☆☆ |
| `small` | 244M | ~2 GB | ⚡⚡ | ★★★☆☆ |
| `medium` | 769M | ~5 GB | ⚡ | ★★★★☆ |
| `large` | 1550M | ~10 GB | 🐢 | ★★★★★ |
| `turbo` | 809M | ~6 GB | ⚡⚡ | ★★★★☆ ⭐ |

⭐ **Empfohlen:** `turbo` für beste Geschwindigkeit/Qualität-Balance.

---

## Modell-Cache-Speicherorte

| Backend | Cache-Pfad |
|---------|------------|
| `whisper` | `~/.cache/whisper/` |
| `faster-whisper` | `~/.cache/huggingface/` |
| `mlx-whisper` | `~/.cache/huggingface/` |
| `lightning` | `~/.pulsescribe/lightning_models/` |

**Festplattennutzung:** 75 MB (tiny) bis 3 GB (large) pro Modell.

---

## Systemvoraussetzungen

### Abhängigkeiten

```bash
# macOS: Erforderlich für alle lokalen Backends
brew install ffmpeg portaudio

# Ubuntu/Debian
sudo apt install ffmpeg
```

> **Hinweis:** `ffmpeg` wird nur für Datei-Transkription benötigt, nicht für Live-Mikrofon-Aufnahme.

### Backend-spezifisch

| Backend | Installationsbefehl |
|---------|---------------------|
| `whisper` | `pip install openai-whisper` |
| `faster` | `pip install faster-whisper` |
| `mlx` | `pip install mlx-whisper` |
| `lightning` | `pip install lightning-whisper-mlx` |

---

## Fehlerbehebung

| Problem | Lösung |
|---------|--------|
| `ModuleNotFoundError: No module named 'mlx'` | Nur Apple Silicon. Auf Intel `faster` verwenden. |
| Modell-Download 404 | Kurznamen (`large`) oder vollständige Repo-ID verwenden |
| `beam_size not implemented (mlx)` | `PULSESCRIBE_LOCAL_BEAM_SIZE` entfernen |
| Langsame erste Transkription | `PULSESCRIBE_LOCAL_WARMUP=true` aktivieren |
| Speichermangel | Kleineres Modell oder `PULSESCRIBE_LIGHTNING_QUANT=4bit` |
| `Read-only file system` (DMG) | Modelle werden in `~/.pulsescribe/lightning_models/` gespeichert |

---

## Beispielkonfigurationen

### Maximale Geschwindigkeit (Apple Silicon)

```bash
PULSESCRIBE_MODE=local
PULSESCRIBE_LOCAL_BACKEND=lightning
PULSESCRIBE_LOCAL_MODEL=turbo
PULSESCRIBE_LOCAL_FAST=true
PULSESCRIBE_LIGHTNING_BATCH_SIZE=16
```

### Wenig Speicher (Apple Silicon)

```bash
PULSESCRIBE_MODE=local
PULSESCRIBE_LOCAL_BACKEND=lightning
PULSESCRIBE_LOCAL_MODEL=small
PULSESCRIBE_LIGHTNING_QUANT=4bit
```

### CPU-Server

```bash
PULSESCRIBE_MODE=local
PULSESCRIBE_LOCAL_BACKEND=faster
PULSESCRIBE_LOCAL_MODEL=medium
PULSESCRIBE_LOCAL_COMPUTE_TYPE=int8
PULSESCRIBE_LOCAL_CPU_THREADS=8
```

---

_Zuletzt aktualisiert: Dezember 2025_
