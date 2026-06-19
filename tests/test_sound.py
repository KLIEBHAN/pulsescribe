"""Tests für whisper_platform.sound – Zombie-Vermeidung und Failure-Cache."""

import threading
import wave
from unittest.mock import MagicMock, patch

from whisper_platform.sound import (
    READY_CUE_SAMPLE_RATE,
    MacOSSoundPlayer,
    WindowsSoundPlayer,
    _ready_cue_samples,
    _write_ready_cue_wav,
)


def _make_fallback_player() -> MacOSSoundPlayer:
    """Create a player that always uses the afplay fallback."""
    player = MacOSSoundPlayer.__new__(MacOSSoundPlayer)
    player._sound_ids = {}
    player._failed_sounds = set()
    player._audio_toolbox = None
    player._core_foundation = None
    player._use_fallback = True
    player._ctypes = None
    return player


class TestPlayFallbackNoZombies:
    """_play_fallback must call subprocess.run (which reaps the child)."""

    def test_play_fallback_uses_subprocess_run_in_thread(self) -> None:
        """Verify that _play_fallback spawns a daemon thread with subprocess.run."""
        player = _make_fallback_player()
        started_threads: list[threading.Thread] = []

        def _capture_start(self_thread: threading.Thread) -> None:
            started_threads.append(self_thread)
            # Don't actually start the thread in the test

        with patch.object(threading.Thread, "start", _capture_start):
            player._play_fallback("/System/Library/Sounds/Tink.aiff")

        assert len(started_threads) == 1
        assert started_threads[0].daemon is True

    def test_play_fallback_thread_calls_subprocess_run(self) -> None:
        """The thread target should call subprocess.run, not Popen."""
        player = _make_fallback_player()
        spawned: list[threading.Thread] = []
        _real_start = threading.Thread.start

        def _track_start(self_thread: threading.Thread) -> None:
            spawned.append(self_thread)
            _real_start(self_thread)

        with (
            patch("whisper_platform.sound.subprocess.run") as mock_run,
            patch.object(threading.Thread, "start", _track_start),
        ):
            player._play_fallback("/System/Library/Sounds/Tink.aiff")
            assert len(spawned) == 1
            spawned[0].join(timeout=2)

        mock_run.assert_called_once()
        args = mock_run.call_args
        assert args[0][0] == ["afplay", "/System/Library/Sounds/Tink.aiff"]
        assert args[1].get("timeout") == 5


class TestFailureCache:
    """Failed _load_sound results must be cached to avoid repeated failures."""

    def test_failed_load_sound_is_cached(self) -> None:
        """After _load_sound fails, subsequent play() calls skip it."""
        player = _make_fallback_player()
        player._use_fallback = False  # Enable CoreAudio path

        fallback_calls = []
        player._play_fallback = lambda path: fallback_calls.append(path)
        player._load_sound = lambda path: None  # Always fail

        player.play("ready")
        player.play("ready")
        player.play("ready")

        # _load_sound should only be attempted once; rest go straight to fallback
        assert "ready" in player._failed_sounds
        assert len(fallback_calls) == 3

    def test_successful_load_sound_is_not_in_failed_cache(self) -> None:
        """Successful sounds are cached in _sound_ids, not _failed_sounds."""
        player = _make_fallback_player()
        player._use_fallback = False

        player._load_sound = lambda path: 42  # Fake sound ID
        player._audio_toolbox = MagicMock()
        player._audio_toolbox.AudioServicesPlaySystemSound.return_value = 0

        player.play("ready")

        assert "ready" not in player._failed_sounds
        assert player._sound_ids["ready"] == 42

    def test_playback_error_uses_fallback_and_caches_failure(self) -> None:
        """Non-zero CoreAudio results should fall back and stop retrying CoreAudio."""
        player = _make_fallback_player()
        player._use_fallback = False

        fallback_calls: list[str] = []
        player._load_sound = lambda path: 42
        player._play_fallback = lambda path: fallback_calls.append(path)
        player._audio_toolbox = MagicMock()
        player._audio_toolbox.AudioServicesPlaySystemSound.return_value = 1

        player.play("ready")
        player.play("ready")

        assert fallback_calls == [
            "/System/Library/Sounds/Tink.aiff",
            "/System/Library/Sounds/Tink.aiff",
        ]
        assert "ready" in player._failed_sounds
        assert "ready" not in player._sound_ids
        assert player._audio_toolbox.AudioServicesPlaySystemSound.call_count == 1


