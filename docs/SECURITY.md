# Security & Privacy

[🇩🇪 Deutsche Version](SICHERHEIT.md)

This document describes how PulseScribe handles your data, what permissions it requires, and security best practices.

## Data Handling

### Audio Data

| Aspect | Behavior |
|--------|----------|
| **Storage** | Audio is **never stored locally** by default |
| **Transmission** | Streamed directly to the selected provider (Deepgram/OpenAI/Groq) |
| **Local Mode** | With `PULSESCRIBE_MODE=local`, audio stays on your device |
| **Retention** | Check your provider's data retention policy |

### Transcripts

| Aspect | Behavior |
|--------|----------|
| **Clipboard** | Copied to system clipboard after transcription |
| **Logs** | May appear in debug logs (if `--debug` enabled) |
| **Storage** | Not stored permanently by PulseScribe |

### Log Files

Logs are stored in `~/.pulsescribe/logs/`:

```
~/.pulsescribe/
├── logs/
│   └── pulsescribe.log    # Rotating, max 1MB, 3 backups
└── startup.log            # Emergency startup log
```

**Log contents:**
- Timestamps and status messages
- Provider responses (without full transcripts in normal mode)
- Error messages and stack traces

**Logs do NOT contain:**
- API keys (masked in diagnostics export)
- Raw audio data

## API Key Storage

API keys are stored in **plaintext** in `~/.pulsescribe/.env`:

```bash
~/.pulsescribe/.env
├── DEEPGRAM_API_KEY=dg_...
├── OPENAI_API_KEY=sk-...
├── GROQ_API_KEY=gsk_...
└── OPENROUTER_API_KEY=sk-or-...
```

### Security Recommendations

1. **File permissions:** Ensure the file is only readable by you:
   ```bash
   chmod 600 ~/.pulsescribe/.env
   ```

2. **Never commit:** Add `.env` to `.gitignore` (already done in this repo)

3. **Use least privilege:** Create API keys with minimal required permissions

4. **Rotate regularly:** Periodically regenerate API keys

> **Note:** OS keychain integration is planned for a future release.

## Required Permissions

### macOS

| Permission | Reason | How to Enable |
|------------|--------|---------------|
| **Microphone** | Audio recording | System Settings → Privacy & Security → Microphone → Enable PulseScribe |
| **Accessibility** | Keyboard simulation for auto-paste (Cmd+V) | System Settings → Privacy & Security → Accessibility → Add PulseScribe |
| **Input Monitoring** | Hold-to-record hotkeys (Quartz Event Taps) | System Settings → Privacy & Security → Input Monitoring → Enable PulseScribe |

**Notes:**
- **Toggle hotkeys** (press-to-start, press-to-stop) do **not** require Accessibility/Input Monitoring – they use the Carbon API (`RegisterEventHotKey`)
- **Hold hotkeys** (push-to-talk) require Input Monitoring
- After rebuilding an unsigned app, you must re-authorize in Accessibility settings

### Windows

| Permission | Reason | How to Enable |
|------------|--------|---------------|
| **Microphone** | Audio recording | Granted on first use via Windows prompt |

**Notes:**
- No special permissions required for global hotkeys
- Some enterprise environments may block global hotkey listeners

## Network Security

See [NETWORK.md](NETWORK.md) for:
- Required endpoints and ports
- Proxy configuration
- Firewall rules
- Offline mode details

## Provider Security

| Provider | Data Handling | Privacy Policy |
|----------|---------------|----------------|
| **Deepgram** | Audio processed, not stored by default | [deepgram.com/privacy](https://deepgram.com/privacy) |
| **OpenAI** | Check API data usage policy | [openai.com/policies/privacy-policy](https://openai.com/policies/privacy-policy) |
| **Groq** | Check data retention settings | [groq.com/privacy-policy](https://groq.com/privacy-policy) |
| **Local** | All processing on-device | No external transmission |

> **Recommendation:** For sensitive data, use `PULSESCRIBE_MODE=local` to keep everything on your device.

## Diagnostics Export

The "Export Diagnostics" feature (Menu Bar → Export Diagnostics…) creates a ZIP file with:

- System information
- Sanitized configuration (API keys masked)
- Recent log entries (last 100 lines)

**Masked in export:**
- All API keys replaced with `***REDACTED***`
- User paths anonymized where possible

## Security Best Practices

1. **Use local mode for sensitive content**
   ```bash
   PULSESCRIBE_MODE=local
   ```

2. **Disable auto-paste in sensitive apps**
   - Use `--no-paste` flag or clipboard-only mode

3. **Review logs before sharing**
   - Check `~/.pulsescribe/logs/` for sensitive content

4. **Keep PulseScribe updated**
   - Security fixes are included in updates

5. **Use strong API key hygiene**
   - Different keys for different purposes
   - Regular rotation
   - Monitor usage dashboards

## Reporting Security Issues

For security vulnerabilities, please **do not** open a public GitHub issue.

Instead, contact the maintainers directly via email or use GitHub's private vulnerability reporting feature.

