"""Tests für whisper_platform.sound – Zombie-Vermeidung und Failure-Cache."""

import threading
from unittest.mock import MagicMock, patch

from whisper_platform.sound import MacOSSoundPlayer


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
