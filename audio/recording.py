"""Audio-Aufnahme für pulsescribe.

Enthält Funktionen und Klassen für die Mikrofon-Aufnahme
mit sounddevice.
"""

import logging
import tempfile
import threading
import time
from pathlib import Path

# Zentrale Konfiguration importieren
from config import (
    WHISPER_SAMPLE_RATE,
    WHISPER_CHANNELS,
    WHISPER_BLOCKSIZE,
    TEMP_RECORDING_FILENAME,
    get_input_device,
    reset_input_device_cache,
)
from utils.logging import get_session_id

logger = logging.getLogger("pulsescribe")


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
    except Exception as e:
        logger.debug(f"Sound '{name}' konnte nicht abgespielt werden: {e}")


def _resolve_input_stream_config(
    sample_rate: int,
    device: int | None = None,
) -> tuple[int | None, int]:
    """Ermittelt Device + Sample-Rate fuer die Aufnahme.

    Ohne explizites Device nutzen wir die zentrale, plattformbewusste
    Input-Device-Erkennung. Dadurch verhalten sich CLI-Aufnahme und Daemons
    konsistent, insbesondere auf Windows ohne gesetztes Default-Mikrofon.
    """
    if device is not None:
        return device, sample_rate

    resolved_device, resolved_sample_rate = get_input_device()
    if sample_rate != WHISPER_SAMPLE_RATE:
        return resolved_device, sample_rate
    return resolved_device, resolved_sample_rate


