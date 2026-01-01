# CLI Reference

Complete reference for the `transcribe.py` command-line interface.

## Basic Usage

```bash
# Transcribe audio file
python transcribe.py audio.mp3

# Record from microphone
python transcribe.py --record

# Record and copy to clipboard
python transcribe.py --record --copy
```

## All Options

| Option | Short | Description |
|--------|-------|-------------|
| `--mode` | | Transcription provider: `openai`, `deepgram`, `groq`, `local` |
| `--model` | | Model name (overrides provider default) |
| `--record` | `-r` | Record from microphone instead of file |
| `--copy` | `-c` | Copy result to clipboard |
| `--language` | | Language code: `de`, `en`, `auto`, etc. |
| `--format` | | Output format: `text`, `json`, `srt`, `vtt` (API mode only) |
| `--refine` | | Enable LLM post-processing |
| `--no-refine` | | Disable LLM post-processing (overrides env) |
| `--refine-model` | | Model for post-processing |
| `--refine-provider` | | LLM provider: `groq`, `openai`, `openrouter`, `gemini` |
| `--context` | | Context for post-processing: `email`, `chat`, `code`, `default` |

## Provider-Specific Examples

### Deepgram (Recommended)

```bash
# WebSocket streaming (default, ~300ms latency)
python transcribe.py --record --mode deepgram

# With LLM post-processing
python transcribe.py --record --mode deepgram --refine
```

### OpenAI

```bash
# GPT-4o Transcribe (highest quality)
python transcribe.py audio.mp3 --mode openai

# Specific model
python transcribe.py audio.mp3 --mode openai --model gpt-4o-mini-transcribe
```

### Groq

```bash
# Whisper on LPU (~1s latency)
python transcribe.py --record --mode groq

# English-only (faster)
python transcribe.py --record --mode groq --model distil-whisper-large-v3-en
```

### Local (Offline)

```bash
# Default backend (auto-detected)
python transcribe.py --record --mode local

# Specific backend + model
python transcribe.py --record --mode local --model turbo
```

See [Local Backends](LOCAL_BACKENDS.md) for detailed local mode configuration.

## Output Formats

| Format | Description | Use Case |
|--------|-------------|----------|
| `text` | Plain text (default) | General use |
| `json` | JSON with timestamps | Integration |
| `srt` | SubRip subtitles | Video subtitles |
| `vtt` | WebVTT subtitles | Web video |

```bash
# Generate SRT subtitles
python transcribe.py video.mp4 --format srt > subtitles.srt

# JSON with word-level timestamps
python transcribe.py audio.mp3 --format json
```

> **Note:** `srt` and `vtt` formats are only available with API providers (not local mode).

## LLM Post-Processing

The `--refine` flag enables LLM post-processing to clean up transcriptions:

- Removes filler words (um, uh, like)
- Corrects grammar and punctuation
- Formats into proper paragraphs
- Interprets voice commands (see below)

```bash
# With Groq (fast, free tier)
python transcribe.py --record --refine --refine-provider groq

# With specific model
python transcribe.py --record --refine --refine-model gpt-4o
```

### Context Modes

| Context | Style | Auto-detected Apps |
|---------|-------|-------------------|
| `email` | Formal, complete sentences | Mail, Outlook, Spark |
| `chat` | Casual, concise | Slack, Discord, Messages |
| `code` | Technical, preserve terms | VS Code, Cursor, Terminal |
| `default` | Standard correction | All others |

```bash
# Force email context
python transcribe.py --record --refine --context email
```

### Voice Commands

With `--refine`, these spoken commands are interpreted:

| Speak | Result |
|-------|--------|
| "new paragraph" | ¶ (paragraph break) |
| "new line" | ↵ (line break) |
| "period" / "full stop" | `.` |
| "comma" | `,` |
| "question mark" | `?` |
| "exclamation mark" | `!` |
| "colon" | `:` |
| "semicolon" | `;` |

```bash
# Example
python transcribe.py --record --refine
# Speak: "Hello comma how are you question mark"
# Result: "Hello, how are you?"
```

## Environment Variables

CLI arguments take precedence over environment variables. See [Configuration Reference](CONFIGURATION.md) for all options.

**Most common:**

```bash
export PULSESCRIBE_MODE=deepgram
export PULSESCRIBE_LANGUAGE=en
export PULSESCRIBE_REFINE=true
```

## Exit Codes

| Code | Meaning |
|------|---------|
| `0` | Success |
| `1` | General error (missing file, API error, etc.) |
| `2` | Invalid arguments |

## Examples

### Quick Dictation

```bash
# Record → clipboard → paste manually
python transcribe.py --record --copy
```

### Batch Transcription

```bash
# Transcribe all MP3 files
for f in *.mp3; do
  python transcribe.py "$f" > "${f%.mp3}.txt"
done
```

### Generate Subtitles

```bash
# SRT for video editing
python transcribe.py interview.mp4 --format srt --mode deepgram > interview.srt
```

### Multilingual

```bash
# German transcription with English LLM post-processing
python transcribe.py --record --language de --refine
```

