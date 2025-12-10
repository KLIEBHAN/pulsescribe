"""Integration-Tests für PID-Cleanup und Crash-Recovery."""

import os
import unittest
from unittest.mock import patch

import utils.daemon
from utils.daemon import cleanup_stale_pid_file


class TestCleanupStalePidFile:
    """Tests für _cleanup_stale_pid_file() - Crash-Recovery."""

    def test_no_pid_file_noop(self, temp_files):
        """Ohne PID-File passiert nichts."""
        # PID-File existiert nicht
        assert not utils.daemon.PID_FILE.exists()

        cleanup_stale_pid_file()

        # Immer noch keine Datei
        assert not utils.daemon.PID_FILE.exists()

    def test_stale_pid_file_removed(self, temp_files):
        """PID-File mit nicht-existentem Prozess wird gelöscht."""
        utils.daemon.PID_FILE.write_text("99999")  # Sehr hohe PID

        with patch("os.kill", side_effect=ProcessLookupError):
            cleanup_stale_pid_file()

        assert not utils.daemon.PID_FILE.exists()

    def test_invalid_pid_removed(self, temp_files):
        """PID-File mit ungültigem Inhalt wird gelöscht."""
        utils.daemon.PID_FILE.write_text("not-a-number")

        cleanup_stale_pid_file()

        assert not utils.daemon.PID_FILE.exists()

    def test_own_pid_not_killed(self, temp_files):
        """Eigene PID wird nicht gekillt."""
        own_pid = os.getpid()
        utils.daemon.PID_FILE.write_text(str(own_pid))

        with patch("os.kill") as mock_kill:
            cleanup_stale_pid_file()

        # os.kill sollte nicht aufgerufen werden
        mock_kill.assert_not_called()
        # PID-File bleibt erhalten
        assert utils.daemon.PID_FILE.exists()

    def test_foreign_process_not_killed(self, temp_files):
        """Fremder Prozess (PID-Recycling) wird nicht gekillt."""
        utils.daemon.PID_FILE.write_text("12345")

        with (
            patch("os.kill") as mock_kill,
            patch(
                "utils.daemon.is_whisper_go_process", return_value=False
            ) as mock_check,
        ):
            # Signal 0 (Ping) erfolgreich - Prozess existiert
            mock_kill.side_effect = lambda pid, sig: None if sig == 0 else None

            cleanup_stale_pid_file()

        # _is_whisper_go_process wurde geprüft
        mock_check.assert_called_once_with(12345)
        # PID-File wurde gelöscht (nur File, nicht Prozess)
        assert not utils.daemon.PID_FILE.exists()

    def test_whisper_go_process_killed(self, temp_files):
        """Echter whisper_go Prozess wird gekillt."""
        utils.daemon.PID_FILE.write_text("12345")

        kill_signals = []

        def track_kill(pid, sig):
            kill_signals.append((pid, sig))
            if sig != 0:
                # Nach SIGTERM "stirbt" der Prozess
                raise ProcessLookupError

        with (
            patch("os.kill", side_effect=track_kill),
            patch("utils.daemon.is_whisper_go_process", return_value=True),
        ):
            cleanup_stale_pid_file()

        # Signal 0 (Ping) und SIGTERM wurden gesendet
        assert (12345, 0) in kill_signals
        import signal

        assert (12345, signal.SIGTERM) in kill_signals
        # PID-File wurde gelöscht
        assert not utils.daemon.PID_FILE.exists()

    def test_permission_error_handled(self, temp_files):
        """PermissionError wird abgefangen."""
        # Wir mocken PID_FILE komplett als Objekt, das beim Lesen PermissionError wirft
        mock_path = unittest.mock.MagicMock()
        mock_path.exists.return_value = True
        mock_path.read_text.side_effect = PermissionError
        
        with patch("utils.daemon.PID_FILE", mock_path):
            # Sollte nicht crashen
            cleanup_stale_pid_file()

        # Überprüfe, dass unlink NICHT aufgerufen wurde (weil Exception abgefangen wurde)
        mock_path.unlink.assert_not_called()
