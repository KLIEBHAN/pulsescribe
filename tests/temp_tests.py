
    @patch("whisper_daemon.threading.Thread")
    def test_start_recording_deepgram_streaming_default_when_unset(self, mock_thread_cls):
        """Deepgram mode defaults to streaming when WHISPER_GO_STREAMING is unset."""
        daemon = WhisperDaemon(mode="deepgram")

        # Ensure the env var is not present for this test
        with patch.dict(os.environ, {}, clear=True):
            # os.environ.pop("WHISPER_GO_STREAMING", None) handled by clear=True w/ mock dict
            # But wait, we need other env vars perhaps? 
            # Safer to just pop the one we care about
             with patch.dict(os.environ), patch("whisper_daemon.INTERIM_FILE"):
                if "WHISPER_GO_STREAMING" in os.environ:
                    del os.environ["WHISPER_GO_STREAMING"]
                
                daemon._start_recording()

        mock_thread_cls.assert_called_once()
        args, kwargs = mock_thread_cls.call_args
        self.assertEqual(kwargs["target"], daemon._streaming_worker)
        self.assertEqual(kwargs["name"], "StreamingWorker")

    @patch("whisper_daemon.WhisperDaemon")
    def test_main_uses_env_mode_for_deepgram(self, mock_daemon_cls):
        """whisper_daemon.main() uses WHISPER_GO_MODE when set (e.g., deepgram)."""
        import whisper_daemon
        # Ensure we don't accidentally run real arg parsing that kills test
        with patch.dict(os.environ, {"WHISPER_GO_MODE": "deepgram"}), \
             patch.object(sys, "argv", ["whisper-daemon"]):
            
            # We need to mock _daemonize and run to avoid side effects
            with patch("whisper_daemon.transcribe._daemonize"), \
                 patch("whisper_daemon.WhisperDaemon.run"):
                 whisper_daemon.main()

        mock_daemon_cls.assert_called_once()
        _, kwargs = mock_daemon_cls.call_args
        # Expect deepgram mode from env wiring
        self.assertEqual(kwargs.get("mode"), "deepgram")

    @patch("whisper_daemon.WhisperDaemon")
    def test_main_uses_cli_mode_over_env(self, mock_daemon_cls):
        """CLI --mode should override WHISPER_GO_MODE env variable."""
        import whisper_daemon
        with patch.dict(os.environ, {"WHISPER_GO_MODE": "openai"}), \
             patch.object(sys, "argv", ["whisper-daemon", "--mode", "deepgram"]):
            
            with patch("whisper_daemon.transcribe._daemonize"), \
                 patch("whisper_daemon.WhisperDaemon.run"):
                whisper_daemon.main()

        mock_daemon_cls.assert_called_once()
        _, kwargs = mock_daemon_cls.call_args
        # Expect deepgram mode from CLI overriding env
        self.assertEqual(kwargs.get("mode"), "deepgram")
