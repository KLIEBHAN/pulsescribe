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

`PULSESCRIBE_LOCAL_BACKEND=auto` (Standard) nutzt auf Apple Silicon **mlx**, sonst faster-whisper (openai-whisper, falls faster-whisper fehlt). Die macOS-App enthält mlx, lightning und faster-whisper; openai-whisper gibt es nur beim Start aus dem Quellcode. Ist in der App ein nicht enthaltenes Backend eingestellt, nutzt sie das Standard-Backend und schreibt eine Warnung ins Log.

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

**Quantisierung:** `PULSESCRIBE_MLX_QUANT` = `8bit` (Standard), `4bit` oder `none`; in den Einstellungen unter Providers als „Quantization“, solange MLX aktiv ist. Mit 8 Bit lädt PulseScribe `large`/`large-v3` als fertig quantisiertes Repo (1,6 statt 2,9 GB) und quantisiert andere Modelle nach dem Laden. Gemessen auf einem M6 mit deutschen Diktaten: turbo in 8 Bit so genau wie fp16, etwa 10 % schneller, 0,8 statt 1,5 GB Speicher; 4 Bit ist kleiner, aber etwas ungenauer.

**Live-Vorschau:** Während du sprichst, zeigt das Overlay den bisher erkannten Text. PulseScribe transkribiert die laufende Aufnahme etwa alle 0,7 s neu; das letzte Wort erscheint, sobald das nächste folgt oder du pausierst. Der endgültige Text bleibt ein Durchlauf über die ganze Aufnahme und ist derselbe wie ohne Vorschau. Aufnahmen über 20 s zeigen nur ihre neuesten 10 bis 20 s in der Vorschau.

- Eine Pause von etwa einer Sekunde vor dem Stoppen macht den Text sofort verfügbar: Die Pause hat den endgültigen Durchlauf bereits ausgelöst. Das gilt für Aufnahmen bis 20 s in ruhiger Umgebung. Liegt das Hintergrundgeräusch nahe am Pegel leiser Sprache, kann PulseScribe die Pause nicht vom Geräusch unterscheiden: Das letzte Wort erscheint dann 0,7 s nach dem Ende des Sprechens, und der Text kommt zur üblichen Zeit. Geräusch, das lauter ist als ein Zehntel deiner lautesten Sprache, zählt als Sprache; das letzte Wort bleibt dann bis zum Stopp verborgen.
- Stoppst du mitten in einem Vorschau-Durchlauf, verzögert sich der Text um den Rest dieses Durchlaufs. Durchläufe starten nur, solange du sprichst; das trifft also vor allem zu, wenn du innerhalb von 0,1 s nach dem letzten Wort stoppst.
- Gemessen auf einem M6 mit turbo und deutschen Aufnahmen, 28 Läufe je Stoppzeitpunkt, bei lautem und bei leisem Mikrofonpegel (lautester Moment 0,012 RMS): Ein Vorschau-Durchlauf dauert 0,3 bis 0,4 s. Die mittlere Wartezeit nach dem Stopp (Median) lag ohne Vorschau bei 0,4 s. Mit Vorschau: 0,04 s bei Stopp 1 s nach dem letzten Wort, 0,3 s nach 0,45 s und 0,4 s nach 0,1 s; dort wartete etwa jede fünfte Aufnahme bis zu 0,3 s länger als ohne Vorschau.
- Die GPU arbeitet etwa die Hälfte der Zeit, in der du sprichst (alle 0,7 s ein Durchlauf von 0,3 s).
- Dauert ein Durchlauf länger als 1 s, endet die Vorschau für diese Aufnahme. Andere Backends sind dafür zu langsam.
- Kein Live-Text? Läuft die Vorschau, steht in `~/.pulsescribe/logs/pulsescribe.log` je Aufnahme eine Zeile `Live-Vorschau:`, die zeigt, wie viele Durchläufe liefen oder warum keiner.
- Abschalten unter Einstellungen → Providers → Advanced backend options → Live preview oder mit `PULSESCRIBE_LOCAL_PREVIEW=false`.

**Modellnamen-Zuordnung:**

| Kurzname | Vollständige Repo-ID |
|----------|---------------------|
| `turbo` | `mlx-community/whisper-large-v3-turbo` ⭐ |
| `large` | `mlx-community/whisper-large-v3-mlx` (8 Bit: `mlx-community/whisper-large-v3-mlx-8bit`) |
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

Originale PyTorch-Implementierung. Beste Kompatibilität, nutzt MPS auf Apple Silicon. Nicht in der macOS-App enthalten; dafür PulseScribe aus dem Quellcode starten.

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

