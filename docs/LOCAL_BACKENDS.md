# Local Transcription Backends

[🇩🇪 Deutsche Version](LOKALE_BACKENDS.md)

PulseScribe supports offline transcription using local Whisper models. No API keys or internet connection required after initial model download.

## Backend Comparison

| Backend | Speed | Platform | GPU | Best For |
|---------|-------|----------|-----|----------|
| **lightning** | ⚡⚡⚡⚡ | Apple Silicon | Metal | Maximum speed (M1+) |
| **mlx** | ⚡⚡⚡ | Apple Silicon | Metal | Stability + speed |
| **faster** | ⚡⚡ | All | CPU only | CPU-only systems |
| **whisper** | ⚡ | All | MPS/CUDA | Compatibility |

`PULSESCRIBE_LOCAL_BACKEND=auto` (default) uses **mlx** on Apple Silicon and faster-whisper elsewhere (openai-whisper if faster-whisper is missing). The macOS app bundles mlx, lightning and faster-whisper; openai-whisper is only available when running from source. If the app is configured for a backend it does not bundle, it uses the default backend and logs a warning.

## Quick Start

### Apple Silicon (Recommended)

```bash
# Install MLX backend
pip install mlx-whisper

# Configure
export PULSESCRIBE_MODE=local
export PULSESCRIBE_LOCAL_BACKEND=mlx
export PULSESCRIBE_LOCAL_MODEL=turbo
export PULSESCRIBE_LANGUAGE=en

# Run
python pulsescribe_daemon.py
```

### CPU-Only Systems

```bash
# Install faster-whisper
pip install faster-whisper

# Configure
export PULSESCRIBE_MODE=local
export PULSESCRIBE_LOCAL_BACKEND=faster
export PULSESCRIBE_LOCAL_MODEL=turbo

# Run
python pulsescribe_daemon.py
```

---

## Backend Details

### Lightning (`lightning-whisper-mlx`)

**~4x faster** than standard MLX via batched decoding.

```bash
PULSESCRIBE_LOCAL_BACKEND=lightning
```

| Variable | Values | Default | Description |
|----------|--------|---------|-------------|
| `PULSESCRIBE_LIGHTNING_BATCH_SIZE` | 6-24 | 12 | Higher = faster, more RAM |
| `PULSESCRIBE_LIGHTNING_QUANT` | `4bit`, `8bit`, (empty) | (none) | Quantization for memory savings |

**Supported models:** `tiny`, `base`, `small`, `medium`, `large`, `large-v2`, `large-v3`

> **Note:** `turbo` is automatically mapped to `large-v3` (turbo not available in Lightning).

**Automatic fallback:** If Lightning fails, PulseScribe falls back to MLX automatically.

---

### MLX (`mlx-whisper`)

Native Metal acceleration for Apple Silicon. Good balance of speed and stability.

```bash
pip install mlx-whisper
PULSESCRIBE_LOCAL_BACKEND=mlx
```

**Quantization:** `PULSESCRIBE_MLX_QUANT` = `8bit` (default), `4bit` or `none`; in Settings → Providers as "Quantization" while MLX is active. 8-bit loads `large`/`large-v3` as a pre-quantized repo (1.6 GB instead of 2.9 GB) and quantizes other models after loading. Measured on an M6 with German dictation: turbo 8-bit as accurate as fp16, about 10 % faster, 0.8 GB instead of 1.5 GB memory; 4-bit is smaller but slightly less accurate.

**Live preview:** While you speak, the overlay shows the text recognized so far. PulseScribe transcribes the running recording again about every 0.7 s; the last word appears once the next one follows or you pause. The final transcript is still one pass over the whole recording, so its text is the same as without the preview. Recordings longer than 20 s preview only their newest 10 to 20 s.

