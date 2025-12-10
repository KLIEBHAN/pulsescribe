import unittest
from unittest.mock import MagicMock, patch, ANY
import threading
import sys
import queue
from pathlib import Path

# Add project root to path
sys.path.append(str(Path(__file__).parent.parent))

from whisper_daemon import WhisperDaemon, VAD_THRESHOLD
from utils.state import AppState, DaemonMessage, MessageType

class TestLocalFeedback(unittest.TestCase):
    def setUp(self):
        self.daemon = WhisperDaemon(mode="openai")
        # Disable timers to prevent interference
        self.daemon._stop_result_polling = MagicMock()
        self.daemon._stop_interim_polling = MagicMock()
        self.daemon._overlay = MagicMock() # Mock UI

    def test_start_recording_sets_listening_state(self):
        """Test that _start_recording sets initial state to LISTENING."""
        with patch("whisper_daemon.threading.Thread"), \
             patch("whisper_daemon.INTERIM_FILE"):
            self.daemon._start_recording()
        
        self.assertTrue(self.daemon._recording)
        self.assertEqual(self.daemon._current_state, AppState.LISTENING)
        # Verify overlay update was called with LISTENING
        self.daemon._overlay.update_state.assert_called_with(AppState.LISTENING, None)

    def test_on_audio_level_queues_message(self):
        """Test that _on_audio_level puts message in queue."""
        level = VAD_THRESHOLD + 0.05 # Ensure level is high enough for testing if needed, though this test just checks queuing
        self.daemon._on_audio_level(level)
        
        msg = self.daemon._result_queue.get_nowait()
        self.assertEqual(msg.type, MessageType.AUDIO_LEVEL)
        self.assertEqual(msg.payload, level)

    def test_result_polling_vad_trigger(self):
        """Test that polling switches LISTENING -> RECORDING on threshold."""
        # Setup initial state
        self.daemon._current_state = AppState.LISTENING
        
        # Inject AUDIO_LEVEL message > Threshold
        # Wir nutzen hier explizit den importierten VAD_THRESHOLD um sicherzustellen,
        # dass der Test gegen die echte Config läuft.
        level = VAD_THRESHOLD + 0.001
        msg = DaemonMessage(type=MessageType.AUDIO_LEVEL, payload=level)
        self.daemon._result_queue.put(msg)
        
        # Manually trigger polling callback logic (extract inner function logic or mock it)
        # Since _start_result_polling defines inner function, we can mock NSTimer and capture the callback
        mock_foundation = MagicMock()
        mock_timer_cls = MagicMock()
        mock_foundation.NSTimer = mock_timer_cls
        
        with patch.dict(sys.modules, {"Foundation": mock_foundation}):
            self.daemon._start_result_polling()
            args = mock_timer_cls.scheduledTimerWithTimeInterval_repeats_block_.call_args
            callback = args[0][2] # lambda
            
            # Execute callback
            callback(None)
            
            # Verify state change
            self.assertEqual(self.daemon._current_state, AppState.RECORDING)
            self.daemon._overlay.update_state.assert_called_with(AppState.RECORDING, None)
            
            # Verify overlay audio level update
            self.daemon._overlay.update_audio_level.assert_called_with(level)

    def test_result_polling_no_vad_trigger_if_low(self):
        """Test that polling keeps LISTENING if level is low."""
        self.daemon._current_state = AppState.LISTENING
        
        # Inject AUDIO_LEVEL message < Threshold
        level = VAD_THRESHOLD - 0.001
        msg = DaemonMessage(type=MessageType.AUDIO_LEVEL, payload=level)
        self.daemon._result_queue.put(msg)
        
        mock_foundation = MagicMock()
        mock_timer_cls = MagicMock()
        mock_foundation.NSTimer = mock_timer_cls
        
        with patch.dict(sys.modules, {"Foundation": mock_foundation}):
            self.daemon._start_result_polling()
            callback = mock_timer_cls.scheduledTimerWithTimeInterval_repeats_block_.call_args[0][2]
            callback(None)
            
            self.assertEqual(self.daemon._current_state, AppState.LISTENING)
            # Should still update levels
            self.daemon._overlay.update_audio_level.assert_called_with(level)

    @patch("numpy.mean")
    @patch("numpy.sqrt")
    def test_recording_worker_calculates_rms(self, mock_sqrt, mock_mean):
        """Test that _recording_worker calculates RMS and queues it."""
        import numpy as np
        
        # Setup mocks
        mock_mean.return_value = 100.0
        mock_sqrt.return_value = 10.0 # RMS
        
        mock_sd = MagicMock()
        mock_stream = MagicMock()
        mock_sd.InputStream.return_value.__enter__.return_value = mock_stream
        
        def side_effect_sleep(*args):
             # Trigger callback
            call_args = mock_sd.InputStream.call_args
            callback = call_args[1].get('callback')
            # Fake audio data
            data = np.array([1, 2, 3])
            callback(data, 100, None, None)
            self.daemon._stop_event.set()

        mock_sd.sleep.side_effect = side_effect_sleep
        
        self.daemon._stop_event = threading.Event()
        
        with patch.dict(sys.modules, {"sounddevice": mock_sd, "soundfile": MagicMock(), "numpy": np}), \
             patch("whisper_daemon.get_provider"), \
             patch("whisper_daemon.get_sound_player"), \
             patch("whisper_daemon.tempfile.mkstemp", return_value=(1, "tmp")), \
             patch("whisper_daemon.os.close"), \
             patch("whisper_daemon.os.unlink"), \
             patch("whisper_daemon.os.path.exists", return_value=True):
            
            self.daemon._recording_worker()
            
            # Verify RMS calculation flow
            # 1. AUDIO_LEVEL message should be in queue
            # 2. TRANSCRIPT_RESULT message should be in queue
            
            messages = []
            while not self.daemon._result_queue.empty():
                messages.append(self.daemon._result_queue.get())
            
            # Check for AUDIO_LEVEL
            audio_msgs = [m for m in messages if isinstance(m, DaemonMessage) and m.type == MessageType.AUDIO_LEVEL]
            self.assertTrue(len(audio_msgs) > 0)
            self.assertEqual(audio_msgs[0].payload, 10.0)

if __name__ == "__main__":
    unittest.main()
