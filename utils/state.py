from enum import Enum, auto
from dataclasses import dataclass
from typing import Any

class AppState(Enum):
    IDLE = "idle"
    LISTENING = "listening" # Hotkey pressed, waiting for speech
    RECORDING = "recording" # Speech detected
    TRANSCRIBING = "transcribing"
    REFINING = "refining"
    DONE = "done"
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