- A pause of about one second before you stop makes the transcript available at once: the pause already ran the final pass. This applies to recordings up to 20 s in a quiet room. With background noise close to the level of quiet speech, PulseScribe cannot tell the pause from the noise: the last word then appears 0.7 s after you stop speaking, and the transcript comes at the usual time. Noise louder than a tenth of your loudest speech counts as speech, so the last word stays hidden until you stop.
- Stopping in the middle of a preview pass delays the transcript by the rest of that pass. Passes start only while you speak, so this happens mostly when you stop within 0.1 s of your last word.
- Measured on an M6 with turbo and German recordings, 28 runs per stop time, at a loud and at a quiet microphone level (loudest moment 0.012 RMS): a preview pass takes 0.3 to 0.4 s. Median waiting time after the stop was 0.4 s without the preview. With it: 0.04 s when stopping 1 s after the last word, 0.3 s after 0.45 s and 0.4 s after 0.1 s, where about one recording in five waited up to 0.3 s longer than without the preview.
- The GPU works for roughly half of the time you speak (a 0.3 s pass every 0.7 s).
- If a pass takes longer than 1 s, the preview stops for that recording. Other backends are too slow for it.
- No live text? With the preview running, `~/.pulsescribe/logs/pulsescribe.log` has one `Live-Vorschau:` line per recording that says how many passes ran or why none did.
- Turn it off in Settings → Providers → Advanced backend options → Live preview, or with `PULSESCRIBE_LOCAL_PREVIEW=false`.

**Model name mapping:**

| Short Name | Full Repo ID |
|------------|--------------|
| `turbo` | `mlx-community/whisper-large-v3-turbo` ⭐ |
| `large` | `mlx-community/whisper-large-v3-mlx` (8-bit: `mlx-community/whisper-large-v3-mlx-8bit`) |
| `medium` | `mlx-community/whisper-medium` |
| `small` | `mlx-community/whisper-small-mlx` |
| `base` | `mlx-community/whisper-base-mlx` |
| `tiny` | `mlx-community/whisper-tiny` |

**English-only (distilled, 30-40% faster):**

| Short Name | Full Repo ID |
|------------|--------------|
| `large-en` | `mlx-community/distil-whisper-large-v3` |
| `medium-en` | `mlx-community/distil-whisper-medium.en` |
| `small-en` | `mlx-community/distil-whisper-small.en` |

> **Warning:** `-en` models only support English. Use `turbo` or `large` for German/other languages.

**Limitations:**
- `PULSESCRIBE_LOCAL_BEAM_SIZE` is ignored (beam search not implemented)

---

### Faster-Whisper (`faster-whisper`)

CTranslate2-based backend. Very fast on CPU, lower memory usage.

```bash
pip install faster-whisper
PULSESCRIBE_LOCAL_BACKEND=faster
```

| Variable | Values | Default | Description |
|----------|--------|---------|-------------|
| `PULSESCRIBE_LOCAL_COMPUTE_TYPE` | `int8`, `float16`, `float32` | `int8` (CPU) | Compute precision |
| `PULSESCRIBE_LOCAL_CPU_THREADS` | 0-N | 0 (auto) | CPU threads (0 = all cores) |
| `PULSESCRIBE_LOCAL_NUM_WORKERS` | 1-N | 1 | Parallel workers |
| `PULSESCRIBE_LOCAL_WITHOUT_TIMESTAMPS` | `true`, `false` | `true` | Disable timestamps |
| `PULSESCRIBE_LOCAL_VAD_FILTER` | `true`, `false` | `false` | Voice activity detection |

**Notes:**
- On macOS, runs CPU-only (no Metal/MPS support)
- Model name `turbo` maps to `large-v3-turbo`
- Default `compute_type` is `float16` on CUDA

---

### OpenAI Whisper (`openai-whisper`)

Original PyTorch implementation. Best compatibility, uses MPS on Apple Silicon. Not included in the macOS app; run from source to use it.

```bash
pip install openai-whisper
PULSESCRIBE_LOCAL_BACKEND=whisper
```

| Variable | Values | Default | Description |
|----------|--------|---------|-------------|
| `PULSESCRIBE_DEVICE` | `auto`, `mps`, `cpu`, `cuda` | `auto` | Compute device |
| `PULSESCRIBE_FP16` | `true`, `false` | Auto | Force FP16 precision |

