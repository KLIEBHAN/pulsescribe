"""Audio-Aufnahme für whisper_go.

Enthält Funktionen und Klassen für die Mikrofon-Aufnahme
mit sounddevice.
"""

import logging
import os
import signal
import tempfile
import threading
import time
from pathlib import Path

logger = logging.getLogger("whisper_go")

# =============================================================================
# Audio-Konfiguration
# =============================================================================

# Whisper erwartet Audio mit 16kHz – andere Sampleraten führen zu schlechteren Ergebnissen
WHISPER_SAMPLE_RATE = 16000
WHISPER_CHANNELS = 1
WHISPER_BLOCKSIZE = 1024

# IPC-Dateien
PID_FILE = Path("/tmp/whisper_go.pid")
TEMP_RECORDING_FILENAME = "whisper_recording.wav"


def _get_session_id() -> str:
    """Holt Session-ID für Logging."""
    try:
        from utils.logging import get_session_id
        return get_session_id()
    except ImportError:
        return "unknown"


def _log(message: str) -> None:
    """Status-Meldung auf stderr."""
    import sys
    print(message, file=sys.stderr)


def _play_sound(name: str) -> None:
    """Spielt benannten Sound ab."""
    try:
        from whisper_platform import get_sound_player
        player = get_sound_player()
        player.play(name)
    except Exception:
        pass


class AudioRecorder:
    """Wiederverwendbare Audio-Aufnahme Klasse.

    Kann für CLI und Daemon verwendet werden.

    Usage:
        recorder = AudioRecorder()
        recorder.start()
        # ... später ...
        path = recorder.stop()
    """

    def __init__(
        self,
        sample_rate: int = WHISPER_SAMPLE_RATE,
        channels: int = WHISPER_CHANNELS,
        blocksize: int = WHISPER_BLOCKSIZE,
    ):
        self.sample_rate = sample_rate
        self.channels = channels
        self.blocksize = blocksize

        self._recorded_chunks: list = []
        self._stream = None
        self._recording_start: float = 0
        self._stop_event = threading.Event()

    def _audio_callback(self, indata, _frames, _time_info, _status):
        """Callback: Sammelt Audio-Chunks während der Aufnahme."""
        self._recorded_chunks.append(indata.copy())

    def start(self, play_ready_sound: bool = True) -> None:
        """Startet die Aufnahme.

        Args:
            play_ready_sound: Wenn True, wird Ready-Sound abgespielt
        """
        import sounddevice as sd

        self._recorded_chunks = []
        self._stop_event.clear()
        self._recording_start = time.perf_counter()

        self._stream = sd.InputStream(
            samplerate=self.sample_rate,
            channels=self.channels,
            blocksize=self.blocksize,
            dtype="float32",
            callback=self._audio_callback,
        )
        self._stream.start()

        if play_ready_sound:
            _play_sound("ready")

        logger.info(f"[{_get_session_id()}] Aufnahme gestartet")

    def stop(self, output_path: Path | None = None) -> Path:
        """Stoppt die Aufnahme und speichert die Audiodatei.

        Args:
            output_path: Optionaler Pfad für die Ausgabedatei

        Returns:
            Pfad zur gespeicherten WAV-Datei

        Raises:
            ValueError: Wenn keine Audiodaten aufgenommen wurden
        """
        import numpy as np
        import soundfile as sf

        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None

        self._stop_event.set()

        recording_duration = time.perf_counter() - self._recording_start
        logger.info(f"[{_get_session_id()}] Aufnahme: {recording_duration:.1f}s")

        _play_sound("stop")

        if not self._recorded_chunks:
            logger.error(f"[{_get_session_id()}] Keine Audiodaten aufgenommen")
            raise ValueError("Keine Audiodaten aufgenommen.")

        audio_data = np.concatenate(self._recorded_chunks)

        if output_path is None:
            output_path = Path(tempfile.gettempdir()) / TEMP_RECORDING_FILENAME

        sf.write(output_path, audio_data, self.sample_rate)

        return output_path

    def wait_for_stop(self, timeout: float | None = None) -> bool:
        """Wartet auf Stop-Event.

        Args:
            timeout: Maximale Wartezeit in Sekunden

        Returns:
            True wenn gestoppt, False bei Timeout
        """
        return self._stop_event.wait(timeout=timeout)

    def request_stop(self) -> None:
        """Signalisiert, dass die Aufnahme beendet werden soll."""
        self._stop_event.set()

    @property
    def is_recording(self) -> bool:
        """True wenn aktuell aufgenommen wird."""
        return self._stream is not None and self._stream.active

    @property
    def chunks(self) -> list:
        """Gibt die bisher aufgenommenen Chunks zurück."""
        return self._recorded_chunks


