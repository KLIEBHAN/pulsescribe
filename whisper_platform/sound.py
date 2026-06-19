"""Sound-Playback Implementierungen.

Plattformspezifische Sound-Playback mit einheitlichem Interface.
macOS: CoreAudio via AudioToolbox mit afplay Fallback
Windows: winsound mit System-Sounds; der "ready"-Cue wird als kurzer Tick mit
sofortigem Onset synthetisiert, damit das Startsignal nicht träge wirkt.
"""

import logging
import math
import os
import struct
import subprocess
import sys
import tempfile
import threading
import wave

logger = logging.getLogger("pulsescribe.platform.sound")

# Sound-Registry: Name → System-Sound-Pfad (macOS)
MACOS_SYSTEM_SOUNDS = {
    "ready": "/System/Library/Sounds/Tink.aiff",
    "stop": "/System/Library/Sounds/Pop.aiff",
    "done": "/System/Library/Sounds/Bottle.aiff",  # Erfolgs-Feedback (Korken-Pop = "geschafft")
    "error": "/System/Library/Sounds/Basso.aiff",
    "warmup": "/System/Library/Sounds/Glass.aiff",  # Preload/Warmup fertig
}

# Windows System-Sound Aliases (Win10/11 optimiert)
WINDOWS_SYSTEM_SOUNDS = {
    "ready": "DeviceConnect",  # Aufnahme startet
    "stop": "DeviceDisconnect",  # Aufnahme stoppt
    "done": "Notification.SMS",  # Text eingefügt
    "error": "SystemHand",  # Fehler (kritischer Ton)
    "warmup": "SystemAsterisk",  # Preload/Warmup fertig
}

# Windows "ready"-Cue: kurzer synthetischer Tick mit sofortigem Onset.
# System-Aliase wie DeviceConnect schwingen langsam ein und lassen das
# Startsignal träge wirken; ein knapper Tick ist sofort als "go" hörbar.
READY_CUE_SAMPLE_RATE = 44100
READY_CUE_FREQ_HZ = 2600.0  # crisp, gut hörbar, nicht schrill
READY_CUE_DURATION_SEC = 0.012  # ~12ms: kurz genug für "instant", lang genug hörbar
READY_CUE_ATTACK_SEC = 0.0005  # ~0.5ms Anstieg: praktisch sofort, ohne DC-Klick
READY_CUE_DECAY_TAU_SEC = 0.003  # exponentieller Abfall
READY_CUE_AMPLITUDE = 0.5  # Anteil von Full-Scale int16
READY_CUE_FILENAME = "pulsescribe_ready_cue.wav"


def _ready_cue_samples() -> list[int]:
    """Erzeugt die int16-Samples für den sofort-einsetzenden Ready-Tick.

    Near-instant attack (kein DC-Klick) gefolgt von exponentiellem Decay.
    """
    sample_rate = READY_CUE_SAMPLE_RATE
    total = int(sample_rate * READY_CUE_DURATION_SEC)
    attack = max(1, int(sample_rate * READY_CUE_ATTACK_SEC))
    two_pi_f = 2.0 * math.pi * READY_CUE_FREQ_HZ
    peak = READY_CUE_AMPLITUDE * 32767

    samples: list[int] = []
    for n in range(total):
        t = n / sample_rate
        if n < attack:
            envelope = n / attack  # linearer Anstieg
        else:
            envelope = math.exp(-(t - READY_CUE_ATTACK_SEC) / READY_CUE_DECAY_TAU_SEC)
        value = peak * envelope * math.sin(two_pi_f * t)
        samples.append(max(-32768, min(32767, int(value))))
    return samples


