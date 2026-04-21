from enum import Enum, auto
from dataclasses import dataclass
from typing import Any


class AppState(Enum):
    IDLE = "idle"
    LOADING = "loading"  # Model is being loaded/downloaded
    LISTENING = "listening"  # Hotkey pressed, waiting for speech
    RECORDING = "recording"  # Speech detected
    TRANSCRIBING = "transcribing"
    REFINING = "refining"
    DONE = "done"
    NO_SPEECH = "no_speech"  # Recording finished but no usable transcript was produced
    ERROR = "error"


class MessageType(Enum):
    STATUS_UPDATE = auto()
    TRANSCRIPT_RESULT = auto()
    AUDIO_LEVEL = auto()
    ERROR = auto()


@dataclass
class DaemonMessage:
    type: MessageType
    payload: Any = None
