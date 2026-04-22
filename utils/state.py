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


class DaemonErrorCode(str, Enum):
    MISSING_API_KEY = "missing_api_key"
    BUSY = "busy"
    TIMEOUT = "timeout"
    INVALID_PROVIDER = "invalid_provider"
    INPUT_MONITORING = "input_monitoring"
    ACCESSIBILITY_PERMISSION = "accessibility_permission"
    MICROPHONE_PERMISSION = "microphone_permission"
    MICROPHONE_UNAVAILABLE = "microphone_unavailable"
    CONNECTION = "connection"
    MISSING_DEPENDENCY = "missing_dependency"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class DaemonStatusError:
    code: DaemonErrorCode
    detail: str | None = None

    def __str__(self) -> str:
        return self.detail or self.code.value


@dataclass
class DaemonMessage:
    type: MessageType
    payload: Any = None
