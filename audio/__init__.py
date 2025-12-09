"""Audio-Modul für whisper_go.

Bietet Funktionen für die Mikrofon-Aufnahme.

Usage:
    from audio import record_audio, AudioRecorder

    # Interactive Aufnahme
    path = record_audio()

    # Oder mit mehr Kontrolle
    recorder = AudioRecorder()
    recorder.start()
    # ... später ...
    path = recorder.stop()
"""

from .recording import (
    record_audio,
    record_audio_daemon,
    AudioRecorder,
    WHISPER_SAMPLE_RATE,
    WHISPER_CHANNELS,
    WHISPER_BLOCKSIZE,
)

__all__ = [
    "record_audio",
    "record_audio_daemon",
    "AudioRecorder",
    "WHISPER_SAMPLE_RATE",
    "WHISPER_CHANNELS",
    "WHISPER_BLOCKSIZE",
]
