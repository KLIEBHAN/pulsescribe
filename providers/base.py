"""Protocol-Definition für Transkriptions-Provider.

Alle Provider müssen dieses Interface implementieren.
"""

import os
from pathlib import Path
from typing import Protocol, runtime_checkable


@runtime_checkable
class TranscriptionProvider(Protocol):
    """Interface für Transkriptions-Provider.

    Jeder Provider muss mindestens transcribe() implementieren.
    Streaming-Provider implementieren zusätzlich transcribe_stream().
    """

    def transcribe(
        self,
        audio_path: Path,
        model: str | None = None,
        language: str | None = None,
    ) -> str:
        """Transkribiert eine Audio-Datei.

        Args:
            audio_path: Pfad zur Audio-Datei
            model: Modell-Name (optional, nutzt Provider-Default)
            language: Sprachcode (z.B. 'de', 'en') oder None für Auto-Detection

        Returns:
            Transkribierter Text

        Raises:
            ValueError: Bei fehlenden Credentials oder ungültiger Datei
            RuntimeError: Bei API-Fehlern
        """
        ...

    def supports_streaming(self) -> bool:
        """Gibt zurück ob der Provider Streaming unterstützt.

        Returns:
            True wenn transcribe_stream() verfügbar ist
        """
        ...

    @property
    def name(self) -> str:
        """Provider-Name für Logging."""
        ...

    @property
    def default_model(self) -> str:
        """Standard-Modell für diesen Provider."""
        ...


class EnvValidatedProvider:
    """Shared base for providers that require a single env-backed credential."""

    api_key_env_var: str = ""
    missing_api_key_message: str = ""

    def __init__(self) -> None:
        self._validated = False

    def _validate(self) -> None:
        if self._validated:
            return
        if not self.api_key_env_var:
            raise RuntimeError("api_key_env_var ist nicht konfiguriert")
        if not os.getenv(self.api_key_env_var):
            raise ValueError(self.missing_api_key_message)
        self._validated = True

    def supports_streaming(self) -> bool:
        """Env-validated REST providers do not stream by default."""
        return False


__all__ = ["TranscriptionProvider", "EnvValidatedProvider"]
