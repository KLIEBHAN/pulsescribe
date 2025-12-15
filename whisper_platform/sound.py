"""Sound-Playback Implementierungen.

Plattformspezifische Sound-Playback mit einheitlichem Interface.
macOS: CoreAudio via AudioToolbox mit afplay Fallback
Windows: winsound mit System-Sounds
"""

import logging
import subprocess
import sys

logger = logging.getLogger("pulsescribe.platform.sound")

# Sound-Registry: Name → System-Sound-Pfad (macOS)
MACOS_SYSTEM_SOUNDS = {
    "ready": "/System/Library/Sounds/Tink.aiff",
    "stop": "/System/Library/Sounds/Pop.aiff",
    "done": "/System/Library/Sounds/Morse.aiff",  # Erfolgs-Feedback (sehr dezent, kurz)
    "error": "/System/Library/Sounds/Basso.aiff",
}

# Windows System-Sound Aliases
WINDOWS_SYSTEM_SOUNDS = {
    "ready": "SystemAsterisk",
    "stop": "SystemExclamation",
    "done": "SystemAsterisk",  # Erfolgs-Feedback
    "error": "SystemHand",
}


class MacOSSoundPlayer:
    """CoreAudio-Sound-Playback mit Fallback auf afplay.

    Cached Sound-IDs für schnelles Abspielen (~0.2ms statt ~500ms mit afplay).
    """

    def __init__(self) -> None:
        self._sound_ids: dict[str, int] = {}
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

        # Sound-ID aus Cache oder neu laden
        if name not in self._sound_ids:
            sound_id = self._load_sound(sound_path)
            if sound_id is None:
                self._play_fallback(sound_path)
                return
            self._sound_ids[name] = sound_id

        # Sound abspielen (non-blocking, ~0.2ms)
        try:
            self._audio_toolbox.AudioServicesPlaySystemSound(self._sound_ids[name])
        except Exception:
            self._play_fallback(sound_path)

    def _play_fallback(self, sound_path: str) -> None:
        """Fallback auf afplay wenn CoreAudio nicht funktioniert."""
        try:
            subprocess.Popen(
                ["afplay", sound_path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError:
            pass


class WindowsSoundPlayer:
    """Windows Sound-Playback via winsound.

    Nutzt Windows System-Sounds für konsistente UX.
    """

    def __init__(self) -> None:
        self._winsound = None
        try:
            import winsound

            self._winsound = winsound
        except ImportError:
            logger.warning("winsound nicht verfügbar")

    def play(self, name: str) -> None:
        """Spielt benannten System-Sound ab."""
        if self._winsound is None:
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


def get_sound_player() -> "MacOSSoundPlayer | WindowsSoundPlayer":
    """Gibt gecachten Sound-Player für die aktuelle Plattform zurück.

    Nutzt Singleton-Pattern: Player wird nur einmal erstellt, Sound-IDs bleiben gecacht.
    Performance: Erste Initialisierung ~1ms, danach ~0ms.
    """
    global _sound_player_cache
    if _sound_player_cache is None:
        if sys.platform == "darwin":
            _sound_player_cache = MacOSSoundPlayer()
        elif sys.platform == "win32":
            _sound_player_cache = WindowsSoundPlayer()
        else:
            raise NotImplementedError(f"Sound nicht implementiert für {sys.platform}")
    return _sound_player_cache


__all__ = ["MacOSSoundPlayer", "WindowsSoundPlayer", "get_sound_player"]
