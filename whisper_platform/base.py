"""Protocol-Definitionen für plattformspezifische Komponenten.

Diese Protocols definieren die Interfaces, die jede Plattform-Implementierung
erfüllen muss. Sie ermöglichen Typ-Checking ohne Laufzeit-Abhängigkeiten.

Usage:
    from platform.base import SoundPlayer

    class MacOSSoundPlayer(SoundPlayer):
        def play(self, name: str) -> None:
            ...
"""

from typing import Protocol, Callable, runtime_checkable


@runtime_checkable
class SoundPlayer(Protocol):
    """Interface für Sound-Playback.

    Implementierungen:
        - macOS: CoreAudio via AudioToolbox + afplay Fallback
        - Windows: winsound.PlaySound mit System-Sounds
    """

    def play(self, name: str) -> None:
        """Spielt einen benannten Sound ab.

        Args:
            name: Sound-Name ('ready', 'stop', 'error')

        Die Implementierung sollte nicht-blockierend sein.
        """
        ...


@runtime_checkable
class ClipboardHandler(Protocol):
    """Interface für Clipboard-Operationen.

    Implementierungen:
        - macOS: pbcopy/pbpaste via subprocess
        - Windows: win32clipboard oder pyperclip
    """

    def copy(self, text: str) -> bool:
        """Kopiert Text in die Zwischenablage.

        Args:
            text: Der zu kopierende Text

        Returns:
            True bei Erfolg, False bei Fehler
        """
        ...

    def paste(self) -> str | None:
        """Liest Text aus der Zwischenablage.

        Returns:
            Clipboard-Inhalt oder None bei Fehler/leer
        """
        ...


@runtime_checkable
class AppDetector(Protocol):
    """Interface für App-Detection.

    Ermittelt die aktuell aktive/fokussierte Anwendung.

    Implementierungen:
        - macOS: NSWorkspace.sharedWorkspace().frontmostApplication()
        - Windows: win32gui.GetForegroundWindow() + psutil
    """

    def get_frontmost_app(self) -> str | None:
        """Ermittelt den Namen der aktiven App.

        Returns:
            App-Name (z.B. 'Slack', 'VS Code') oder None bei Fehler
        """
        ...


@runtime_checkable
class DaemonController(Protocol):
    """Interface für Daemon-Prozess-Kontrolle.

    Verwaltet den Aufnahme-Daemon (Start, Stop, Status).

    Implementierungen:
        - macOS: Double-Fork + SIGUSR1 Signale
        - Windows: CREATE_NEW_PROCESS_GROUP + Named Events
    """

    def start(self, command: list[str]) -> int | None:
        """Startet einen Daemon-Prozess.

        Args:
            command: Kommando als Liste (z.B. ['python', 'transcribe.py', '--daemon'])

        Returns:
            PID des gestarteten Prozesses oder None bei Fehler
        """
        ...

    def stop(self, pid: int) -> bool:
        """Stoppt einen Daemon-Prozess.

        Args:
            pid: Prozess-ID des zu stoppenden Daemons

        Returns:
            True wenn erfolgreich gestoppt, False bei Fehler
        """
        ...

    def is_running(self, pid: int) -> bool:
        """Prüft ob ein Daemon-Prozess läuft.

        Args:
            pid: Prozess-ID

        Returns:
            True wenn Prozess läuft, False sonst
        """
        ...


@runtime_checkable
class HotkeyListener(Protocol):
    """Interface für globale Hotkey-Registrierung.

    Implementierungen:
        - macOS: QuickMacHotKey (Carbon API)
        - Windows: pynput oder keyboard
    """

    def register(self) -> None:
        """Registriert den Hotkey."""
        ...

    def unregister(self) -> None:
        """Deregistriert den Hotkey."""
        ...

    def run(self) -> None:
        """Startet den Event-Loop (blockiert)."""
        ...


# Callback-Typen für Hotkey-Listener
HotkeyCallback = Callable[[], None]


__all__ = [
    "SoundPlayer",
    "ClipboardHandler",
    "AppDetector",
    "DaemonController",
    "HotkeyListener",
    "HotkeyCallback",
]
