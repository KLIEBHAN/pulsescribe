import os
import sys
import threading
import unittest
from unittest.mock import MagicMock, patch

from whisper_daemon import WhisperDaemon
from utils.state import AppState, DaemonMessage, MessageType


class TestDaemonMode(unittest.TestCase):
    @patch("whisper_daemon.threading.Thread")
    def test_start_recording_openai_mode(self, mock_thread_cls):
        """Test that OpenAI mode starts recording worker, not streaming."""
        daemon = WhisperDaemon(mode="openai")

        # Unlink INTERIM_FILE mock? It's a Path object in the module.
        with patch("whisper_daemon.INTERIM_FILE") as mock_interim:
            daemon._start_recording()

        # Check that thread was started with _recording_worker
        mock_thread_cls.assert_called_once()
        args, kwargs = mock_thread_cls.call_args
        self.assertEqual(kwargs["target"], daemon._recording_worker)
        self.assertEqual(kwargs["name"], "RecordingWorker")
        self.assertTrue(daemon._recording)

    @patch("whisper_daemon.threading.Thread")
    def test_start_recording_deepgram_streaming(self, mock_thread_cls):
        """Test that Deepgram mode (streaming enabled) starts streaming worker."""
        daemon = WhisperDaemon(mode="deepgram")

        with (
            patch.dict(os.environ, {"WHISPER_GO_STREAMING": "true"}),
            patch("whisper_daemon.INTERIM_FILE"),
        ):
            daemon._start_recording()

        mock_thread_cls.assert_called_once()
        args, kwargs = mock_thread_cls.call_args
        self.assertEqual(kwargs["target"], daemon._streaming_worker)
        self.assertEqual(kwargs["name"], "StreamingWorker")

    @patch("whisper_daemon.threading.Thread")
    def test_start_recording_deepgram_no_streaming(self, mock_thread_cls):
        """Test that Deepgram mode (streaming disabled) starts recording worker."""
        daemon = WhisperDaemon(mode="deepgram")

        with (
            patch.dict(os.environ, {"WHISPER_GO_STREAMING": "false"}),
            patch("whisper_daemon.INTERIM_FILE"),
        ):
            daemon._start_recording()

        mock_thread_cls.assert_called_once()
        args, kwargs = mock_thread_cls.call_args
        self.assertEqual(kwargs["target"], daemon._recording_worker)
        self.assertEqual(kwargs["name"], "RecordingWorker")

    @patch("whisper_daemon.threading.Thread")
    def test_start_recording_deepgram_streaming_default_when_unset(
        self, mock_thread_cls
    ):
        """Deepgram mode defaults to streaming when WHISPER_GO_STREAMING is unset."""
        daemon = WhisperDaemon(mode="deepgram")

        # Ensure the env var is not present for this test
        # Use simple os.environ manipulation with patch.dict to restore later
        with patch.dict(os.environ):
            if "WHISPER_GO_STREAMING" in os.environ:
                del os.environ["WHISPER_GO_STREAMING"]

            with patch("whisper_daemon.INTERIM_FILE"):
                daemon._start_recording()

        mock_thread_cls.assert_called_once()
        args, kwargs = mock_thread_cls.call_args
        self.assertEqual(kwargs["target"], daemon._streaming_worker)
        self.assertEqual(kwargs["name"], "StreamingWorker")

    @patch("whisper_daemon.WhisperDaemon")
    def test_main_uses_env_mode_for_deepgram(self, mock_daemon_cls):
        """whisper_daemon.main() uses WHISPER_GO_MODE when set (e.g., deepgram)."""
        import whisper_daemon

        with (
            patch.dict(os.environ, {"WHISPER_GO_MODE": "deepgram"}),
            patch.object(sys, "argv", ["whisper-daemon"]),
            patch("whisper_daemon.WhisperDaemon.run"),
        ):
            whisper_daemon.main()

        mock_daemon_cls.assert_called_once()
        _, kwargs = mock_daemon_cls.call_args
        self.assertEqual(kwargs.get("mode"), "deepgram")

    @patch("whisper_daemon.WhisperDaemon")
    def test_main_uses_cli_mode_over_env(self, mock_daemon_cls):
        """CLI --mode should override WHISPER_GO_MODE env variable."""
        import whisper_daemon

        with (
            patch.dict(os.environ, {"WHISPER_GO_MODE": "openai"}),
            patch.object(sys, "argv", ["whisper-daemon", "--mode", "deepgram"]),
            patch("whisper_daemon.WhisperDaemon.run"),
        ):
            whisper_daemon.main()

        mock_daemon_cls.assert_called_once()
        _, kwargs = mock_daemon_cls.call_args
        self.assertEqual(kwargs.get("mode"), "deepgram")

    def test_model_name_for_logging_local_uses_local_env(self):
        """Im local-Mode soll für Logs WHISPER_GO_LOCAL_MODEL genutzt werden."""
        daemon = WhisperDaemon(mode="local", model=None)
        mock_provider = MagicMock()
        mock_provider.default_model = "turbo"

        with patch.dict(os.environ, {"WHISPER_GO_LOCAL_MODEL": "large"}):
            self.assertEqual(daemon._model_name_for_logging(mock_provider), "large")

    def test_reload_settings_unsets_removed_local_backend_env(self):
        """Wenn Settings ein Key entfernen, muss ENV ebenfalls bereinigt werden (sonst bleibt z.B. faster aktiv)."""
        daemon = WhisperDaemon(mode="openai")
        local_provider = MagicMock()
        daemon._provider_cache["local"] = local_provider

        with (
            patch.dict(os.environ, {"WHISPER_GO_LOCAL_BACKEND": "faster"}),
            patch("whisper_daemon.load_environment"),
            patch("utils.preferences.read_env_file", return_value={}),
        ):
            daemon._reload_settings()
            self.assertNotIn("WHISPER_GO_LOCAL_BACKEND", os.environ)
            # Provider bleibt gecached, aber muss Runtime-Config neu evaluieren.
            self.assertIn("local", daemon._provider_cache)
            local_provider.invalidate_runtime_config.assert_called_once()

    def test_recording_worker_execution(self):
        daemon = WhisperDaemon(mode="openai", language="de")
        daemon._stop_event = threading.Event()

        # Mocking items used inside _recording_worker
        mock_sd = MagicMock()
        mock_sf = MagicMock()
        mock_np = MagicMock()

        # Mock context manager for InputStream
        mock_stream = MagicMock()
        mock_sd.InputStream.return_value.__enter__.return_value = mock_stream

        # Setup numpy to return concatenatable result
        mock_np.concatenate.return_value = b"fake_audio_data"

        # Stop event handling: let the loop run once then stop
        def side_effect_sleep(*args):
            # Capture callback and call it
            call_args = mock_sd.InputStream.call_args
            if call_args:
                kw = call_args[1]
                callback = kw.get("callback")
                if callback:
                    # Call with dummy data
                    import numpy as np

                    callback(np.zeros((100, 1), dtype="float32"), 100, None, None)

            daemon._stop_event.set()

        mock_sd.sleep.side_effect = side_effect_sleep

        # Mock dependencies
        mock_provider = MagicMock()
        mock_get_provider = MagicMock(return_value=mock_provider)
        mock_player = MagicMock()
        mock_get_player = MagicMock(return_value=mock_player)

        with (
            patch.dict(
                sys.modules,
                {"sounddevice": mock_sd, "soundfile": mock_sf, "numpy": mock_np},
            ),
            patch(
                "whisper_daemon.tempfile.mkstemp", return_value=(123, "/tmp/fake.wav")
            ),
            patch("whisper_daemon.os.close"),
            patch("whisper_daemon.os.unlink"),
            patch("whisper_daemon.os.path.exists", return_value=True),
            patch("whisper_daemon.get_provider", mock_get_provider),
            patch("whisper_daemon.get_sound_player", mock_get_player),
        ):
            daemon._recording_worker()

            # Verify usage
            mock_player.play.assert_any_call("ready")
            mock_player.play.assert_any_call("stop")

            # Verify provider called
            mock_get_provider.assert_called_with("openai")
            mock_provider.transcribe.assert_called_once()
            call_args = mock_provider.transcribe.call_args
            self.assertEqual(
                str(call_args[0][0]), "/tmp/fake.wav"
            )  # First arg is audio_path
            self.assertEqual(call_args[1]["model"], None)  # Default
            self.assertEqual(call_args[1]["language"], "de")

            # Verify result put in queue
            self.assertFalse(daemon._result_queue.empty())

    def test_recording_worker_no_audio_puts_empty_result(self):
        """Sehr kurzer Hold-Tap ohne Callback darf nicht im TRANSCRIBING hängen bleiben."""
        daemon = WhisperDaemon(mode="openai")
        daemon._stop_event = threading.Event()
        daemon._stop_event.set()  # Aufnahme sofort beenden, keine Chunks

        mock_sd = MagicMock()
        mock_sf = MagicMock()
        mock_np = MagicMock()

        # Mock context manager for InputStream (kein Callback wird aufgerufen)
        mock_stream = MagicMock()
        mock_sd.InputStream.return_value.__enter__.return_value = mock_stream

        mock_player = MagicMock()
        mock_get_player = MagicMock(return_value=mock_player)

        with (
            patch.dict(
                sys.modules,
                {"sounddevice": mock_sd, "soundfile": mock_sf, "numpy": mock_np},
            ),
            patch("whisper_daemon.get_sound_player", mock_get_player),
        ):
            daemon._recording_worker()

            self.assertFalse(daemon._result_queue.empty())
            msg = daemon._result_queue.get_nowait()
            self.assertIsInstance(msg, DaemonMessage)
            self.assertEqual(msg.type, MessageType.TRANSCRIPT_RESULT)
            self.assertEqual(msg.payload, "")

    def test_stop_recording_does_not_overwrite_idle_state(self):
        """_stop_recording darf IDLE/DONE nicht wieder auf TRANSCRIBING setzen."""
        daemon = WhisperDaemon(mode="openai")
        daemon._recording = True
        daemon._current_state = AppState.IDLE
        daemon._stop_event = threading.Event()

        daemon._stop_recording()

        self.assertEqual(daemon._current_state, AppState.IDLE)

    def test_recording_worker_silent_skips_transcription(self):
        """Wenn keine Sprache erkannt wird, wird nicht transkribiert."""
        daemon = WhisperDaemon(mode="openai")
        daemon._stop_event = threading.Event()

        mock_sd = MagicMock()
        mock_sf = MagicMock()
        mock_np = MagicMock()

        mock_stream = MagicMock()
        mock_sd.InputStream.return_value.__enter__.return_value = mock_stream

        def side_effect_sleep(*_args):
            call_args = mock_sd.InputStream.call_args
            if call_args:
                callback = call_args[1].get("callback")
                if callback:
                    callback(MagicMock(), 100, None, None)
            daemon._stop_event.set()

        mock_sd.sleep.side_effect = side_effect_sleep

        mock_get_provider = MagicMock()
        mock_player = MagicMock()
        mock_get_player = MagicMock(return_value=mock_player)

        with (
            patch.dict(
                sys.modules,
                {"sounddevice": mock_sd, "soundfile": mock_sf, "numpy": mock_np},
            ),
            patch("whisper_daemon.get_provider", mock_get_provider),
            patch("whisper_daemon.get_sound_player", mock_get_player),
            patch("whisper_daemon.VAD_THRESHOLD", 9999.0),
        ):
            daemon._recording_worker()

        mock_get_provider.assert_not_called()
        result_msg = None
        while not daemon._result_queue.empty():
            msg = daemon._result_queue.get_nowait()
            if (
                isinstance(msg, DaemonMessage)
                and msg.type == MessageType.TRANSCRIPT_RESULT
            ):
                result_msg = msg
                break

        self.assertIsNotNone(result_msg)
        self.assertEqual(result_msg.payload, "")

    def test_recording_worker_local_keeps_trailing_audio_and_pads_tail(self):
        """Local mode: Trimming darf leise Enden nicht abschneiden; zusätzlich wird Tail-Padding angehängt."""
        import numpy as np

        sample_rate = 16000
        loud = np.ones((int(sample_rate * 1.0), 1), dtype=np.float32) * 0.03
        quiet_tail = np.concatenate(
            [
                np.ones((int(sample_rate * 0.5), 1), dtype=np.float32) * 0.01,
                np.zeros((int(sample_rate * 0.1), 1), dtype=np.float32),
            ],
            axis=0,
        )

        daemon = WhisperDaemon(mode="local", language="en")
        daemon._stop_event = threading.Event()

        mock_sd = MagicMock()
        mock_sd.InputStream.return_value.__enter__.return_value = MagicMock()

        chunks = [loud, quiet_tail]

        def side_effect_sleep(*_args):
            call_args = mock_sd.InputStream.call_args
            callback = call_args[1].get("callback") if call_args else None
            if callback and chunks:
                callback(chunks.pop(0), 0, None, None)
            if not chunks:
                daemon._stop_event.set()

        mock_sd.sleep.side_effect = side_effect_sleep

        mock_provider = MagicMock()
        mock_provider.transcribe_audio.return_value = "ok"
        mock_player = MagicMock()

        with (
            patch.dict(
                sys.modules,
                {"sounddevice": mock_sd, "soundfile": MagicMock()},
            ),
            patch("whisper_daemon.get_provider", MagicMock(return_value=mock_provider)),
            patch(
                "whisper_daemon.get_sound_player", MagicMock(return_value=mock_player)
            ),
            patch(
                "whisper_daemon.tempfile.mkstemp", return_value=(123, "/tmp/fake.wav")
            ),
            patch("whisper_daemon.os.close"),
            patch("whisper_daemon.os.path.exists", return_value=True),
            patch("whisper_daemon.os.unlink"),
        ):
            daemon._recording_worker()

        # audio = 1.0s loud + 0.5s quiet + 0.1s silence + 0.2s tail padding
        audio_arg = mock_provider.transcribe_audio.call_args[0][0]
        self.assertEqual(int(audio_arg.shape[0]), int(sample_rate * 1.8))


