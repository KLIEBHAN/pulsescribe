# whisper_go

[![Tests](https://github.com/KLIEBHAN/whisper_go/actions/workflows/test.yml/badge.svg)](https://github.com/KLIEBHAN/whisper_go/actions/workflows/test.yml)
[![codecov](https://codecov.io/gh/KLIEBHAN/whisper_go/graph/badge.svg)](https://codecov.io/gh/KLIEBHAN/whisper_go)

[🇩🇪 Deutsche Version](README.de.md)

Voice input for macOS – inspired by [Wispr Flow](https://wisprflow.ai). Transcribes audio using OpenAI Whisper via API, Deepgram, Groq, or locally.

**Features:** Real-time Streaming (Deepgram) · Multiple Providers (OpenAI, Deepgram, Groq, Local) · LLM Post-processing · Context Awareness · Custom Vocabulary · Raycast Hotkeys · Live Preview Overlay · Menu Bar Feedback

> **Performance:** Ultra-fast startup with ~170ms to "Ready-Sound" thanks to parallel microphone and WebSocket initialization. Audio is transcribed during recording – results appear immediately after stopping.

### Provider Overview

| Provider     | Latency   | Method    | Special Feature                  |
| ------------ | --------- | --------- | -------------------------------- |
| **Deepgram** | ~300ms ⚡ | WebSocket | Real-time streaming, recommended |
| **Groq**     | ~1s       | REST      | Whisper on LPU, very fast        |
| **OpenAI**   | ~2-3s     | REST      | GPT-4o, highest quality          |
| **Local**    | ~5-10s    | Whisper   | Offline, no API costs (optional faster backend) |

## Quick Start

Ready to use in under 2 minutes:

```bash
# 1. Clone repository
git clone https://github.com/KLIEBHAN/whisper_go.git && cd whisper_go

# 2. Install dependencies
pip install -r requirements.txt

# 3. Set API Key (Deepgram: $200 free credit)
export DEEPGRAM_API_KEY="your_key"

# 4. First recording
python transcribe.py --record --copy --mode deepgram
```

### Recommended `.env` Configuration

Create a `.env` file in the project directory for persistent settings:

```bash
# API Keys
DEEPGRAM_API_KEY=...
GROQ_API_KEY=...
OPENAI_API_KEY=...
OPENROUTER_API_KEY=...

# Transcription
WHISPER_GO_MODE=deepgram
WHISPER_GO_LANGUAGE=en

# LLM Post-processing
WHISPER_GO_REFINE=true
WHISPER_GO_REFINE_PROVIDER=groq
WHISPER_GO_REFINE_MODEL=openai/gpt-oss-120b
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
| `--model NAME`                         | Model (CLI > `WHISPER_GO_MODEL` env > Provider default)                       |
| `--record`, `-r`                       | Microphone recording instead of file                                          |
| `--copy`, `-c`                         | Copy result to clipboard                                                      |
| `--language CODE`                      | Language code e.g., `de`, `en`                                                |
| `--format FORMAT`                      | Output: `text`, `json`, `srt`, `vtt` (only API mode)                          |
| `--no-streaming`                       | Disable WebSocket streaming (Deepgram only)                                   |
| `--refine`                             | Enable LLM post-processing                                                    |
| `--no-refine`                          | Disable LLM post-processing (overrides env)                                   |
| `--refine-model`                       | Model for post-processing (default: `gpt-5-nano`)                             |
| `--refine-provider`                    | LLM provider: `openai`, `openrouter`, `groq`                                  |
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
export WHISPER_GO_MODE="deepgram"

# Transcription Model (overrides Provider default)
export WHISPER_GO_MODEL="nova-3"

# Device for local Whisper (auto, mps, cpu, cuda)
# Default: auto → uses MPS on Apple Silicon, otherwise CPU/CUDA
export WHISPER_GO_DEVICE="auto"

# Force FP16 for local Whisper (true/false)
# Default: CPU/MPS → false (stable), CUDA → true
export WHISPER_GO_FP16="false"

# Backend for local Whisper (whisper, faster, auto)
# whisper = openai-whisper (PyTorch, uses MPS/GPU)
# faster  = faster-whisper (CTranslate2, very fast on CPU)
# auto    = faster if installed, else whisper
export WHISPER_GO_LOCAL_BACKEND="whisper"

# Local model override (only for local mode)
# Default: provider default (turbo)
# export WHISPER_GO_LOCAL_MODEL="turbo"

# Compute type for faster-whisper (optional)
# Default: int8 on CPU, float16 on CUDA
# export WHISPER_GO_LOCAL_COMPUTE_TYPE="int8"

# Faster-whisper threading (optional)
# 0 threads = auto (all cores)
# export WHISPER_GO_LOCAL_CPU_THREADS=0
# export WHISPER_GO_LOCAL_NUM_WORKERS=1

# Faster-whisper options (optional)
# default on faster: without_timestamps=true, vad_filter=false
# export WHISPER_GO_LOCAL_WITHOUT_TIMESTAMPS="true"
# export WHISPER_GO_LOCAL_VAD_FILTER="false"

# Optional: faster local decoding (more speed, slightly less robustness)
export WHISPER_GO_LOCAL_FAST="false"  # sets beam_size=1, best_of=1, temperature=0.0
# Fine-tuning:
# export WHISPER_GO_LOCAL_BEAM_SIZE=1
# export WHISPER_GO_LOCAL_BEST_OF=1
# export WHISPER_GO_LOCAL_TEMPERATURE=0.0

# WebSocket Streaming for Deepgram (default: true)
export WHISPER_GO_STREAMING="true"

# LLM Post-processing
export WHISPER_GO_REFINE="true"
export WHISPER_GO_REFINE_MODEL="gpt-5-nano"
export WHISPER_GO_REFINE_PROVIDER="openai"  # or openrouter, groq
```

### System Dependencies

Certain modes require additional tools:

```bash
# Local Mode (ffmpeg for audio conversion)
brew install ffmpeg          # macOS
sudo apt install ffmpeg      # Ubuntu/Debian

# Microphone Recording (macOS)
brew install portaudio
```

## Advanced Features

Beyond basic transcription, whisper_go offers intelligent post-processing and customization.

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
export WHISPER_GO_APP_CONTEXTS='{"MyApp": "chat"}'
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

Custom terms for better recognition in `~/.whisper_go/vocabulary.json`:

```json
{
  "keywords": ["Anthropic", "Claude", "Kubernetes", "OAuth"]
}
```

Supported by Deepgram and local Whisper. The OpenAI API does not support custom vocabulary – LLM post-processing helps there.

## Hotkey Integration

For system-wide voice input via hotkey – the main use case of whisper_go.

### Unified Daemon (Recommended)

The `whisper_daemon.py` combines all components in one process:

- Hotkey Listener (QuickMacHotKey)
- Microphone Recording + Deepgram Streaming
- Menu Bar Status (🎤 🔴 ⏳ ✅ ❌) - via `ui/menubar.py`
- Overlay with Animations - via `ui/overlay.py`
- Auto-Paste

```bash
# Manual Start
python whisper_daemon.py

# With CLI Options
python whisper_daemon.py --hotkey cmd+shift+r --debug

# As Login Item (Double click or add to Login Items)
open start_daemon.command
```

> **Toggle hotkeys don't need Accessibility permission.** QuickMacHotKey uses the native Carbon API (`RegisterEventHotKey`).  
> **Hold mode uses pynput and requires Accessibility** on macOS.

### Configuration

In `.env` or as environment variable:

```bash
# Hotkeys (default: Fn/Globe as hold)
#
# Optional: toggle + hold in parallel.
# If set, these override WHISPER_GO_HOTKEY / WHISPER_GO_HOTKEY_MODE.
#
# Recommended default: use Fn/Globe as Push‑to‑Talk (hold).
# WHISPER_GO_HOLD_HOTKEY=fn
# Optional: add a separate toggle hotkey (e.g. F19).
# WHISPER_GO_TOGGLE_HOTKEY=f19
#
# Legacy (single hotkey):
WHISPER_GO_HOTKEY=fn
WHISPER_GO_HOTKEY_MODE=hold

# Dock Icon (default: true) – set to false for menubar-only mode
WHISPER_GO_DOCK_ICON=true
```

**Supported Hotkeys:**

| Format          | Example               |
| --------------- | --------------------- |
| Function Keys   | `f19`, `f1`, `f12`    |
| Single Key      | `fn`, `capslock`, `space`, `tab`, `esc` |
| Key Combination | `cmd+shift+r`         |

**Recommended hotkey setup (macOS):**

- **Fn/Globe as Hold‑to‑Record:** set `WHISPER_GO_HOLD_HOTKEY=fn`.  
  This gives a fast, one‑finger Push‑to‑Talk workflow. Requires Accessibility/Input Monitoring.
- **CapsLock alternative:** CapsLock can be used directly as a toggle hotkey, but macOS often toggles capitalization.  
  For a conflict‑free “single key toggle”, map CapsLock → `F19` via **Karabiner‑Elements** and set `WHISPER_GO_TOGGLE_HOTKEY=f19`.

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

| Mode       | Provider | Method    | Latency   | Special Feature                    |
| ---------- | -------- | --------- | --------- | ---------------------------------- |
| `deepgram` | Deepgram | WebSocket | ~300ms ⚡ | Real-time streaming (recommended)  |
| `groq`     | Groq     | REST      | ~1s       | Whisper on LPU, very fast          |
| `openai`   | OpenAI   | REST      | ~2-3s     | GPT-4o Transcribe, highest quality |
| `local`    | Whisper  | Local     | ~5-10s    | Offline, no API costs (optional faster backend) |

> **Recommendation:** `--mode deepgram` for daily use. The streaming architecture ensures minimal waiting time between recording stop and text insertion.

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
# Streaming (Default)
python transcribe.py --record --mode deepgram

# REST Fallback (if streaming causes issues)
python transcribe.py --record --mode deepgram --no-streaming
# or via ENV:
WHISPER_GO_STREAMING=false
```

### Groq Models

| Model                        | Description                          |
| ---------------------------- | ------------------------------------ |
| `whisper-large-v3`           | Whisper Large v3, ~300x real-time ⭐ |
| `distil-whisper-large-v3-en` | English only, even faster            |

Groq uses LPU chips (Language Processing Units) for particularly fast inference.

### Local Models

Local mode now supports two backends:

- **`whisper` (default):** openai‑whisper on PyTorch. Uses Apple‑GPU via MPS automatically on M‑series Macs (`WHISPER_GO_DEVICE=auto`). Best compatibility/quality.
- **`faster`:** faster‑whisper (CTranslate2). Very fast on CPU (often 2–4× faster) and lower memory. Default `compute_type` is `int8` on CPU and `float16` on CUDA. Enable via `WHISPER_GO_LOCAL_BACKEND=faster`.

Notes:

- Model name `turbo` maps to faster‑whisper `large-v3-turbo`.
- For maximum speed (with slight robustness trade‑off), set `WHISPER_GO_LOCAL_FAST=true` or lower `WHISPER_GO_LOCAL_BEAM_SIZE`/`BEST_OF`.
- For long recordings on `faster`, you can tune throughput via `WHISPER_GO_LOCAL_CPU_THREADS` and `WHISPER_GO_LOCAL_NUM_WORKERS`.

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

| Problem                             | Solution                                                                 |
| ----------------------------------- | ------------------------------------------------------------------------ |
| Module not installed                | `pip install -r requirements.txt`                                        |
| API Key missing                     | `export DEEPGRAM_API_KEY="..."` (or OPENAI/GROQ)                         |
| Microphone issues (macOS)           | `brew install portaudio && pip install --force-reinstall sounddevice`    |
| Microphone permission               | Grant access in System Settings → Privacy & Security → Microphone        |
| ffmpeg missing                      | `brew install ffmpeg` (macOS) or `sudo apt install ffmpeg` (Ubuntu)      |
| Transcription slow                  | Switch to `--mode groq`/`deepgram`, or use `WHISPER_GO_LOCAL_BACKEND=faster`, `WHISPER_GO_LOCAL_FAST=true`, or a smaller local model |
| Daemon crashes silently             | Check `~/.whisper_go/startup.log` for emergency logs                     |
| Auto-Paste not working (App Bundle) | See [Auto-Paste Troubleshooting](#auto-paste-troubleshooting-app-bundle) |

### Auto-Paste Troubleshooting (App Bundle)

If Auto-Paste doesn't work in `WhisperGo.app` (text is copied but not pasted):

**Symptom:** Log shows `AXIsProcessTrusted = False` even though the app is enabled in Accessibility.

**Cause:** Unsigned PyInstaller bundles change their hash with every rebuild. macOS doesn't recognize the "new" app as authorized.

**Solution:**

1. System Settings → Privacy & Security → Accessibility
2. **Remove** `WhisperGo` (minus button)
3. **Re-add** `WhisperGo` (plus button or drag & drop the app)

> **Tip:** After every `pyinstaller build_app.spec`, you need to repeat this step as long as the app is not code-signed.

### Log Files

Logs are stored in `~/.whisper_go/logs/`:

```bash
# Main log file
~/.whisper_go/logs/whisper_go.log

# Emergency startup log (if daemon fails to start)
~/.whisper_go/startup.log
```

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

### Building the macOS App Bundle

To create a standalone `WhisperGo.app`:

```bash
# Install PyInstaller (if not already installed)
pip install pyinstaller

# Build the app
pyinstaller build_app.spec

# Output: dist/WhisperGo.app
```

**Optional: Code-Sign for stable Accessibility permissions**

```bash
codesign --force --deep --sign - dist/WhisperGo.app
```

> **Note:** Without signing, you must re-authorize the app in System Settings → Privacy & Security → Accessibility after every rebuild. See [Auto-Paste Troubleshooting](#auto-paste-troubleshooting-app-bundle).