def record_audio() -> Path:
    """Nimmt Audio vom Mikrofon auf (Enter startet, Enter stoppt).

    Gibt Pfad zur temporären WAV-Datei zurück.
    """
    import numpy as np
    import sounddevice as sd
    import soundfile as sf

    recorded_chunks: list = []

    def on_audio_chunk(indata, _frames, _time, _status):
        recorded_chunks.append(indata.copy())

    _log("🎤 Drücke ENTER um die Aufnahme zu starten...")
    input()

    _play_sound("ready")
    _log("🔴 Aufnahme läuft... Drücke ENTER zum Beenden.")
    with sd.InputStream(
        samplerate=WHISPER_SAMPLE_RATE,
        channels=1,
        dtype="float32",
        callback=on_audio_chunk,
    ):
        input()

    _log("✅ Aufnahme beendet.")
    _play_sound("stop")

    if not recorded_chunks:
        raise ValueError("Keine Audiodaten aufgenommen. Bitte länger aufnehmen.")

    audio_data = np.concatenate(recorded_chunks)
    output_path = Path(tempfile.gettempdir()) / TEMP_RECORDING_FILENAME
    sf.write(output_path, audio_data, WHISPER_SAMPLE_RATE)

    return output_path


def record_audio_daemon() -> Path:
    """Daemon-Modus: Nimmt Audio auf bis SIGUSR1 empfangen wird.

    Schreibt PID-File für externe Steuerung (Raycast).
    Kein globaler State – verwendet Closure für Signal-Flag.
    """
    import numpy as np
    import sounddevice as sd
    import soundfile as sf

    recorded_chunks: list = []
    recording_start = time.perf_counter()
    should_stop = False
    session_id = _get_session_id()

    def on_audio_chunk(indata, _frames, _time_info, _status):
        """Callback: Sammelt Audio-Chunks während der Aufnahme."""
        recorded_chunks.append(indata.copy())

    def handle_stop_signal(_signum: int, _frame) -> None:
        """Signal-Handler: Setzt Stop-Flag bei SIGUSR1."""
        nonlocal should_stop
        logger.debug(f"[{session_id}] SIGUSR1 empfangen")
        should_stop = True

    pid = os.getpid()
    logger.info(f"[{session_id}] Daemon gestartet (PID: {pid})")

    # PID-File schreiben für Raycast
    PID_FILE.write_text(str(pid))

    # Signal-Handler registrieren
    signal.signal(signal.SIGUSR1, handle_stop_signal)

    _play_sound("ready")
    _log("🎤 Daemon: Aufnahme gestartet (warte auf SIGUSR1)...")
    logger.info(f"[{session_id}] Aufnahme gestartet")

    try:
        with sd.InputStream(
            samplerate=WHISPER_SAMPLE_RATE,
            channels=1,
            dtype="float32",
            callback=on_audio_chunk,
        ):
            while not should_stop:
                sd.sleep(100)  # 100ms warten, dann Signal prüfen
    finally:
        # PID-File aufräumen
        if PID_FILE.exists():
            PID_FILE.unlink()

    recording_duration = time.perf_counter() - recording_start
    logger.info(f"[{session_id}] Aufnahme: {recording_duration:.1f}s")
    _log("✅ Daemon: Aufnahme beendet.")
    _play_sound("stop")

    if not recorded_chunks:
        logger.error(f"[{session_id}] Keine Audiodaten aufgenommen")
        raise ValueError("Keine Audiodaten aufgenommen.")

    audio_data = np.concatenate(recorded_chunks)
    output_path = Path(tempfile.gettempdir()) / TEMP_RECORDING_FILENAME
    sf.write(output_path, audio_data, WHISPER_SAMPLE_RATE)
    return output_path