class TestTestDictation(unittest.TestCase):
    """Tests für Test-Dictation-Methoden (Onboarding Wizard)."""

    def test_stop_test_dictation_preserves_callback(self):
        """stop_test_dictation() behält den Callback für das Ergebnis."""
        daemon = WhisperDaemon(mode="local")
        callback = MagicMock()

        # Simulate starting a test run
        daemon._test_run_active = True
        daemon._test_run_callback = callback

        with patch.object(daemon, "_stop_recording"):
            daemon.stop_test_dictation()

        # Callback should still be set
        self.assertEqual(daemon._test_run_callback, callback)

    def test_cancel_test_dictation_clears_callback(self):
        """cancel_test_dictation() löscht den Callback um veraltete Ergebnisse zu verwerfen."""
        daemon = WhisperDaemon(mode="local")
        callback = MagicMock()

        # Simulate starting a test run
        daemon._test_run_active = True
        daemon._test_run_callback = callback

        with patch.object(daemon, "_stop_recording"):
            daemon.cancel_test_dictation()

        # Callback should be cleared
        self.assertIsNone(daemon._test_run_callback)

    def test_finish_test_run_calls_callback_when_set(self):
        """_finish_test_run() ruft den Callback auf wenn er gesetzt ist."""
        daemon = WhisperDaemon(mode="local")
        callback = MagicMock()

        daemon._test_run_active = True
        daemon._test_run_callback = callback

        daemon._finish_test_run("Hello World", None)

        callback.assert_called_once_with("Hello World", None)
        self.assertFalse(daemon._test_run_active)
        self.assertIsNone(daemon._test_run_callback)

    def test_finish_test_run_skips_callback_when_none(self):
        """_finish_test_run() überspringt Callback wenn er None ist (nach cancel)."""
        daemon = WhisperDaemon(mode="local")

        daemon._test_run_active = True
        daemon._test_run_callback = None  # Cleared by cancel

        # Should not raise, should just clean up state
        daemon._finish_test_run("Hello World", None)

        self.assertFalse(daemon._test_run_active)

    def test_start_test_dictation_sets_callback(self):
        """start_test_dictation() setzt den Callback korrekt."""
        daemon = WhisperDaemon(mode="local")
        callback = MagicMock()

        with patch.object(daemon, "_start_recording"):
            daemon.start_test_dictation(callback)

        self.assertTrue(daemon._test_run_active)
        self.assertEqual(daemon._test_run_callback, callback)