def _write_ready_cue_wav(path: str) -> None:
    """Schreibt den Ready-Cue als 16-bit Mono-WAV an den angegebenen Pfad."""
    samples = _ready_cue_samples()
    frames = struct.pack("<%dh" % len(samples), *samples)
    with wave.open(path, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(READY_CUE_SAMPLE_RATE)
        wav.writeframes(frames)


class MacOSSoundPlayer:
    """CoreAudio-Sound-Playback mit Fallback auf afplay.

    Cached Sound-IDs für schnelles Abspielen (~0.2ms statt ~500ms mit afplay).
    """

    def __init__(self) -> None:
        self._sound_ids: dict[str, int] = {}
        self._failed_sounds: set[str] = set()
        self._audio_toolbox = None
        self._core_foundation = None
        self._use_fallback = False
        self._ctypes = None

        try:
            import ctypes

            self._ctypes = ctypes
            self._audio_toolbox = ctypes.CDLL(
                "/System/Library/Frameworks/AudioToolbox.framework/AudioToolbox"
            )
            self._core_foundation = ctypes.CDLL(
                "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
            )

            # CFStringCreateWithCString
            self._core_foundation.CFStringCreateWithCString.restype = ctypes.c_void_p
            self._core_foundation.CFStringCreateWithCString.argtypes = [
                ctypes.c_void_p,
                ctypes.c_char_p,
                ctypes.c_uint32,
            ]

            # CFURLCreateWithFileSystemPath
            self._core_foundation.CFURLCreateWithFileSystemPath.restype = (
                ctypes.c_void_p
            )
            self._core_foundation.CFURLCreateWithFileSystemPath.argtypes = [
                ctypes.c_void_p,
                ctypes.c_void_p,
                ctypes.c_int,
                ctypes.c_bool,
            ]

            # AudioServicesCreateSystemSoundID
            self._audio_toolbox.AudioServicesCreateSystemSoundID.restype = (
                ctypes.c_int32
            )
            self._audio_toolbox.AudioServicesCreateSystemSoundID.argtypes = [
                ctypes.c_void_p,
                ctypes.POINTER(ctypes.c_uint32),
            ]

            # AudioServicesPlaySystemSound
            self._audio_toolbox.AudioServicesPlaySystemSound.restype = ctypes.c_int32
            self._audio_toolbox.AudioServicesPlaySystemSound.argtypes = [
                ctypes.c_uint32
            ]

            # CFRelease für Memory Management
            self._core_foundation.CFRelease.restype = None
            self._core_foundation.CFRelease.argtypes = [ctypes.c_void_p]
        except (OSError, AttributeError) as e:
            logger.debug(f"CoreAudio nicht verfügbar, nutze Fallback: {e}")
            self._use_fallback = True

    def _load_sound(self, path: str) -> int | None:
        """Lädt Sound-Datei und gibt Sound-ID zurück."""
        if self._use_fallback or self._core_foundation is None or self._ctypes is None:
            return None

        cf_string = None
        cf_url = None
        try:
            # CFString aus Pfad erstellen (kCFStringEncodingUTF8 = 0x08000100)
            cf_string = self._core_foundation.CFStringCreateWithCString(
                None, path.encode(), 0x08000100
            )
            if not cf_string:
                return None

            # CFURL erstellen (kCFURLPOSIXPathStyle = 0)
            cf_url = self._core_foundation.CFURLCreateWithFileSystemPath(
                None, cf_string, 0, False
            )
            if not cf_url:
                return None

            # Sound-ID erstellen
            sound_id = self._ctypes.c_uint32(0)
            result = self._audio_toolbox.AudioServicesCreateSystemSoundID(
                cf_url, self._ctypes.byref(sound_id)
            )

            if result == 0:
                return sound_id.value
            return None
        except Exception:
            return None
        finally:
            # WICHTIG: CF-Objekte freigeben um Memory Leaks zu vermeiden
            if cf_url:
                self._core_foundation.CFRelease(cf_url)
            if cf_string:
                self._core_foundation.CFRelease(cf_string)

    def play(self, name: str) -> None:
        """Spielt benannten Sound ab."""
        sound_path = MACOS_SYSTEM_SOUNDS.get(name)
        if not sound_path:
            logger.warning(f"Unbekannter Sound: {name}")
            return

        # Fallback auf subprocess
        if self._use_fallback:
            self._play_fallback(sound_path)
            return

        if name in self._failed_sounds:
            self._play_fallback(sound_path)
            return

        # Sound-ID aus Cache oder neu laden
        if name not in self._sound_ids:
            sound_id = self._load_sound(sound_path)
            if sound_id is None:
                self._failed_sounds.add(name)
                self._play_fallback(sound_path)
                return
            self._sound_ids[name] = sound_id

        # Sound abspielen (non-blocking, ~0.2ms)
        try:
            result = self._audio_toolbox.AudioServicesPlaySystemSound(
                self._sound_ids[name]
            )
            if result == 0:
                return
        except Exception:
            pass

        self._failed_sounds.add(name)
        self._sound_ids.pop(name, None)
        self._play_fallback(sound_path)

    def _play_fallback(self, sound_path: str) -> None:
        """Fallback auf afplay wenn CoreAudio nicht funktioniert.

        Runs in a daemon thread so subprocess.run reaps the child process.
        """

        def _run() -> None:
            try:
                subprocess.run(
                    ["afplay", sound_path],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=5,
                )
            except Exception:
                pass

        threading.Thread(target=_run, daemon=True).start()


class WindowsSoundPlayer:
    """Windows Sound-Playback via winsound.

    Nutzt Windows System-Sounds für konsistente UX. Der "ready"-Cue wird als
    kurzer, sofort einsetzender Tick synthetisiert (statt eines langsam
    einschwingenden System-Alias), damit das Startsignal nicht träge wirkt.
    """

    def __init__(self) -> None:
        self._winsound = None
        self._ready_cue_path: str | None = None
        try:
            import winsound

            self._winsound = winsound
        except ImportError:
            logger.warning("winsound nicht verfügbar")
            return

        self._ready_cue_path = self._ensure_ready_cue()

    def _ensure_ready_cue(self) -> str | None:
        """Synthetisiert den Ready-Cue einmalig in den Temp-Ordner.

        Gibt den Pfad zurück oder None, falls die Synthese fehlschlägt (dann
        fällt play("ready") auf den System-Alias zurück).
        """
        path = os.path.join(tempfile.gettempdir(), READY_CUE_FILENAME)
        try:
            if not os.path.exists(path) or os.path.getsize(path) == 0:
                _write_ready_cue_wav(path)
            return path
        except Exception as e:
            logger.debug(f"Ready-Cue konnte nicht erzeugt werden: {e}")
            return None

    def _play_ready_cue(self) -> bool:
        """Spielt den synthetischen Ready-Tick. True bei Erfolg."""
        if not self._ready_cue_path:
            return False
        try:
            # SND_FILENAME | SND_ASYNC: non-blocking; SND_NODEFAULT unterdrückt
            # den System-Beep, falls die Datei nicht abgespielt werden kann.
            self._winsound.PlaySound(
                self._ready_cue_path,
                self._winsound.SND_FILENAME
                | self._winsound.SND_ASYNC
                | self._winsound.SND_NODEFAULT,
            )
            return True
        except Exception as e:
            logger.debug(f"Ready-Cue-Playback fehlgeschlagen: {e}")
            return False

    def play(self, name: str) -> None:
        """Spielt benannten System-Sound ab."""
        if self._winsound is None:
            return

        if name == "ready" and self._play_ready_cue():
            return

        sound_alias = WINDOWS_SYSTEM_SOUNDS.get(name)
        if not sound_alias:
            logger.warning(f"Unbekannter Sound: {name}")
            return

        try:
            # SND_ALIAS | SND_ASYNC für non-blocking Playback
            self._winsound.PlaySound(
                sound_alias, self._winsound.SND_ALIAS | self._winsound.SND_ASYNC
            )
        except Exception as e:
            logger.debug(f"Sound-Playback fehlgeschlagen: {e}")


# Singleton-Cache für Sound-Player (vermeidet wiederholte ctypes/CDLL Initialisierung)
_sound_player_cache: "MacOSSoundPlayer | WindowsSoundPlayer | None" = None
_sound_player_lock = threading.Lock()


def get_sound_player() -> "MacOSSoundPlayer | WindowsSoundPlayer":
    """Gibt gecachten Sound-Player für die aktuelle Plattform zurück.

    Nutzt Singleton-Pattern: Player wird nur einmal erstellt, Sound-IDs bleiben gecacht.
    Performance: Erste Initialisierung ~1ms, danach ~0ms.
    """
    global _sound_player_cache
    if _sound_player_cache is None:
        with _sound_player_lock:
            if _sound_player_cache is None:
                if sys.platform == "darwin":
                    _sound_player_cache = MacOSSoundPlayer()
                elif sys.platform == "win32":
                    _sound_player_cache = WindowsSoundPlayer()
                else:
                    raise NotImplementedError(
                        f"Sound nicht implementiert für {sys.platform}"
                    )
    return _sound_player_cache


__all__ = ["MacOSSoundPlayer", "WindowsSoundPlayer", "get_sound_player"]