Einstellungen → Providers → Advanced backend options zeigen nur die Optionen, die das aktive Backend nutzt (bei `auto` das Backend, das daraus wird); der Tooltip erklärt jede Option und ihren Standard:

| Option | Wirkt bei |
|--------|-----------|
| Preset, Warmup | allen lokalen Backends |
| Unload when idle | allen lokalen Backends (macOS-App) |
| Live preview | MLX (macOS-App) |
| Fast, Best of, Temperature | openai-whisper, faster-whisper, MLX (Lightning liest nur die Sprache) |
| Beam size | openai-whisper, faster-whisper (MLX und Lightning haben kein Beam Search) |
| FP16 | openai-whisper, MLX (faster-whisper nutzt den Compute type) |
| Device | openai-whisper; faster-whisper unter Windows (MLX und Lightning laufen immer auf Metal) |
| Compute type, CPU threads, Workers, No timestamps, VAD filter | faster-whisper |
| Batch size, Quantization (Lightning) | Lightning |

Eingetippte Werte werden beim Speichern geprüft (etwa Beam size 1–20, Temperature 0.0–1.0 mit Punkt als Dezimaltrennzeichen); ein ungültiger Wert verhindert das Speichern mit Hinweis. **Reset to defaults** setzt alle erweiterten Optionen zurück; Backend und Modell bleiben.

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

Beim ersten Laden wird ein Modell heruntergeladen. Bei mlx-whisper und faster-whisper zeigt die Menüleiste (macOS) bzw. das Tray (Windows) den Fortschritt, z. B. „43 % · downloading turbo (1.6 GB)“; Lightning und openai-whisper zeigen nur „Loading…“.

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

## Windows GPU-Unterstützung

Der Windows-Installer (Variante `-Local`) läuft standardmäßig auf **CPU**. Für NVIDIA GPU-Beschleunigung:

### Option 1: Aus Quellcode starten (Empfohlen)

```powershell
# Klonen und installieren
git clone https://github.com/KLIEBHAN/pulsescribe.git
cd pulsescribe
pip install -r requirements.txt
pip install faster-whisper nvidia-cudnn-cu12

# Starten
python pulsescribe_windows.py
```

### Option 2: Systemweite cuDNN-Installation

1. cuDNN von [NVIDIA cuDNN](https://developer.nvidia.com/cudnn) herunterladen (NVIDIA-Account erforderlich)
2. DLLs nach `C:\Windows\System32\` kopieren oder zum PATH hinzufügen
3. PulseScribe-Installer starten

### GPU-Unterstützung prüfen

```powershell
python -c "import torch; print(f'CUDA verfügbar: {torch.cuda.is_available()}')"
python -c "import ctranslate2; print(f'CUDA-Geräte: {ctranslate2.get_cuda_device_count()}')"
```

### Warum nur CPU im Installer?

Die gebündelte EXE kann cuDNN-DLLs aus pip-Paketen nicht finden (sie liegen in `site-packages/nvidia/*/bin/`). Diese DLLs einzubinden würde ~2GB zum Installer hinzufügen und hat NVIDIA-Lizenzimplikationen.

**CPU-Performance ist gut:**

| Modell | CPU (8-Kern) | GPU (RTX 3080) |
|--------|--------------|----------------|
| `turbo` | 5–10 Sek. für 10 Sek. Audio | ~1 Sek. für 10 Sek. Audio |
| `medium` | 20–30 Sek. für 10 Sek. Audio | ~2 Sek. für 10 Sek. Audio |

> **Hinweis:** GPU ist ~10× schneller, aber CPU-Performance reicht für die meisten Anwendungsfälle.

---

## Fehlerbehebung

| Problem | Lösung |
|---------|--------|
| `ModuleNotFoundError: No module named 'mlx'` | MLX ist nur für Apple Silicon. Auf Intel/Windows das `faster`-Backend verwenden. |
| Modell-Download 404 | Kurznamen (`large`) oder vollständige Repo-ID verwenden |
| `beam_size not implemented (mlx)` | `PULSESCRIBE_LOCAL_BEAM_SIZE` entfernen |
| Langsame erste Transkription | `PULSESCRIBE_LOCAL_WARMUP=true` aktivieren |
| Speichermangel | Kleineres Modell oder `PULSESCRIBE_LIGHTNING_QUANT=4bit` |
| `Read-only file system` (DMG) | Modelle werden in `~/.pulsescribe/lightning_models/` gespeichert |
| CUDA nicht erkannt (Windows) | cuDNN systemweit installieren oder aus Quellcode starten |
| Erste Transkription sehr langsam | Modell wird heruntergeladen (~1.5 GB), auf Abschluss warten |

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