def _make_windows_player(cue_path: str | None) -> tuple[WindowsSoundPlayer, MagicMock]:
    """Construct a WindowsSoundPlayer without importing real winsound."""
    player = WindowsSoundPlayer.__new__(WindowsSoundPlayer)
    fake = MagicMock()
    fake.SND_FILENAME = 0x00020000
    fake.SND_ASYNC = 0x0001
    fake.SND_NODEFAULT = 0x0002
    fake.SND_ALIAS = 0x00010000
    player._winsound = fake
    player._ready_cue_path = cue_path
    return player, fake


class TestReadyCueSynthesis:
    """The Windows ready cue must have instant onset and valid WAV framing."""

    def test_samples_have_instant_onset(self) -> None:
        samples = _ready_cue_samples()

        assert len(samples) == int(READY_CUE_SAMPLE_RATE * 0.012)
        assert samples[0] == 0  # no DC click at the boundary

        first_ms = samples[: int(READY_CUE_SAMPLE_RATE * 0.001)]
        assert max(abs(sample) for sample in first_ms) >= 8000

    def test_write_ready_cue_wav_is_valid_pcm16_mono(self, tmp_path) -> None:
        out = tmp_path / "cue.wav"
        _write_ready_cue_wav(str(out))

        with wave.open(str(out), "rb") as wav:
            assert wav.getnchannels() == 1
            assert wav.getsampwidth() == 2
            assert wav.getframerate() == READY_CUE_SAMPLE_RATE
            assert wav.getnframes() == len(_ready_cue_samples())


class TestWindowsReadyRouting:
    """play('ready') uses the synthesized cue; other sounds use aliases."""

    def test_ready_uses_synthesized_cue_file(self) -> None:
        player, fake = _make_windows_player(r"C:\tmp\cue.wav")

        player.play("ready")

        fake.PlaySound.assert_called_once()
        target, flags = fake.PlaySound.call_args[0]
        assert target == r"C:\tmp\cue.wav"
        assert flags & fake.SND_FILENAME
        assert flags & fake.SND_ASYNC
        assert flags & fake.SND_NODEFAULT

    def test_ready_falls_back_to_alias_without_cue(self) -> None:
        player, fake = _make_windows_player(None)

        player.play("ready")

        target, flags = fake.PlaySound.call_args[0]
        assert target == "DeviceConnect"
        assert flags & fake.SND_ALIAS
        assert flags & fake.SND_ASYNC

    def test_ready_falls_back_to_alias_when_cue_playback_raises(self) -> None:
        cue_path = r"C:\tmp\cue.wav"
        player, fake = _make_windows_player(cue_path)

        def _side_effect(target, _flags):
            if target == cue_path:
                raise RuntimeError("cue playback failed")
            return 0

        fake.PlaySound.side_effect = _side_effect

        player.play("ready")

        calls = fake.PlaySound.call_args_list
        assert calls[0][0][0] == cue_path
        assert calls[1][0][0] == "DeviceConnect"

    def test_non_ready_sound_uses_alias(self) -> None:
        player, fake = _make_windows_player(r"C:\tmp\cue.wav")

        player.play("stop")

        target, flags = fake.PlaySound.call_args[0]
        assert target == "DeviceDisconnect"
        assert flags & fake.SND_ALIAS
        assert flags & fake.SND_ASYNC
