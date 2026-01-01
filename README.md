# PulseScribe

[![Tests](https://github.com/KLIEBHAN/pulsescribe/actions/workflows/test.yml/badge.svg)](https://github.com/KLIEBHAN/pulsescribe/actions/workflows/test.yml)
[![codecov](https://codecov.io/gh/KLIEBHAN/pulsescribe/graph/badge.svg)](https://codecov.io/gh/KLIEBHAN/pulsescribe)

[🇩🇪 Deutsche Version](README.de.md)

**Voice input for macOS and Windows** – inspired by [Wispr Flow](https://wisprflow.ai).

Press a hotkey, speak, release – your text appears. That's it.

<p align="center">
  <img src="docs/assets/demo.gif" alt="PulseScribe Demo" width="700">
</p>

## Features

- **Real-time Streaming** – ~300ms latency with Deepgram
- **Multiple Providers** – Deepgram, OpenAI, Groq, or local Whisper
- **LLM Post-processing** – Clean up transcriptions with GPT/Llama
- **Context Awareness** – Adjusts style based on active app (email, chat, code)
- **Visual Feedback** – Animated overlay shows recording status

## Table of Contents

- [Installation](#installation)
- [Quick Start](#quick-start)
- [Hotkey Configuration](#hotkey-configuration)
- [Provider Selection](#provider-selection)
- [LLM Post-Processing](#llm-post-processing)
- [Known Limitations](#known-limitations)
- [Troubleshooting](#troubleshooting)
- [Documentation](#documentation)

---

## Installation

### macOS

```bash
# 1. Clone repository
git clone https://github.com/KLIEBHAN/pulsescribe.git && cd pulsescribe

# 2. Install dependencies
brew install portaudio
pip install -r requirements.txt

# 3. Run the daemon
python pulsescribe_daemon.py
```

**Permissions required:**
- **Microphone** – System Settings → Privacy & Security → Microphone
- **Accessibility** – For auto-paste (Cmd+V simulation)
- **Input Monitoring** – For hold-to-record hotkeys

### Windows

```bash
# 1. Clone repository
git clone https://github.com/KLIEBHAN/pulsescribe.git && cd pulsescribe

# 2. Create virtual environment (recommended)
python -m venv venv
venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt
pip install PySide6  # Optional: GPU-accelerated overlay

# 4. Run the daemon
python pulsescribe_windows.py
```

**Autostart:** Press `Win+R`, type `shell:startup`, create shortcut to `start_daemon.bat`

### Pre-built Installers

Download from [Releases](https://github.com/KLIEBHAN/pulsescribe/releases):
- **macOS:** `PulseScribe-{version}.dmg`
- **Windows:** `PulseScribe-Setup-{version}.exe`

---

## Quick Start

### 1. Get an API Key

| Provider | Free Tier | Get Key |
|----------|-----------|---------|
| **Deepgram** (recommended) | $200 credit | [console.deepgram.com](https://console.deepgram.com) |
| **Groq** | Free tier | [console.groq.com](https://console.groq.com) |
| **OpenAI** | Pay-as-you-go | [platform.openai.com](https://platform.openai.com/api-keys) |

### 2. Configure

```bash
# Copy example config
cp .env.example ~/.pulsescribe/.env

# Edit with your API key
nano ~/.pulsescribe/.env
```

Minimal `~/.pulsescribe/.env`:

```bash
DEEPGRAM_API_KEY=your_key_here
PULSESCRIBE_MODE=deepgram
```

### 3. Start & Use

```bash
# macOS
python pulsescribe_daemon.py

# Windows
python pulsescribe_windows.py
```

**Default hotkeys:**
- **macOS:** Hold `Fn` (Globe key) → speak → release
- **Windows:** Hold `Ctrl+Win` → speak → release

---

## Hotkey Configuration

### Modes

| Mode | Behavior | Best For |
|------|----------|----------|
| **Hold** (default) | Hold key → speak → release | Quick dictation |
| **Toggle** | Press → speak → press again | Longer recordings |

Both modes can be active simultaneously.

### Configuration

In `~/.pulsescribe/.env`:

```bash
# Hold-to-record (push-to-talk)
PULSESCRIBE_HOLD_HOTKEY=fn          # macOS: Fn/Globe key
PULSESCRIBE_HOLD_HOTKEY=ctrl+win    # Windows default

# Toggle (press-start, press-stop)
PULSESCRIBE_TOGGLE_HOTKEY=f19       # Recommended for macOS
PULSESCRIBE_TOGGLE_HOTKEY=ctrl+alt+r # Windows default
```

### Supported Formats

| Format | Examples |
|--------|----------|
| Function keys | `f1`, `f12`, `f19` |
| Single keys | `fn`, `capslock`, `space` |
| Combinations | `cmd+shift+r`, `ctrl+alt+space` |

### Visual Feedback

| Status | Color | Meaning |
|--------|-------|---------|
| Listening | 🌸 Pink | Hotkey pressed, waiting for speech |
| Recording | 🔴 Red | Speech detected, recording |
| Transcribing | 🟠 Orange | Processing text |
| Refining | 💜 Violet | LLM post-processing |
| Done | ✅ Green | Text pasted |
| Error | ❌ Red | Error occurred |

---

## Provider Selection

| Provider | Latency | Method | Best For |
|----------|---------|--------|----------|
| **Deepgram** | ~300ms | WebSocket | Daily use (recommended) |
| **Groq** | ~1s | REST | Free tier, fast |
| **OpenAI** | ~2-3s | REST | Highest quality |
| **Local** | varies | Whisper | Offline, privacy |

```bash
# In ~/.pulsescribe/.env
PULSESCRIBE_MODE=deepgram  # or: openai, groq, local
```

### Local Mode (Offline)

For offline transcription on Apple Silicon:

```bash
pip install mlx-whisper

# In ~/.pulsescribe/.env
PULSESCRIBE_MODE=local
PULSESCRIBE_LOCAL_BACKEND=mlx
PULSESCRIBE_LOCAL_MODEL=turbo
```

See [Local Backends](docs/LOCAL_BACKENDS.md) for all options.

---

## LLM Post-Processing

Enable refine to clean up transcriptions:

- Removes filler words (um, uh, like)
- Corrects grammar and punctuation
- Interprets voice commands ("new paragraph" → ¶)

```bash
# In ~/.pulsescribe/.env
PULSESCRIBE_REFINE=true
PULSESCRIBE_REFINE_PROVIDER=groq  # Free tier
```

### Context Awareness

PulseScribe detects the active app and adjusts writing style:

| Context | Apps | Style |
|---------|------|-------|
| `email` | Mail, Outlook | Formal, complete sentences |
| `chat` | Slack, Discord | Casual, concise |
| `code` | VS Code, Terminal | Technical, preserve terms |

### Voice Commands

With refine enabled, these spoken commands work:

| Speak | Result |
|-------|--------|
| "new paragraph" | ¶ |
| "comma" | `,` |
| "question mark" | `?` |

See [Configuration Reference](docs/CONFIGURATION.md) for all refine options.

---

## Known Limitations

| Area | Limitation |
|------|------------|
| **Platforms** | Linux not yet supported |
| **LLM Refine** | Requires network (no local LLM) |
| **Custom Vocabulary** | Not supported by OpenAI API |
| **Windows GPU** | Requires manual cuDNN installation |
| **Unsigned Builds** | macOS: Re-authorize Accessibility after each rebuild |

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| Module not found | `pip install -r requirements.txt` |
| API key missing | Set `DEEPGRAM_API_KEY` in `~/.pulsescribe/.env` |
| Microphone not working | macOS: `brew install portaudio` |
| No permission | Grant Microphone + Accessibility in System Settings |
| Auto-paste fails | Re-add app in Accessibility settings |

**Logs:** `~/.pulsescribe/logs/pulsescribe.log`

**Diagnostics:** Menu bar → Export Diagnostics…

For more solutions, see the [full troubleshooting section](#detailed-troubleshooting) below.

---

## Documentation

| Document | Description |
|----------|-------------|
| [Configuration Reference](docs/CONFIGURATION.md) | All settings and environment variables |
| [CLI Reference](docs/CLI_REFERENCE.md) | Command-line options for `transcribe.py` |
| [Local Backends](docs/LOCAL_BACKENDS.md) | Offline transcription setup |
| [Security & Privacy](docs/SECURITY.md) | Data handling and permissions |
| [Network Requirements](docs/NETWORK.md) | Endpoints and firewall rules |
| [Building macOS](docs/BUILDING_MACOS.md) | App bundle and DMG creation |
| [Building Windows](docs/BUILDING_WINDOWS.md) | EXE and installer creation |
| [Contributing](CONTRIBUTING.md) | Development setup and guidelines |
| [Architecture](CLAUDE.md) | Technical reference for developers |

---

## CLI Usage

For scripting and automation, use `transcribe.py` directly:

```bash
# Transcribe file
python transcribe.py audio.mp3

# Record from microphone
python transcribe.py --record --copy

# With LLM post-processing
python transcribe.py --record --refine --context email
```

See [CLI Reference](docs/CLI_REFERENCE.md) for all options.

---

## Detailed Troubleshooting

### Auto-Paste Not Working (macOS App Bundle)

**Symptom:** Log shows `AXIsProcessTrusted = False` even though enabled in Accessibility.

**Cause:** Unsigned PyInstaller bundles change hash on rebuild. macOS doesn't recognize the "new" app.

**Solution:**
1. System Settings → Privacy & Security → Accessibility
2. Remove PulseScribe (minus button)
3. Re-add PulseScribe (plus button or drag & drop)

> After every rebuild, you need to repeat this step until the app is code-signed.

### Clipboard Behavior

By default, transcribed text stays in clipboard after paste. To restore previous clipboard:

```bash
# In ~/.pulsescribe/.env
PULSESCRIBE_CLIPBOARD_RESTORE=true
```

### Log Files

```bash
# Main log
~/.pulsescribe/logs/pulsescribe.log

# Startup errors
~/.pulsescribe/startup.log
```

### Common Issues

| Problem | Solution |
|---------|----------|
| pystray/pillow missing (Windows) | `pip install pystray pillow` |
| ffmpeg missing | `brew install ffmpeg` (macOS) or `apt install ffmpeg` (Linux) |
| MLX model 404 | Use `PULSESCRIBE_LOCAL_MODEL=large` or full repo ID |
| Transcription slow | Use `deepgram` or `groq` mode, or smaller local model |
| Deepgram cuts last word | Update to latest version; streaming drains audio properly now |

---

## License

MIT License – see [LICENSE](LICENSE) for details.
