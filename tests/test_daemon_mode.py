import unittest
from unittest.mock import MagicMock, patch
import threading
import os
import sys
from pathlib import Path

# Add project root to path
sys.path.append(str(Path(__file__).parent.parent))

from whisper_daemon import WhisperDaemon

class TestDaemonMode(unittest.TestCase):
    def setUp(self):
        self.mock_transcribe_module = MagicMock()
        self.mock_transcribe_module.DEFAULT_DEEPGRAM_MODEL = "nova-3"
        self.patcher = patch("whisper_daemon._get_transcribe", return_value=self.mock_transcribe_module)
        self.patcher.start()
        
    def tearDown(self):
        self.patcher.stop()

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
        
        with patch.dict(os.environ, {"WHISPER_GO_STREAMING": "true"}), \
             patch("whisper_daemon.INTERIM_FILE"):
            daemon._start_recording()
            
        mock_thread_cls.assert_called_once()
        args, kwargs = mock_thread_cls.call_args
        self.assertEqual(kwargs["target"], daemon._streaming_worker)
        self.assertEqual(kwargs["name"], "StreamingWorker")

    @patch("whisper_daemon.threading.Thread")
    def test_start_recording_deepgram_no_streaming(self, mock_thread_cls):
        """Test that Deepgram mode (streaming disabled) starts recording worker."""
        daemon = WhisperDaemon(mode="deepgram")
        
        with patch.dict(os.environ, {"WHISPER_GO_STREAMING": "false"}), \
             patch("whisper_daemon.INTERIM_FILE"):
            daemon._start_recording()
            
        mock_thread_cls.assert_called_once()
        args, kwargs = mock_thread_cls.call_args
        self.assertEqual(kwargs["target"], daemon._recording_worker)
        self.assertEqual(kwargs["name"], "RecordingWorker")

    @patch("whisper_daemon.threading.Thread")
    def test_start_recording_deepgram_streaming_default_when_unset(self, mock_thread_cls):
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
        
        with patch.dict(os.environ, {"WHISPER_GO_MODE": "deepgram"}), \
             patch.object(sys, "argv", ["whisper-daemon"]), \
             patch("whisper_daemon.WhisperDaemon.run"):
            
            whisper_daemon.main()

        mock_daemon_cls.assert_called_once()
        _, kwargs = mock_daemon_cls.call_args
        self.assertEqual(kwargs.get("mode"), "deepgram")

    @patch("whisper_daemon.WhisperDaemon")
    def test_main_uses_cli_mode_over_env(self, mock_daemon_cls):
        """CLI --mode should override WHISPER_GO_MODE env variable."""
        import whisper_daemon
        
        with patch.dict(os.environ, {"WHISPER_GO_MODE": "openai"}), \
             patch.object(sys, "argv", ["whisper-daemon", "--mode", "deepgram"]), \
             patch("whisper_daemon.WhisperDaemon.run"):
            
            whisper_daemon.main()

        mock_daemon_cls.assert_called_once()
        _, kwargs = mock_daemon_cls.call_args
        self.assertEqual(kwargs.get("mode"), "deepgram")



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
                callback = kw.get('callback')
                if callback:
                    # Call with dummy data
                    import numpy as np
                    callback(np.zeros((100,1), dtype='float32'), 100, None, None)
            
            daemon._stop_event.set()
        mock_sd.sleep.side_effect = side_effect_sleep
        
        # Mock tempfile
        with patch.dict(sys.modules, {
                "sounddevice": mock_sd, 
                "soundfile": mock_sf, 
                "numpy": mock_np
            }), \
             patch("whisper_daemon.tempfile.mkstemp", return_value=(123, "/tmp/fake.wav")), \
             patch("whisper_daemon.os.close"), \
             patch("whisper_daemon.os.unlink"), \
             patch("whisper_daemon.os.path.exists", return_value=True):
            
            daemon._recording_worker()
            
            # Verify transcribe called
            self.mock_transcribe_module.transcribe.assert_called_once()
            call_args = self.mock_transcribe_module.transcribe.call_args
            self.assertEqual(str(call_args[0][0]), "/tmp/fake.wav")
            self.assertEqual(call_args[1]["mode"], "openai")
            self.assertEqual(call_args[1]["language"], "de")
            
            # Verify result put in queue
            self.assertFalse(daemon._result_queue.empty())

if __name__ == "__main__":
    unittest.main()