class AudioRecorder:
    """Wiederverwendbare Audio-Aufnahme Klasse.

    Kann für CLI verwendet werden.

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
        device: int | None = None,
    ):
        self.sample_rate = sample_rate
        self.channels = channels
        self.blocksize = blocksize
        self.device = device

        self._recorded_chunks: list = []
        self._chunks_lock = threading.Lock()  # Thread-safety für Audio-Chunks
        self._stream = None
        self._recording_start: float = 0
        self._stop_event = threading.Event()
        self._active_sample_rate = sample_rate
        self._session_active = False

    def _audio_callback(self, indata, _frames, _time_info, _status):
        """Callback: Sammelt Audio-Chunks während der Aufnahme."""
        with self._chunks_lock:
            self._recorded_chunks.append(indata.copy())

    def start(self, play_ready_sound: bool = True) -> None:
        """Startet die Aufnahme.

        Args:
            play_ready_sound: Wenn True, wird Ready-Sound abgespielt
        """
        import sounddevice as sd

        with self._chunks_lock:
            self._recorded_chunks = []
        self._stop_event.clear()
        self._recording_start = 0
        self._session_active = False
        input_device, active_sample_rate = _resolve_input_stream_config(
            self.sample_rate,
            self.device,
        )
        self._active_sample_rate = active_sample_rate
        should_retry_with_fresh_device = (
            self.device is None and self.sample_rate == WHISPER_SAMPLE_RATE
        )

        while True:
            stream = None
            try:
                stream = sd.InputStream(
                    device=input_device,
                    samplerate=active_sample_rate,
                    channels=self.channels,
                    blocksize=self.blocksize,
                    dtype="float32",
                    callback=self._audio_callback,
                )
                stream.start()
                self._recording_start = time.perf_counter()
                self._stream = stream
                self._session_active = True
                break
            except Exception:
                if stream is not None:
                    try:
                        stream.close()
                    except Exception:
                        pass
                self._stream = None
                if should_retry_with_fresh_device:
                    should_retry_with_fresh_device = False
                    previous_config = (input_device, active_sample_rate)
                    reset_input_device_cache()
                    input_device, active_sample_rate = _resolve_input_stream_config(
                        self.sample_rate,
                        self.device,
                    )
                    self._active_sample_rate = active_sample_rate
                    if (input_device, active_sample_rate) == previous_config:
                        self._recording_start = 0
                        self._stop_event.set()
                        raise
                    logger.info(
                        "[%s] Aufnahme-Start fehlgeschlagen, versuche Device-Reprobe",
                        get_session_id(),
                    )
                    continue
                self._recording_start = 0
                self._stop_event.set()
                raise

        if play_ready_sound:
            _play_sound("ready")

        logger.info(f"[{get_session_id()}] Aufnahme gestartet")

    def stop(self, output_path: Path | None = None) -> Path:
        """Stoppt die Aufnahme und speichert die Audiodatei.

        Args:
            output_path: Optionaler Pfad für die Ausgabedatei

        Returns:
            Pfad zur gespeicherten WAV-Datei

        Raises:
            ValueError: Wenn keine Audiodaten aufgenommen wurden
        """
        if not self._session_active:
            raise RuntimeError("Keine aktive Aufnahme.")

        import numpy as np
        import soundfile as sf

        self._session_active = False
        stream = self._stream
        self._stream = None
        stream_error: Exception | None = None
        if stream:
            try:
                stream.stop()
            except Exception as exc:
                stream_error = exc
            try:
                stream.close()
            except Exception as exc:
                if stream_error is None:
                    stream_error = exc
                else:
                    logger.debug("Stream close after stop failure also failed: %s", exc)

        self._stop_event.set()
        if stream_error is not None:
            self._recording_start = 0
            raise stream_error

        recording_duration = time.perf_counter() - self._recording_start
        logger.info(f"[{get_session_id()}] Aufnahme: {recording_duration:.1f}s")

        _play_sound("stop")

        with self._chunks_lock:
            if not self._recorded_chunks:
                self._recording_start = 0
                logger.error(f"[{get_session_id()}] Keine Audiodaten aufgenommen")
                raise ValueError("Keine Audiodaten aufgenommen.")

            audio_data = np.concatenate(self._recorded_chunks)

        if output_path is None:
            output_path = Path(tempfile.gettempdir()) / TEMP_RECORDING_FILENAME

        sf.write(output_path, audio_data, self._active_sample_rate)
        self._recording_start = 0
        with self._chunks_lock:
            self._recorded_chunks = []

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
        """Gibt eine Kopie der bisher aufgenommenen Chunks zurück (thread-safe)."""
        with self._chunks_lock:
            return list(self._recorded_chunks)


def record_audio() -> Path:
    """Nimmt Audio vom Mikrofon auf (Enter startet, Enter stoppt).

    Gibt Pfad zur temporären WAV-Datei zurück.
    """
    import numpy as np
    import sounddevice as sd
    import soundfile as sf

    recorded_chunks: list = []
    input_device, actual_sample_rate = _resolve_input_stream_config(
        WHISPER_SAMPLE_RATE
    )

    def on_audio_chunk(indata, _frames, _time, _status):
        recorded_chunks.append(indata.copy())

    _log("🎤 Drücke ENTER um die Aufnahme zu starten...")
    input()

    should_retry_with_fresh_device = True
    while True:
        try:
            with sd.InputStream(
                device=input_device,
                samplerate=actual_sample_rate,
                channels=1,
                dtype="float32",
                callback=on_audio_chunk,
            ):
                _play_sound("ready")
                _log("🔴 Aufnahme läuft... Drücke ENTER zum Beenden.")
                input()
            break
        except Exception:
            if should_retry_with_fresh_device:
                should_retry_with_fresh_device = False
                previous_config = (input_device, actual_sample_rate)
                reset_input_device_cache()
                input_device, actual_sample_rate = _resolve_input_stream_config(
                    WHISPER_SAMPLE_RATE
                )
                if (input_device, actual_sample_rate) != previous_config:
                    logger.info(
                        "[%s] CLI-Aufnahme-Start fehlgeschlagen, versuche Device-Reprobe",
                        get_session_id(),
                    )
                    continue
            raise

    _log("✅ Aufnahme beendet.")
    _play_sound("stop")

    if not recorded_chunks:
        raise ValueError("Keine Audiodaten aufgenommen. Bitte länger aufnehmen.")

    audio_data = np.concatenate(recorded_chunks)
    output_path = Path(tempfile.gettempdir()) / TEMP_RECORDING_FILENAME
    sf.write(output_path, audio_data, actual_sample_rate)

    return output_path
