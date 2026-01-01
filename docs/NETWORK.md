# Network Requirements

[🇩🇪 Deutsche Version](NETZWERK.md)

This document describes the network requirements for PulseScribe, including required endpoints, proxy configuration, and offline mode details.

## Required Endpoints

### Transcription Providers

| Provider | Endpoint | Port | Protocol |
|----------|----------|------|----------|
| **Deepgram** | `api.deepgram.com` | 443 | HTTPS / WSS |
| **OpenAI** | `api.openai.com` | 443 | HTTPS |
| **Groq** | `api.groq.com` | 443 | HTTPS |

### LLM Refine Providers

| Provider | Endpoint | Port | Protocol |
|----------|----------|------|----------|
| **OpenAI** | `api.openai.com` | 443 | HTTPS |
| **Groq** | `api.groq.com` | 443 | HTTPS |
| **OpenRouter** | `openrouter.ai` | 443 | HTTPS |

### Model Downloads (Local Mode)

| Backend | Endpoint | Purpose |
|---------|----------|---------|
| **Whisper/MLX** | `huggingface.co` | Model weights download |
| **Lightning** | `huggingface.co` | Model weights download |

## Firewall Configuration

### Minimum Required (Cloud Mode)

Allow outbound HTTPS (port 443) to:
```
api.deepgram.com
api.openai.com
api.groq.com
openrouter.ai
```

### For Local Mode Setup

Additionally allow:
```
huggingface.co
*.hf.co
```

> **Note:** After initial model download, local mode works completely offline.

## Proxy Configuration

PulseScribe respects standard environment variables for proxy configuration:

```bash
# HTTP/HTTPS Proxy
export HTTP_PROXY=http://proxy.example.com:8080
export HTTPS_PROXY=http://proxy.example.com:8080

# No proxy for specific hosts
export NO_PROXY=localhost,127.0.0.1
```

### WebSocket Proxy (Deepgram Streaming)

For Deepgram's WebSocket streaming, ensure your proxy supports:
- WebSocket upgrade (HTTP 101)
- WSS (WebSocket Secure) connections

If WebSocket proxying fails, PulseScribe falls back to REST API automatically:
```bash
PULSESCRIBE_STREAMING=false
```

## Offline Mode

### Complete Offline Operation

With `PULSESCRIBE_MODE=local`, PulseScribe works entirely offline **after initial setup**:

```bash
# One-time setup (requires internet)
pip install -r requirements.txt
export PULSESCRIBE_MODE=local
export PULSESCRIBE_LOCAL_BACKEND=mlx  # or: whisper, faster, lightning
python pulsescribe_daemon.py  # Downloads model on first run

# After model download: works offline
```

### Model Cache Locations

| Backend | Cache Location |
|---------|----------------|
| **whisper** | `~/.cache/whisper/` |
| **faster-whisper** | `~/.cache/huggingface/` |
| **mlx-whisper** | `~/.cache/huggingface/` |
| **lightning** | `~/.pulsescribe/lightning_models/` |

### Offline Limitations

- **LLM Refine:** Requires network (no local LLM support yet)
- **Model downloads:** First run requires internet
- **Custom vocabulary:** Works offline (stored locally)

## Connection Behavior

### Deepgram Streaming

| Scenario | Behavior |
|----------|----------|
| Connection lost during recording | Automatic reconnect attempt |
| Server timeout | Falls back to REST API |
| DNS failure | Error shown, recording stopped |

### Timeouts

| Operation | Timeout |
|-----------|---------|
| WebSocket connect | 10 seconds |
| REST API call | 30 seconds |
| Model download | No timeout (progress shown) |

## Bandwidth Usage

### Typical Usage (Deepgram Streaming)

| Audio Duration | Upload | Download |
|----------------|--------|----------|
| 10 seconds | ~160 KB | ~5 KB |
| 1 minute | ~960 KB | ~10 KB |
| 5 minutes | ~4.8 MB | ~20 KB |

> Audio is streamed at 16kHz mono, 16-bit PCM (~32 KB/sec).

### Model Download Sizes

| Model | Size |
|-------|------|
| tiny | ~75 MB |
| base | ~150 MB |
| small | ~500 MB |
| medium | ~1.5 GB |
| large/large-v3 | ~3 GB |
| turbo | ~1.5 GB |

## Troubleshooting

### Connection Issues

| Problem | Solution |
|---------|----------|
| `Connection refused` | Check firewall, verify endpoint is reachable |
| `SSL certificate error` | Update CA certificates, check system time |
| `Timeout` | Check proxy settings, try REST fallback |
| `DNS resolution failed` | Check network connection, try direct IP (not recommended) |

### Testing Connectivity

```bash
# Test Deepgram
curl -I https://api.deepgram.com

# Test OpenAI
curl -I https://api.openai.com

# Test Groq
curl -I https://api.groq.com

# Test with proxy
curl -I --proxy http://proxy:8080 https://api.deepgram.com
```

### Diagnostic Logs

Network issues are logged in `~/.pulsescribe/logs/pulsescribe.log`:

```bash
# View recent network errors
grep -i "connection\|timeout\|error" ~/.pulsescribe/logs/pulsescribe.log | tail -20
```

