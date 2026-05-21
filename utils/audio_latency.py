"""Audio stream latency helpers shared by Windows daemon and providers.

Keeps PortAudio/WASAPI latency hints and block-size choices in one place so the
Windows daemon and Deepgram streaming fallback cannot drift apart.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

WINDOWS_AUDIO_BLOCK_MS = 20
WINDOWS_INPUT_LATENCY = "low"

_logger = logging.getLogger("pulsescribe.audio_latency")


def windows_audio_blocksize(sample_rate: int) -> int:
    """Return a small Windows blocksize for snappy VAD/overlay feedback."""
    return max(1, int(sample_rate * WINDOWS_AUDIO_BLOCK_MS / 1000))


def platform_audio_blocksize(sample_rate: int, default_blocksize: int) -> int:
    """Return the platform-appropriate InputStream blocksize."""
    if sys.platform == "win32":
        return windows_audio_blocksize(sample_rate)
    return max(1, default_blocksize)


def create_low_latency_input_stream(
    sd: Any,
    *,
    logger: logging.Logger | None = None,
    **kwargs: Any,
) -> Any:
    """Create an InputStream with Windows low-latency hint and safe fallback.

    Some Windows audio drivers reject PortAudio's ``latency='low'`` hint. In
    that case we fall back to the driver default instead of failing microphone
    startup. Non-Windows platforms use the default stream unchanged.
    """
    if sys.platform != "win32":
        return sd.InputStream(**kwargs)

    log = logger or _logger
    try:
        return sd.InputStream(**kwargs, latency=WINDOWS_INPUT_LATENCY)
    except Exception as exc:
        log.debug("Low-Latency InputStream nicht verfügbar, fallback: %s", exc)
        return sd.InputStream(**kwargs)


__all__ = [
    "WINDOWS_AUDIO_BLOCK_MS",
    "WINDOWS_INPUT_LATENCY",
    "create_low_latency_input_stream",
    "platform_audio_blocksize",
    "windows_audio_blocksize",
]
