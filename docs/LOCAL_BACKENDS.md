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

**macOS version:** the app carries two builds of MLX and uses the one made for your macOS. On macOS 26.2 and newer that build runs Whisper about twice as fast as the one for older systems (turbo on an M5: 0.34 s instead of 0.77 s for 2 s of audio, 0.46 s instead of 0.87 s for 12 s). The log names the build when the model loads (`MLX-Build: …`).

**Live preview:** MLX with a fast model shows your words while you speak; see [Live Preview](#live-preview).

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

## Live Preview

On macOS the overlay can show your words while you speak. The transcript itself is always one Whisper pass over the whole recording; the preview only shows text earlier. Choose the preview in Settings → Providers → Advanced backend options → Live preview, or with `PULSESCRIBE_LOCAL_PREVIEW`.

| Live preview | Text comes from | Works with | Behind your voice | Costs |
|--------------|-----------------|------------|-------------------|-------|
| `on` (default, `true`) | Whisper, transcribing the running recording again and again | MLX with a model other than large, large-v2 or large-v3 | 0.3 to 1.0 s on an M6, 0.8 to 2.4 s on an M5 (turbo) | GPU busy about half of the time you speak |
| `streaming model` (`streaming`) | a second model (NVIDIA Nemotron 3.5 ASR streaming via sherpa-onnx, on the CPU) | every local backend and model | about 0.6 s, at most 0.8 s (M5) | 0.7 GB download, about 1.9 GB memory while loaded, 0.3 CPU cores while you speak |
| `off` (`false`) | – | – | – | – |

The text fills two lines whose words keep their place; words shown before are bright, new ones dimmed until the next update confirms them. The delays for `on` are computed from measured pass durations, those for the streaming model are measured.

### Streaming model

- After you save the choice, PulseScribe downloads the model once (682 MB, progress in the menu bar) into the Hugging Face cache and loads it in the background. Until then Whisper previews as with `on`.
- Words appear in steps of about 0.55 s. Preview quality is lower than Whisper's: on German test recordings 8 to 11 % of the words were wrong (Whisper turbo: 5 %), numbers come as words, and the last word can be cut until its next syllable arrives. The pasted transcript is not affected.
- Measured on an M5 with two CPU threads: text 0.60 s behind the voice (median, 0.77 s at most), one step takes 74 ms. On an M6 the waiting time after the stop was the same as without a preview.
- With MLX and a model other than large, large-v2 or large-v3, Whisper also transcribes at speech pauses in the background, so the transcript is ready sooner when you stop after a pause, as with `on`. Measured on an M6 with turbo: 0.02 s instead of 0.40 s when stopping 1 s after the last word, 0.28 s instead of 0.42 s after 0.45 s. With Whisper passes slowed to an M5's 0.9 s: 0.26 s instead of 0.92 s after 1 s, 0.05 s after 1.6 s. A short remark right after a pause, stopped at once, can wait up to 0.4 s longer. With large models and other backends Whisper starts when you stop.
- The memory is freed with **Unload when idle**, when you choose another preview or leave local mode. The next recording loads the model again in under a second.
- Quiet speech is no longer lost: a recording whose level stays below the speech threshold is normally discarded as "no speech". If the streaming model recognized words in it, Whisper transcribes it. In a test with sentences at a loudest moment of 0.006 RMS (threshold 0.010), 8 of 8 were transcribed instead of none; noise, clicks and silence were still discarded. Speech of about two seconds or less is often too short for the model to recognize and is discarded as before.
- If the model falls 2 s behind the microphone, the preview ends for that recording and pauses for 30 minutes.
- The model is licensed by NVIDIA under OpenMDW-1.1; the files come from the sherpa-onnx project's Hugging Face repository at a fixed commit.

### Whisper preview (`on`)

While you speak, the overlay shows the text recognized so far. PulseScribe transcribes the running recording again about every 0.7 s; the last word appears once the next one follows or you pause. The final transcript is still one pass over the whole recording, so its text is the same as without the preview. Recordings longer than 20 s preview only their newest 10 to 20 s.

- A pause of about one second before you stop makes the transcript available at once: the pause already ran the final pass. This applies to recordings up to 20 s in a quiet room. With background noise close to the level of quiet speech, PulseScribe cannot tell the pause from the noise: the last word then appears 0.7 s after you stop speaking, and the transcript comes at the usual time. Noise louder than a tenth of your loudest speech counts as speech, so the last word stays hidden until you stop.
- Stopping in the middle of a preview pass delays the transcript by the rest of that pass. Passes start only while you speak, so this happens mostly when you stop within 0.1 s of your last word.
- Measured on an M6 with turbo and German recordings, 28 runs per stop time, at a loud and at a quiet microphone level (loudest moment 0.012 RMS): a preview pass takes 0.3 to 0.4 s. Median waiting time after the stop was 0.4 s without the preview. With it: 0.04 s when stopping 1 s after the last word, 0.3 s after 0.45 s and 0.4 s after 0.1 s, where about one recording in five waited up to 0.3 s longer than without the preview.
- The GPU works for roughly half of the time you speak (a 0.3 s pass every 0.7 s).
- Needs a fast model. `large`, `large-v2` and `large-v3` show no live text with `on`, and Settings say so: with large-v3 a pass over 10 s of audio takes 0.95 s on an M6, and passes on an M5 took 1.0 to 1.5 s; turbo takes 0.35 s on the M6. Other backends are too slow as well. The streaming model works with all of them.
- On a slower Mac the text updates less often: the next pass starts at least twice the duration of the last one later. If two passes in a row take longer than 1 s, the preview ends for that recording and its text disappears. If that happens with the first two passes of a recording, the preview pauses for 30 minutes or until you change the model.
- No live text? With MLX and the preview turned on, `~/.pulsescribe/logs/pulsescribe.log` has one `Live-Vorschau:` line per recording that says how many passes ran or why none did.

---

## Performance Tuning

Settings → Providers → Advanced backend options only show the options the active backend uses (for `auto`, the backend it resolves to); hover an option for what it does and its default:

| Option | Applies to |
|--------|------------|
| Preset, Warmup | all local backends |
| Unload when idle | all local backends (macOS app) |
| Live preview | all local backends (macOS app); `on` only with MLX and a fast model |
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