**Auto device selection:**
- Apple Silicon → MPS
- NVIDIA GPU → CUDA
- Otherwise → CPU

---

## Performance Tuning

Settings → Providers → Advanced backend options only show the options the active backend uses (for `auto`, the backend it resolves to); hover an option for what it does and its default:

| Option | Applies to |
|--------|------------|
| Preset, Warmup | all local backends |
| Unload when idle | all local backends (macOS app) |
| Live preview | MLX (macOS app) |
| Fast, Best of, Temperature | openai-whisper, faster-whisper, MLX (Lightning only reads the language) |
| Beam size | openai-whisper, faster-whisper (MLX and Lightning have no beam search) |
| FP16 | openai-whisper, MLX (faster-whisper uses the compute type) |
| Device | openai-whisper; faster-whisper on Windows (MLX and Lightning always use Metal) |
| Compute type, CPU threads, Workers, No timestamps, VAD filter | faster-whisper |
| Batch size, Quantization (Lightning) | Lightning |

Typed values are checked on save (for example Beam size 1-20, Temperature 0.0-1.0 with a dot as decimal separator); an invalid value blocks saving with a message. **Reset to defaults** clears all advanced options; backend and model stay.

### Fast Decoding

Enable for speed (slight robustness trade-off):

```bash
PULSESCRIBE_LOCAL_FAST=true
# Equivalent to:
PULSESCRIBE_LOCAL_BEAM_SIZE=1
PULSESCRIBE_LOCAL_BEST_OF=1
PULSESCRIBE_LOCAL_TEMPERATURE=0.0
```

### Fine-Tuning Parameters

| Variable | Range | Default | Description |
|----------|-------|---------|-------------|
| `PULSESCRIBE_LOCAL_BEAM_SIZE` | 1-10 | 1 | Beam search width |
| `PULSESCRIBE_LOCAL_BEST_OF` | 1-10 | 1 | Candidates per beam |
| `PULSESCRIBE_LOCAL_TEMPERATURE` | 0.0-1.0 | 0.0 | Sampling temperature |

> **Note:** Higher values = better quality, slower speed.

### Warmup

Reduce first-use latency by preloading the model:

```bash
PULSESCRIBE_LOCAL_WARMUP=true   # Always warmup
PULSESCRIBE_LOCAL_WARMUP=false  # Never warmup
PULSESCRIBE_LOCAL_WARMUP=auto   # Default: warmup for openai-whisper on MPS
```

---

## Model Sizes

| Model | Parameters | VRAM | Speed | Quality |
|-------|------------|------|-------|---------|
| `tiny` | 39M | ~1 GB | ⚡⚡⚡⚡ | ★★☆☆☆ |
| `base` | 74M | ~1 GB | ⚡⚡⚡ | ★★★☆☆ |
| `small` | 244M | ~2 GB | ⚡⚡ | ★★★☆☆ |
| `medium` | 769M | ~5 GB | ⚡ | ★★★★☆ |
| `large` | 1550M | ~10 GB | 🐢 | ★★★★★ |
| `turbo` | 809M | ~6 GB | ⚡⚡ | ★★★★☆ ⭐ |

⭐ **Recommended:** `turbo` for best speed/quality balance.

---

## Model Cache Locations

| Backend | Cache Path |
|---------|------------|
| `whisper` | `~/.cache/whisper/` |
| `faster-whisper` | `~/.cache/huggingface/` |
| `mlx-whisper` | `~/.cache/huggingface/` |
| `lightning` | `~/.pulsescribe/lightning_models/` |

The first load of a model downloads it. For mlx-whisper and faster-whisper the menu bar (macOS) or tray (Windows) shows the progress, e.g. "43 % · downloading turbo (1.6 GB)"; Lightning and openai-whisper only show "Loading…".

**Disk usage:** 75 MB (tiny) to 3 GB (large) per model.

---

## System Requirements

