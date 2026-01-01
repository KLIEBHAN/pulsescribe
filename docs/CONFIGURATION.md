# Configuration Reference

[🇩🇪 Deutsche Version](KONFIGURATION.md)

Complete reference for all PulseScribe configuration options. Settings can be configured via environment variables or `~/.pulsescribe/.env`.

## Quick Setup

```bash
# Copy example configuration
cp .env.example ~/.pulsescribe/.env

# Edit with your API keys
nano ~/.pulsescribe/.env
```

**Priority order:** CLI arguments > Environment variables > `.env` file > Defaults

---

## API Keys

At least one API key is required for cloud transcription:

| Variable | Provider | Get Key |
|----------|----------|---------|
| `DEEPGRAM_API_KEY` | Deepgram (recommended) | [console.deepgram.com](https://console.deepgram.com) – $200 free credit |
| `OPENAI_API_KEY` | OpenAI | [platform.openai.com](https://platform.openai.com/api-keys) |
| `GROQ_API_KEY` | Groq | [console.groq.com](https://console.groq.com) – free tier |
| `OPENROUTER_API_KEY` | OpenRouter (for Refine) | [openrouter.ai](https://openrouter.ai/keys) |
| `GEMINI_API_KEY` | Google Gemini (for Refine) | [aistudio.google.com](https://aistudio.google.com/apikey) |

---

## Transcription

### Provider Selection

| Variable | Values | Default | Description |
|----------|--------|---------|-------------|
| `PULSESCRIBE_MODE` | `deepgram`, `openai`, `groq`, `local` | `openai` | Transcription provider |
| `PULSESCRIBE_MODEL` | Provider-specific | Auto | Override provider's default model |
| `PULSESCRIBE_LANGUAGE` | `de`, `en`, `auto`, etc. | `auto` | Language code (explicit improves accuracy) |
| `PULSESCRIBE_STREAMING` | `true`, `false` | `true` | WebSocket streaming for Deepgram |

### Provider-Specific Models

| Provider | Models | Recommended |
|----------|--------|-------------|
| **Deepgram** | `nova-3`, `nova-2` | `nova-3` |
| **OpenAI** | `gpt-4o-transcribe`, `gpt-4o-mini-transcribe`, `whisper-1` | `gpt-4o-transcribe` |
| **Groq** | `whisper-large-v3`, `distil-whisper-large-v3-en` | `whisper-large-v3` |
| **Local** | `tiny`, `base`, `small`, `medium`, `large`, `turbo` | `turbo` |

---

## LLM Post-Processing (Refine)

Removes filler words, fixes grammar, formats paragraphs:

| Variable | Values | Default | Description |
|----------|--------|---------|-------------|
| `PULSESCRIBE_REFINE` | `true`, `false` | `false` | Enable LLM post-processing |
| `PULSESCRIBE_REFINE_PROVIDER` | `groq`, `openai`, `openrouter`, `gemini` | `openai` | LLM provider |
| `PULSESCRIBE_REFINE_MODEL` | Provider-specific | `openai/gpt-oss-120b` | Model for refine |

### Refine Models by Provider

| Provider | Recommended Models |
|----------|-------------------|
| **Groq** | `llama-3.3-70b-versatile`, `mixtral-8x7b-32768` |
| **OpenAI** | `gpt-4o`, `gpt-4o-mini` |
| **OpenRouter** | `openai/gpt-4o`, `anthropic/claude-3.5-sonnet` |
| **Gemini** | `gemini-3-flash-preview`, `gemini-3-pro-preview` |

### Context Awareness

| Variable | Values | Description |
|----------|--------|-------------|
| `PULSESCRIBE_CONTEXT` | `email`, `chat`, `code`, `default` | Force context (overrides auto-detection) |
| `PULSESCRIBE_APP_CONTEXTS` | JSON | Custom app-to-context mappings |

**Auto-detection:** PulseScribe detects the active app and adjusts writing style:
- **email:** Mail, Outlook, Spark → Formal, complete sentences
- **chat:** Slack, Discord, Messages → Casual, concise
- **code:** VS Code, Cursor, Terminal → Technical, preserve terms

Example custom mapping:
```bash
PULSESCRIBE_APP_CONTEXTS='{"MyApp": "chat", "CustomIDE": "code"}'
```

### OpenRouter Options

| Variable | Description |
|----------|-------------|
| `OPENROUTER_PROVIDER_ORDER` | Provider order, e.g., `Together,DeepInfra` |
| `OPENROUTER_ALLOW_FALLBACKS` | Allow fallback providers: `true`/`false` |

---

## Hotkeys

### Dual-Hotkey Mode (Recommended)

| Variable | Description | Example |
|----------|-------------|---------|
| `PULSESCRIBE_TOGGLE_HOTKEY` | Press-to-start, press-to-stop | `f19`, `ctrl+alt+r` |
| `PULSESCRIBE_HOLD_HOTKEY` | Hold-to-record (push-to-talk) | `fn`, `ctrl+win` |

Both hotkeys can be active simultaneously.

### Legacy Single-Hotkey Mode

| Variable | Description |
|----------|-------------|
| `PULSESCRIBE_HOTKEY` | Single hotkey (overridden by TOGGLE/HOLD) |
| `PULSESCRIBE_HOTKEY_MODE` | `toggle` or `hold` |

### Supported Hotkey Formats

| Format | Examples |
|--------|----------|
| Function keys | `f1`, `f12`, `f19` |
| Single keys | `fn`, `capslock`, `space`, `tab`, `esc` |
| Combinations | `cmd+shift+r`, `ctrl+alt+space`, `ctrl+win` |

### Platform Defaults

| Platform | Toggle | Hold |
|----------|--------|------|
| **macOS** | (none) | `fn` (Globe key) |
| **Windows** | `ctrl+alt+r` | `ctrl+win` |

---

## UI & Behavior

| Variable | Values | Default | Description |
|----------|--------|---------|-------------|
| `PULSESCRIBE_OVERLAY` | `true`, `false` | `true` | Show animated overlay |
| `PULSESCRIBE_DOCK_ICON` | `true`, `false` | `true` | Show Dock icon (macOS) |
| `PULSESCRIBE_SHOW_RTF` | `true`, `false` | `false` | Show Real-Time Factor after transcription |
| `PULSESCRIBE_CLIPBOARD_RESTORE` | `true`, `false` | `false` | Restore previous clipboard after paste |

### RTF (Real-Time Factor)

Performance indicator shown in overlay when `PULSESCRIBE_SHOW_RTF=true`:

| RTF | Meaning | Example (10s audio) |
|-----|---------|---------------------|
| 0.3x | 3× faster than real-time | 3s processing |
| 1.0x | Real-time | 10s processing |
| 2.0x | 2× slower than real-time | 20s processing |

---

## Local Mode

See [LOCAL_BACKENDS.md](LOCAL_BACKENDS.md) for detailed local mode configuration.

### Quick Reference

| Variable | Values | Default | Description |
|----------|--------|---------|-------------|
| `PULSESCRIBE_LOCAL_BACKEND` | `whisper`, `faster`, `mlx`, `lightning`, `auto` | `auto` | Local backend |
| `PULSESCRIBE_LOCAL_MODEL` | `tiny`...`large`, `turbo` | `turbo` | Model size |
| `PULSESCRIBE_DEVICE` | `auto`, `mps`, `cpu`, `cuda` | `auto` | Compute device |
| `PULSESCRIBE_LOCAL_WARMUP` | `true`, `false`, `auto` | `auto` | Warmup on startup |

---

## File Paths

| Path | Description |
|------|-------------|
| `~/.pulsescribe/` | User configuration directory |
| `~/.pulsescribe/.env` | User settings (priority 1) |
| `~/.pulsescribe/logs/pulsescribe.log` | Main log file (rotating, max 1MB) |
| `~/.pulsescribe/startup.log` | Emergency startup log |
| `~/.pulsescribe/vocabulary.json` | Custom vocabulary |
| `~/.pulsescribe/prompts.toml` | Custom prompts |

---

## Custom Vocabulary

Improve recognition of domain-specific terms in `~/.pulsescribe/vocabulary.json`:

```json
{
  "keywords": ["Anthropic", "Claude", "Kubernetes", "OAuth", "GraphQL"]
}
```

**Supported by:** Deepgram, Local Whisper
**Not supported:** OpenAI API (use Refine for corrections)

---

## Custom Prompts

Customize LLM prompts in `~/.pulsescribe/prompts.toml`:

```toml
[voice_commands]
instruction = """
Custom voice command instructions...
"""

[prompts.email]
prompt = """
Custom email context prompt...
"""

[prompts.chat]
prompt = """
Custom chat context prompt...
"""

[app_contexts]
"MyApp" = "email"
CustomIDE = "code"
```

**Priority:** CLI > ENV > Custom TOML > Hardcoded defaults

---

## Example Configurations

### Fastest Setup (Deepgram + Groq)

```bash
DEEPGRAM_API_KEY=your_key
GROQ_API_KEY=your_groq_key

PULSESCRIBE_MODE=deepgram
PULSESCRIBE_LANGUAGE=en
PULSESCRIBE_REFINE=true
PULSESCRIBE_REFINE_PROVIDER=groq
```

### Privacy-Focused (Local Only)

```bash
PULSESCRIBE_MODE=local
PULSESCRIBE_LOCAL_BACKEND=mlx
PULSESCRIBE_LOCAL_MODEL=turbo
PULSESCRIBE_LANGUAGE=de
# No API keys needed
```

### High Quality (OpenAI)

```bash
OPENAI_API_KEY=your_key

PULSESCRIBE_MODE=openai
PULSESCRIBE_MODEL=gpt-4o-transcribe
PULSESCRIBE_REFINE=true
PULSESCRIBE_REFINE_PROVIDER=openai
```

