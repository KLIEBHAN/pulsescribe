# PulseScribe

[![Tests](https://github.com/KLIEBHAN/pulsescribe/actions/workflows/test.yml/badge.svg)](https://github.com/KLIEBHAN/pulsescribe/actions/workflows/test.yml)
[![codecov](https://codecov.io/gh/KLIEBHAN/pulsescribe/graph/badge.svg)](https://codecov.io/gh/KLIEBHAN/pulsescribe)

[🇩🇪 Deutsche Version](README.de.md)

Voice input for macOS and Windows – inspired by [Wispr Flow](https://wisprflow.ai). Transcribes audio using OpenAI Whisper via API, Deepgram, Groq, or locally.

**Features:** Real-time Streaming (Deepgram) · Multiple Providers (OpenAI, Deepgram, Groq, Local) · **⚡ Lightning Mode** (4x faster local transcription on Apple Silicon) · LLM Post-processing · Context Awareness · Custom Vocabulary · Live Preview Overlay · Menu Bar Feedback

> **Performance:** Ultra-fast startup with ~170ms to "Ready-Sound" thanks to parallel microphone and WebSocket initialization. Audio is transcribed during recording – results appear immediately after stopping.

<p align="center">
  <img src="docs/assets/demo.gif" alt="PulseScribe Demo" width="700">
</p>

### Provider Overview

| Provider     | Latency   | Method    | Special Feature                                        |
| ------------ | --------- | --------- | ------------------------------------------------------ |
| **Deepgram** | ~300ms ⚡ | WebSocket | Real-time streaming, recommended                       |
| **Groq**     | ~1s       | REST      | Whisper on LPU, very fast                              |
| **OpenAI**   | ~2-3s     | REST      | GPT-4o, highest quality                                |
| **Local**    | varies    | Whisper   | Offline, no API costs (MLX/Lightning on Apple Silicon) |

## Quick Start

Ready to use in under 2 minutes:

```bash
# 1. Clone repository
git clone https://github.com/KLIEBHAN/pulsescribe.git && cd pulsescribe

# 2. Install dependencies
pip install -r requirements.txt

# 3. Set API Key (Deepgram: $200 free credit)
export DEEPGRAM_API_KEY="your_key"

# 4. First recording
python transcribe.py --record --copy --mode deepgram
```

### Recommended `.env` Configuration

PulseScribe loads settings from `~/.pulsescribe/.env` (recommended; used by the Settings UI and the daemon).  
For development, a local `.env` in the project directory is also supported.

```bash
# Recommended (works for the daemon / app bundle)
cp .env.example ~/.pulsescribe/.env
```

Example `~/.pulsescribe/.env`:

```bash
# API Keys
DEEPGRAM_API_KEY=...
GROQ_API_KEY=...
OPENAI_API_KEY=...
OPENROUTER_API_KEY=...

# Transcription
PULSESCRIBE_MODE=deepgram
PULSESCRIBE_LANGUAGE=en

# LLM Post-processing
PULSESCRIBE_REFINE=true
PULSESCRIBE_REFINE_PROVIDER=groq
PULSESCRIBE_REFINE_MODEL=openai/gpt-oss-120b
```

**Why these settings?**

| Setting                            | Reason                                             |
| ---------------------------------- | -------------------------------------------------- |
| `MODE=deepgram`                    | Fastest option (~300ms) via WebSocket streaming    |
| `REFINE_PROVIDER=groq`             | Free/cheap LLM inference on LPU hardware           |
| `REFINE_MODEL=openai/gpt-oss-120b` | Open-source GPT alternative with excellent quality |
| `LANGUAGE=en`                      | Explicit language improves transcription accuracy  |

> **Tip:** For system-wide hotkeys see [Hotkey Integration](#hotkey-integration).

## CLI Usage

Two main functions: Transcribe audio files or record directly from the microphone.

### Transcribe Audio File

```bash
python transcribe.py audio.mp3                        # Default (API Mode)
python transcribe.py audio.mp3 --mode openai          # OpenAI GPT-4o Transcribe
python transcribe.py audio.mp3 --mode deepgram        # Deepgram Nova-3
python transcribe.py audio.mp3 --mode groq            # Groq (fastest option)
python transcribe.py audio.mp3 --mode local           # Offline with local Whisper
```

### Microphone Recording

```bash
python transcribe.py --record                         # Record and print output
python transcribe.py --record --copy                  # Copy directly to clipboard
python transcribe.py --record --refine                # With LLM post-processing
```

**Workflow:** Enter → Speak → Enter → Transcript appears

### All Options

| Option                                 | Description                                                                   |
| -------------------------------------- | ----------------------------------------------------------------------------- |
| `--mode openai\|local\|deepgram\|groq` | Transcription provider (default: `openai`)                                    |
| `--model NAME`                         | Model (CLI > `PULSESCRIBE_MODEL` env > Provider default)                      |
| `--record`, `-r`                       | Microphone recording instead of file                                          |
| `--copy`, `-c`                         | Copy result to clipboard                                                      |
| `--language CODE`                      | Language code e.g., `de`, `en`                                                |
| `--format FORMAT`                      | Output: `text`, `json`, `srt`, `vtt` (only API mode)                          |
| `--refine`                             | Enable LLM post-processing                                                    |
| `--no-refine`                          | Disable LLM post-processing (overrides env)                                   |
| `--refine-model`                       | Model for post-processing (default: `openai/gpt-oss-120b`)                    |
| `--refine-provider`                    | LLM provider: `groq`, `openai` or `openrouter`                                |
| `--context`                            | Context for post-processing: `email`, `chat`, `code`, `default` (auto-detect) |

## Configuration

All settings can be set via environment variables or a `.env` file. CLI arguments always take precedence.

### API Keys

Depending on the selected mode, an API key is required:

```bash
# OpenAI (for --mode openai and --refine with openai)
export OPENAI_API_KEY="sk-..."

# Deepgram (for --mode deepgram) – $200 free credit
export DEEPGRAM_API_KEY="..."

# Groq (for --mode groq and --refine with groq) – free credits
export GROQ_API_KEY="gsk_..."

# OpenRouter (Alternative for --refine) – Hundreds of models
export OPENROUTER_API_KEY="sk-or-..."
```

### Default Settings

```bash
# Transcription Mode (openai, local, deepgram, groq)
export PULSESCRIBE_MODE="deepgram"

# Transcription Model (overrides Provider default)
export PULSESCRIBE_MODEL="nova-3"

# Device for local Whisper (auto, mps, cpu, cuda)
# Default: auto → uses MPS on Apple Silicon, otherwise CPU/CUDA
export PULSESCRIBE_DEVICE="auto"

# Force FP16 for local Whisper (true/false)
# Default: CPU/MPS → false (stable), CUDA → true
export PULSESCRIBE_FP16="false"

# Backend for local Whisper (whisper, faster, mlx, auto)
# whisper = openai-whisper (PyTorch, uses MPS/GPU)
# faster  = faster-whisper (CTranslate2, very fast on CPU)
# mlx     = mlx-whisper (MLX/Metal, Apple Silicon, optional)
# auto    = faster if installed, else whisper
export PULSESCRIBE_LOCAL_BACKEND="whisper"

# Local model override (only for local mode)
# Default: provider default (turbo)
# export PULSESCRIBE_LOCAL_MODEL="turbo"

# Compute type for faster-whisper (optional)
# Default: int8 on CPU, float16 on CUDA
# export PULSESCRIBE_LOCAL_COMPUTE_TYPE="int8"

# Faster-whisper threading (optional)
# 0 threads = auto (all cores)
# export PULSESCRIBE_LOCAL_CPU_THREADS=0
# export PULSESCRIBE_LOCAL_NUM_WORKERS=1

# Faster-whisper options (optional)
# default on faster: without_timestamps=true, vad_filter=false
# export PULSESCRIBE_LOCAL_WITHOUT_TIMESTAMPS="true"
# export PULSESCRIBE_LOCAL_VAD_FILTER="false"

# Optional: faster local decoding (more speed, slightly less robustness)
# Default: true on faster-whisper, false on openai-whisper
# export PULSESCRIBE_LOCAL_FAST="true"  # sets beam_size=1, best_of=1, temperature=0.0
# Fine-tuning:
# export PULSESCRIBE_LOCAL_BEAM_SIZE=1
# export PULSESCRIBE_LOCAL_BEST_OF=1
# export PULSESCRIBE_LOCAL_TEMPERATURE=0.0

# Optional: Local Warmup (reduces "cold start" on first local call)
# Default: auto (warmup only for openai-whisper on MPS). Values: true/false (unset = auto)
# export PULSESCRIBE_LOCAL_WARMUP="true"

# WebSocket Streaming for Deepgram (default: true)
export PULSESCRIBE_STREAMING="true"

# LLM Post-processing
export PULSESCRIBE_REFINE="true"
export PULSESCRIBE_REFINE_MODEL="openai/gpt-oss-120b"
export PULSESCRIBE_REFINE_PROVIDER="openai"  # or openrouter, groq
```

### System Dependencies

Certain modes require additional tools:

```bash
# Local Mode (file transcription)
# Required for `PULSESCRIBE_LOCAL_BACKEND=whisper` and `mlx` when transcribing audio files.
brew install ffmpeg          # macOS
sudo apt install ffmpeg      # Ubuntu/Debian

# Microphone Recording (macOS)
brew install portaudio
```

## Advanced Features

Beyond basic transcription, pulsescribe offers intelligent post-processing and customization.

### LLM Post-processing

Removes filler words (um, uh, like), corrects grammar, and formats into clean paragraphs:

```bash
python transcribe.py --record --refine
```

Supported Providers: OpenAI (default), [OpenRouter](https://openrouter.ai), [Groq](https://groq.com)

### Context Awareness

Post-processing automatically detects the active app and adjusts the writing style:

| Context   | Apps                      | Style                      |
| --------- | ------------------------- | -------------------------- |
| `email`   | Mail, Outlook, Spark      | Formal, complete sentences |
| `chat`    | Slack, Discord, Messages  | Casual, short and concise  |
| `code`    | VS Code, Cursor, Terminal | Technical, preserve terms  |
| `default` | All others                | Standard correction        |

```bash
# Automatic detection (Default)
python transcribe.py --record --refine

# Manual override
python transcribe.py --record --refine --context email

# Custom App Mappings
export PULSESCRIBE_APP_CONTEXTS='{"MyApp": "chat"}'
```

### Real-Time Audio Feedback

The overlay reacts in real-time to voice with a dynamic sound wave visualization:

- **Listening (🌸 Pink):** System waiting for voice input.
- **Recording (🔴 Red):** Voice detected, recording in progress. Bars visualize volume.
- **Transcribing (🟠 Orange):** Recording finished, processing text.

Thanks to integrated Voice Activity Detection (VAD), the status switches immediately when speaking begins.

### Voice Commands

Control formatting via spoken commands (automatically active with `--refine`):

| German           | English            | Result     |
| ---------------- | ------------------ | ---------- |
| "neuer Absatz"   | "new paragraph"    | Paragraph  |
| "neue Zeile"     | "new line"         | Line break |
| "Punkt"          | "period"           | `.`        |
| "Komma"          | "comma"            | `,`        |
| "Fragezeichen"   | "question mark"    | `?`        |
| "Ausrufezeichen" | "exclamation mark" | `!`        |
| "Doppelpunkt"    | "colon"            | `:`        |
| "Semikolon"      | "semicolon"        | `;`        |

```bash
# Example
python transcribe.py --record --refine
# Speak: "Hello comma how are you question mark"
# Result: "Hello, how are you?"
```

> **Note:** Voice commands are interpreted by the LLM – they only work with `--refine`.

### Custom Vocabulary

Custom terms for better recognition in `~/.pulsescribe/vocabulary.json`:

```json
{
  "keywords": ["Anthropic", "Claude", "Kubernetes", "OAuth"]
}
```

Supported by Deepgram and local Whisper. The OpenAI API does not support custom vocabulary – LLM post-processing helps there.

## Hotkey Integration

For system-wide voice input via hotkey – the main use case of pulsescribe.

### Unified Daemon (Recommended)

The `pulsescribe_daemon.py` combines all components in one process:

- Hotkey Listener (QuickMacHotKey)
- Microphone Recording + Deepgram Streaming
- Menu Bar Status (🎤 🔴 ⏳ ✅ ❌) - via `ui/menubar.py`
- Overlay with Animations - via `ui/overlay.py`
- Auto-Paste

```bash
# Manual Start
python pulsescribe_daemon.py

# With CLI Options
python pulsescribe_daemon.py --hotkey cmd+shift+r --debug

# As Login Item (Double click or add to Login Items)
open start_daemon.command
```

> **Toggle hotkeys don't need Accessibility permission.** QuickMacHotKey uses the native Carbon API (`RegisterEventHotKey`).  
> **Hold mode uses Quartz event taps and requires Input Monitoring** on macOS.

### Settings UI (Menu Bar)

Use the menu bar icon → **Settings...** to configure provider keys, mode, local backend/model, and advanced local performance knobs (device, warmup, fast decoding, faster-whisper compute/threading, etc.).  
Settings are stored in `~/.pulsescribe/.env` and applied live (hotkey changes apply immediately).

### Configuration

In `.env` or as environment variable:

```bash
# Hotkeys (default: Fn/Globe as hold)
#
# Optional: toggle + hold in parallel.
# If set, these override PULSESCRIBE_HOTKEY / PULSESCRIBE_HOTKEY_MODE.
#
# Recommended default: use Fn/Globe as Push‑to‑Talk (hold).
# PULSESCRIBE_HOLD_HOTKEY=fn
# Optional: add a separate toggle hotkey (e.g. F19).
# PULSESCRIBE_TOGGLE_HOTKEY=f19
#
# Legacy (single hotkey):
PULSESCRIBE_HOTKEY=fn
PULSESCRIBE_HOTKEY_MODE=hold

# Dock Icon (default: true) – set to false for menubar-only mode
PULSESCRIBE_DOCK_ICON=true

# RTF Display (default: false) – show Real-Time Factor after transcription
# PULSESCRIBE_SHOW_RTF=true
```

**RTF (Real-Time Factor):** Optional performance indicator in the overlay.

| RTF      | Meaning                  | Example (10s audio) |
| -------- | ------------------------ | ------------------- |
| **0.3x** | 3× faster than real-time | 3s processing       |
| **1.0x** | Real-time                | 10s processing      |
| **2.0x** | 2× slower than real-time | 20s processing      |

Enable via Settings → Refine Tab → "Show RTF after transcription" or `PULSESCRIBE_SHOW_RTF=true`.

**Supported Hotkeys:**

| Format          | Example                                 |
| --------------- | --------------------------------------- |
| Function Keys   | `f19`, `f1`, `f12`                      |
| Single Key      | `fn`, `capslock`, `space`, `tab`, `esc` |
| Key Combination | `cmd+shift+r`                           |

**Recommended hotkey setup (macOS):**

- **Fn/Globe as Hold‑to‑Record:** set `PULSESCRIBE_HOLD_HOTKEY=fn`.  
  This gives a fast, one‑finger Push‑to‑Talk workflow. Requires Accessibility/Input Monitoring.
- **CapsLock alternative:** CapsLock can be used directly as a toggle hotkey, but macOS often toggles capitalization.  
  For a conflict‑free “single key toggle”, map CapsLock → `F19` via **Karabiner‑Elements** and set `PULSESCRIBE_TOGGLE_HOTKEY=f19`.

### Usage

**Hold Mode (Default / Push‑to‑Talk):**

- Hold Fn/Globe → Recording runs while held
- Release Fn/Globe → Transcript is pasted

**Toggle Mode (Optional, e.g. with F19):**

- Press F19 → Recording starts
- Press F19 again → Transcript is pasted

### Visual Feedback

The overlay shows the current status through colors and animations:

| Status           | Color     | Animation | Meaning                            |
| ---------------- | --------- | --------- | ---------------------------------- |
| **Listening**    | 🌸 Pink   | Breathing | Hotkey pressed, waiting for speech |
| **Recording**    | 🔴 Red    | Waves     | Speech detected, recording active  |
| **Transcribing** | 🟠 Orange | Loading   | Finalizing transcription           |
| **Refining**     | 💜 Violet | Pulsing   | LLM post-processing active         |
| **Done**         | ✅ Green  | Bounce    | Done, text pasted                  |
| **Error**        | ❌ Red    | Blink     | Error occurred                     |

Both are integrated and start automatically with the daemon.

## Provider Comparison

| Mode       | Provider | Method    | Latency   | Special Feature                                            |
| ---------- | -------- | --------- | --------- | ---------------------------------------------------------- |
| `deepgram` | Deepgram | WebSocket | ~300ms ⚡ | Real-time streaming (recommended)                          |
| `groq`     | Groq     | REST      | ~1s       | Whisper on LPU, very fast                                  |
| `openai`   | OpenAI   | REST      | ~2-3s     | GPT-4o Transcribe, highest quality                         |
| `local`    | Whisper  | Local     | varies    | Offline, no API costs (Whisper / Faster / MLX / Lightning) |

> **Recommendation:** `--mode deepgram` for daily use. The streaming architecture ensures minimal waiting time between recording stop and text insertion.

### Local Backend Options

| Backend     | Description                                            | Best For            |
| ----------- | ------------------------------------------------------ | ------------------- |
| `whisper`   | OpenAI Whisper (PyTorch), works on all platforms       | Compatibility       |
| `faster`    | faster-whisper (CTranslate2), 4x faster on CPU         | CPU-only systems    |
| `mlx`       | mlx-whisper (Metal), optimized for Apple Silicon       | macOS stability     |
| `lightning` | lightning-whisper-mlx, ~4x faster via Batched Decoding | Maximum speed (M1+) |

Set via: `PULSESCRIBE_LOCAL_BACKEND=lightning`

**Lightning-specific options:**

- `PULSESCRIBE_LIGHTNING_BATCH_SIZE`: Batch size (default: 12, higher = faster, more RAM)
- `PULSESCRIBE_LIGHTNING_QUANT`: Quantization (`None`, `4bit`, `8bit`)

## Model Reference

### API Models (OpenAI)

| Model                    | Description      |
| ------------------------ | ---------------- |
| `gpt-4o-transcribe`      | Best quality ⭐  |
| `gpt-4o-mini-transcribe` | Faster, cheaper  |
| `whisper-1`              | Original Whisper |

### Deepgram Models

| Model    | Description                   |
| -------- | ----------------------------- |
| `nova-3` | Newest model, best quality ⭐ |
| `nova-2` | Proven model, cheaper         |

`smart_format` is activated – automatic formatting of dates, currency, and paragraphs.

#### Real-time Streaming (Default)

Deepgram uses **WebSocket streaming** by default for minimal latency:

- Audio is transcribed **during recording**, not just afterwards
- Result appears **immediately** after stopping (instead of 2-3s wait)
- Ideal for hotkey integration

```bash
# Unified daemon uses streaming by default.
# REST fallback (daemon only):
PULSESCRIBE_STREAMING=false
```

### Groq Models

| Model                        | Description                          |
| ---------------------------- | ------------------------------------ |
| `whisper-large-v3`           | Whisper Large v3, ~300x real-time ⭐ |
| `distil-whisper-large-v3-en` | English only, even faster            |

Groq uses LPU chips (Language Processing Units) for particularly fast inference.

### Local Models

Local mode now supports three backends:

- **`whisper` (default):** openai‑whisper on PyTorch. Uses Apple‑GPU via MPS automatically on M‑series Macs (`PULSESCRIBE_DEVICE=auto`). Best compatibility/quality.
- **`faster`:** faster‑whisper (CTranslate2). Very fast on CPU and lower memory. On macOS it runs on CPU (no MPS/Metal). Default `compute_type` is `int8` on CPU and `float16` on CUDA. Enable via `PULSESCRIBE_LOCAL_BACKEND=faster`.
- **`mlx`:** mlx‑whisper (MLX/Metal). Apple Silicon GPU‑accelerated local backend. Install with `pip install mlx-whisper` and enable via `PULSESCRIBE_LOCAL_BACKEND=mlx`.

Notes:

- Model name `turbo` maps to faster‑whisper `large-v3-turbo`.
- For maximum speed (with slight robustness trade‑off), set `PULSESCRIBE_LOCAL_FAST=true` or lower `PULSESCRIBE_LOCAL_BEAM_SIZE`/`PULSESCRIBE_LOCAL_BEST_OF`.
- For long recordings on `faster`, you can tune throughput via `PULSESCRIBE_LOCAL_CPU_THREADS` and `PULSESCRIBE_LOCAL_NUM_WORKERS`.
- For `mlx`, `PULSESCRIBE_LOCAL_BEAM_SIZE` is ignored (beam search is not implemented in mlx‑whisper).

#### Quick setup (offline dictation)

Apple Silicon (recommended local backend):

```bash
pip install mlx-whisper
export PULSESCRIBE_MODE=local
export PULSESCRIBE_LOCAL_BACKEND=mlx
export PULSESCRIBE_LOCAL_MODEL=large   # or: turbo
export PULSESCRIBE_LANGUAGE=de         # optional
python pulsescribe_daemon.py --debug
```

#### Apple Silicon: MLX model names

With `PULSESCRIBE_LOCAL_BACKEND=mlx`, `PULSESCRIBE_LOCAL_MODEL` supports both short names and full Hugging Face repo IDs:

**Multilingual models (German, English, etc.):**

- `turbo` → `mlx-community/whisper-large-v3-turbo` ⭐ (recommended for speed + quality)
- `large` → `mlx-community/whisper-large-v3-mlx` (highest quality, slower)
- `medium` → `mlx-community/whisper-medium`
- `small` → `mlx-community/whisper-small-mlx`
- `base` → `mlx-community/whisper-base-mlx`
- `tiny` → `mlx-community/whisper-tiny`

**English-only (distilled, 30-40% faster, ⚠️ ONLY English!):**

- `large-en` → `mlx-community/distil-whisper-large-v3`
- `medium-en` → `mlx-community/distil-whisper-medium.en`
- `small-en` → `mlx-community/distil-whisper-small.en`

> **Note:** The `-en` models are distilled and only support English. For German or other languages, use `turbo` (best speed/quality) or `large` (best quality).

If you previously tried `whisper-large-v3` and hit a 404, use `large`/`large-v3` or the full repo ID `mlx-community/whisper-large-v3-mlx`.

#### Warmup / cold start

When running the daemon in `local` mode, the local model is preloaded in the background to reduce first-use latency.  
Optionally enable an additional warmup inference via `PULSESCRIBE_LOCAL_WARMUP=true` (most useful for `whisper` on MPS). If you start recording while warmup runs, nothing is “wasted” — your first local transcription may just still include some cold-start overhead.

| Model  | Parameters | VRAM   | Speed          |
| ------ | ---------- | ------ | -------------- |
| tiny   | 39M        | ~1 GB  | Very fast      |
| base   | 74M        | ~1 GB  | Fast           |
| small  | 244M       | ~2 GB  | Medium         |
| medium | 769M       | ~5 GB  | Slow           |
| large  | 1550M      | ~10 GB | Very slow      |
| turbo  | 809M       | ~6 GB  | Fast & good ⭐ |

⭐ = Default model of the provider

## Troubleshooting

| Problem                             | Solution                                                                                                                                                                |
| ----------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Module not installed                | `pip install -r requirements.txt`                                                                                                                                       |
| Windows: pystray/pillow missing     | `pip install pystray pillow`                                                                                                                                            |
| API Key missing                     | `export DEEPGRAM_API_KEY="..."` (or OPENAI/GROQ)                                                                                                                        |
| Microphone issues (macOS)           | `brew install portaudio && pip install --force-reinstall sounddevice`                                                                                                   |
| Microphone permission               | Grant access in System Settings → Privacy & Security → Microphone                                                                                                       |
| ffmpeg missing                      | `brew install ffmpeg` (macOS) or `sudo apt install ffmpeg` (Ubuntu) — needed for local file transcription (`whisper`/`mlx`)                                             |
| MLX model download 404              | Use `PULSESCRIBE_LOCAL_MODEL=large` or a full repo ID (e.g. `mlx-community/whisper-large-v3-mlx`)                                                                       |
| Beam search not implemented (mlx)   | Remove `PULSESCRIBE_LOCAL_BEAM_SIZE` (ignored on `mlx`) or switch backend                                                                                               |
| Transcription slow                  | Switch to `--mode groq`/`deepgram`, or use `PULSESCRIBE_LOCAL_BACKEND=mlx` (Apple Silicon) / `faster` (CPU) and `PULSESCRIBE_LOCAL_FAST=true`, or a smaller local model |
| Daemon crashes silently             | Check `~/.pulsescribe/startup.log` for emergency logs                                                                                                                   |
| Auto-Paste not working (App Bundle) | See [Auto-Paste Troubleshooting](#auto-paste-troubleshooting-app-bundle)                                                                                                |

### Auto-Paste Troubleshooting (App Bundle)

If Auto-Paste doesn't work in `PulseScribe.app` (text is copied but not pasted):

**Clipboard behavior:** By default, the transcribed text stays in the clipboard after pasting. You can optionally restore your previous clipboard content:

```bash
# In ~/.pulsescribe/.env:
PULSESCRIBE_CLIPBOARD_RESTORE=true
```

When enabled, PulseScribe re-copies the previous text after a successful paste. This means clipboard history tools (Paste, Alfred, etc.) will see **both** entries: your transcription and the previous content.

If paste fails, the transcript stays in the clipboard so you can paste manually.

**Symptom:** Log shows `AXIsProcessTrusted = False` even though the app is enabled in Accessibility.

**Cause:** Unsigned PyInstaller bundles change their hash with every rebuild. macOS doesn't recognize the "new" app as authorized.

**Solution:**

1. System Settings → Privacy & Security → Accessibility
2. **Remove** `PulseScribe` (minus button)
3. **Re-add** `PulseScribe` (plus button or drag & drop the app)

> **Tip:** After every `pyinstaller build_app.spec`, you need to repeat this step as long as the app is not code-signed.

### Log Files

Logs are stored in `~/.pulsescribe/logs/`:

```bash
# Main log file
~/.pulsescribe/logs/pulsescribe.log

# Emergency startup log (if daemon fails to start)
~/.pulsescribe/startup.log
```

**Diagnostics report:** Menu bar → **Export Diagnostics…** creates a zip in `~/.pulsescribe/diagnostics/` (API keys masked, log tail redacted).

## Development

```bash
# Install test dependencies
pip install -r requirements-dev.txt

# Run tests
pytest -v

# With coverage report
pytest --cov=. --cov-report=term-missing
```

Tests run automatically via GitHub Actions on Push and Pull Requests.

### Windows Support

PulseScribe includes Windows support with a dedicated daemon:

```bash
# Install dependencies (venv recommended)
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt

# Optional: GPU-accelerated overlay (recommended)
pip install PySide6

# Run the Windows daemon
python pulsescribe_windows.py

# Or use the batch file (also works for Autostart)
start_daemon.bat

# With options (defaults: Toggle=Ctrl+Alt+R, Hold=Ctrl+Win)
python pulsescribe_windows.py --debug                      # Use defaults
python pulsescribe_windows.py --toggle-hotkey "f19" --debug # Custom toggle only
python pulsescribe_windows.py --hold-hotkey "ctrl+alt+space" --refine # Custom hold only
```

**Features:**
- System Tray icon with color-coded status (gray/orange/red/yellow/cyan/green)
- Global hotkeys via pynput (toggle and/or hold mode, supports F1-F24)
- Audio recording via sounddevice with pre-warming for fast startup
- Deepgram WebSocket streaming (~300ms latency) or REST API fallback
- Auto-Paste via `Ctrl+V` simulation
- LLM post-processing (Groq, OpenAI, OpenRouter)
- App context detection (Outlook → email, VS Code → code, Discord → chat)
- Windows system sounds (DeviceConnect, DeviceDisconnect, Notification.SMS)
- GPU-accelerated overlay with PySide6 (falls back to Tkinter if not installed)
- Real-time waveform visualization with interim text preview

**Autostart:**
1. Press `Win+R`, type `shell:startup`
2. Create a shortcut to `start_daemon.bat` in the opened folder

**CLI Options:**

| Flag | Description |
|------|-------------|
| `--toggle-hotkey` | Toggle hotkey (press-speak-press), default: `ctrl+alt+r` |
| `--hold-hotkey` | Hold hotkey (hold-speak-release), default: `ctrl+win` |
| `--no-paste` | Disable auto-paste, copy to clipboard only |
| `--no-streaming` | Use REST API instead of WebSocket streaming |
| `--no-overlay` | Disable animated overlay |
| `--refine` | Enable LLM post-processing |
| `--refine-provider` | LLM provider: `groq`, `openai`, `openrouter` |
| `--refine-model` | LLM model override |
| `--context` | Force context: `email`, `chat`, `code`, `default` |
| `--debug` | Enable debug logging |

**Configuration:** Same `.env` file as macOS (`~/.pulsescribe/.env` or project root):

```bash
DEEPGRAM_API_KEY=your_key
PULSESCRIBE_LANGUAGE=de

# Hotkeys (consistent with macOS)
# Defaults: Toggle=Ctrl+Alt+R, Hold=Ctrl+Win
# Override with env variables:
# PULSESCRIBE_TOGGLE_HOTKEY=f19
# PULSESCRIBE_HOLD_HOTKEY=ctrl+alt+space
# Both hotkeys are active simultaneously by default

# Optional: Disable streaming (default: true)
# PULSESCRIBE_STREAMING=false

# Optional: Disable overlay (default: true)
# PULSESCRIBE_OVERLAY=false

# Optional: LLM Refine
PULSESCRIBE_REFINE=true
PULSESCRIBE_REFINE_PROVIDER=groq
GROQ_API_KEY=your_groq_key
```

**Building Windows EXE + Installer:**

```powershell
# Using build script (recommended)
.\build_windows.ps1                    # EXE only
.\build_windows.ps1 -Clean -Installer  # EXE + Installer

# Manual build
pip install pyinstaller
pyinstaller build_windows.spec --clean

# Output:
#   dist/PulseScribe/PulseScribe.exe      (portable)
#   dist/PulseScribe-Setup-1.1.1.exe      (installer, if built)
```

The installer (built with [Inno Setup](https://jrsoftware.org/isinfo.php)) provides:
- Start Menu entries + optional Desktop shortcut
- Autostart option (adds to Windows startup)
- Clean uninstall via "Apps & Features"

See `docs/BUILDING_WINDOWS.md` for detailed build instructions.

> **Note:** The GPU-accelerated PySide6 overlay provides smoother animations. Install with `pip install PySide6`. Falls back to Tkinter automatically if not available.

### Building the macOS App Bundle

To create a standalone `PulseScribe.app`:

```bash
# Install PyInstaller (if not already installed)
pip install pyinstaller

# Full build (all providers including local Whisper backends)
./build_app.sh

# Slim build (cloud-only: Deepgram, OpenAI, Groq - no local backends)
./build_app.sh --slim

# Output: dist/PulseScribe.app
```

**Build Variants:**

| Variant             | Size    | Providers                           | Use Case                           |
| ------------------- | ------- | ----------------------------------- | ---------------------------------- |
| **Full** (default)  | ~1 GB   | All (Deepgram, OpenAI, Groq, Local) | Offline transcription needed       |
| **Slim** (`--slim`) | ~300 MB | Cloud only (Deepgram, OpenAI, Groq) | Cloud-only usage, smaller download |

> **Note:** The Slim build does not include local Whisper backends (faster-whisper, mlx-whisper, lightning). If you need offline transcription, use the Full build.

**Optional: Code-Sign for stable Accessibility permissions**

```bash
codesign --force --deep --sign - dist/PulseScribe.app
```

> **Note:** Without signing, you must re-authorize the app in System Settings → Privacy & Security → Accessibility after every rebuild. See [Auto-Paste Troubleshooting](#auto-paste-troubleshooting-app-bundle).

### Building a DMG (recommended for distribution)

```bash
# Dev (ad-hoc signed)
./build_dmg.sh

# Release (Developer ID + notarization)
export CODESIGN_IDENTITY="Developer ID Application: Your Name (TEAMID)"
export NOTARY_PROFILE="whispergo-notary"
./build_dmg.sh 1.0.0 --notarize
```

See `docs/BUILDING_MACOS.md` for notarization setup.