### Dependencies

```bash
# macOS: Required for all local backends
brew install ffmpeg portaudio

# Ubuntu/Debian
sudo apt install ffmpeg
```

> **Note:** `ffmpeg` is only needed for file transcription, not live microphone recording.

### Backend-Specific

| Backend | Install Command |
|---------|-----------------|
| `whisper` | `pip install openai-whisper` |
| `faster` | `pip install faster-whisper` |
| `mlx` | `pip install mlx-whisper` |
| `lightning` | `pip install lightning-whisper-mlx` |

---

## Windows GPU Support

The Windows installer (`-Local` variant) runs on **CPU by default**. For NVIDIA GPU acceleration:

### Option 1: Run from Source (Recommended)

```powershell
# Clone and install
git clone https://github.com/KLIEBHAN/pulsescribe.git
cd pulsescribe
pip install -r requirements.txt
pip install faster-whisper nvidia-cudnn-cu12

# Run
python pulsescribe_windows.py
```

### Option 2: System-wide cuDNN Installation

1. Download cuDNN from [NVIDIA cuDNN](https://developer.nvidia.com/cudnn) (requires NVIDIA account)
2. Extract and copy DLLs to `C:\Windows\System32\` or add to PATH
3. Run the bundled PulseScribe installer

### Verifying GPU Support

```powershell
python -c "import torch; print(f'CUDA available: {torch.cuda.is_available()}')"
python -c "import ctranslate2; print(f'CUDA devices: {ctranslate2.get_cuda_device_count()}')"
```

### Why CPU-only in the Installer?

The bundled EXE cannot locate cuDNN DLLs installed via pip (they're in `site-packages/nvidia/*/bin/`). Including these DLLs would add ~2GB to the installer and may have NVIDIA licensing implications.

**CPU Performance is Good:**

| Model | CPU (8-core) | GPU (RTX 3080) |
|-------|--------------|----------------|
| `turbo` | 5–10 sec for 10 sec audio | ~1 sec for 10 sec audio |
| `medium` | 20–30 sec for 10 sec audio | ~2 sec for 10 sec audio |

> **Note:** GPU is ~10× faster, but CPU performance is sufficient for most use cases.

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| `ModuleNotFoundError: No module named 'mlx'` | MLX is Apple Silicon only. Use the `faster` backend on Intel/Windows. |
| Model download 404 | Use short name (`large`) or full repo ID |
| `beam_size not implemented (mlx)` | Remove `PULSESCRIBE_LOCAL_BEAM_SIZE` |
| Slow first transcription | Enable `PULSESCRIBE_LOCAL_WARMUP=true` |
| Out of memory | Use smaller model or `PULSESCRIBE_LIGHTNING_QUANT=4bit` |
| `Read-only file system` (DMG) | Models stored in `~/.pulsescribe/lightning_models/` |
| CUDA not detected (Windows) | Install cuDNN system-wide or run from source |
| First transcription very slow | Model is downloading (~1.5 GB), wait for completion |

---

## Example Configurations

### Maximum Speed (Apple Silicon)

```bash
PULSESCRIBE_MODE=local
PULSESCRIBE_LOCAL_BACKEND=lightning
PULSESCRIBE_LOCAL_MODEL=turbo
PULSESCRIBE_LOCAL_FAST=true
PULSESCRIBE_LIGHTNING_BATCH_SIZE=16
```

### Low Memory (Apple Silicon)

```bash
PULSESCRIBE_MODE=local
PULSESCRIBE_LOCAL_BACKEND=lightning
PULSESCRIBE_LOCAL_MODEL=small
PULSESCRIBE_LIGHTNING_QUANT=4bit
```

### CPU Server

```bash
PULSESCRIBE_MODE=local
PULSESCRIBE_LOCAL_BACKEND=faster
PULSESCRIBE_LOCAL_MODEL=medium
PULSESCRIBE_LOCAL_COMPUTE_TYPE=int8
PULSESCRIBE_LOCAL_CPU_THREADS=8
```

