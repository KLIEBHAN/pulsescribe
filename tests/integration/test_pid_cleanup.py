"""Integration-Tests für PID-Cleanup und Crash-Recovery."""

import os
from unittest.mock import patch

import transcribe
from transcribe import _cleanup_stale_pid_file


class TestCleanupStalePidFile:
    """Tests für _cleanup_stale_pid_file() - Crash-Recovery."""

    def test_no_pid_file_noop(self, temp_files):
        """Ohne PID-File passiert nichts."""
        # PID-File existiert nicht
        assert not transcribe.PID_FILE.exists()

        _cleanup_stale_pid_file()

        # Immer noch keine Datei
        assert not transcribe.PID_FILE.exists()

    def test_stale_pid_file_removed(self, temp_files):
        """PID-File mit nicht-existentem Prozess wird gelöscht."""
        transcribe.PID_FILE.write_text("99999")  # Sehr hohe PID

        with patch("os.kill", side_effect=ProcessLookupError):
            _cleanup_stale_pid_file()

        assert not transcribe.PID_FILE.exists()

    def test_invalid_pid_removed(self, temp_files):
        """PID-File mit ungültigem Inhalt wird gelöscht."""
        transcribe.PID_FILE.write_text("not-a-number")

        _cleanup_stale_pid_file()

        assert not transcribe.PID_FILE.exists()

    def test_own_pid_not_killed(self, temp_files):
        """Eigene PID wird nicht gekillt."""
        own_pid = os.getpid()
        transcribe.PID_FILE.write_text(str(own_pid))

        with patch("os.kill") as mock_kill:
            _cleanup_stale_pid_file()

        # os.kill sollte nicht aufgerufen werden
        mock_kill.assert_not_called()
        # PID-File bleibt erhalten
        assert transcribe.PID_FILE.exists()

    def test_foreign_process_not_killed(self, temp_files):
        """Fremder Prozess (PID-Recycling) wird nicht gekillt."""
        transcribe.PID_FILE.write_text("12345")

        with (
            patch("os.kill") as mock_kill,
            patch(
                "transcribe._is_whisper_go_process", return_value=False
            ) as mock_check,
        ):
            # Signal 0 (Ping) erfolgreich - Prozess existiert
            mock_kill.side_effect = lambda pid, sig: None if sig == 0 else None

            _cleanup_stale_pid_file()

        # _is_whisper_go_process wurde geprüft
        mock_check.assert_called_once_with(12345)
        # PID-File wurde gelöscht (nur File, nicht Prozess)
        assert not transcribe.PID_FILE.exists()

    def test_whisper_go_process_killed(self, temp_files):
        """Echter whisper_go Prozess wird gekillt."""
        transcribe.PID_FILE.write_text("12345")

        kill_signals = []

        def track_kill(pid, sig):
            kill_signals.append((pid, sig))
            if sig != 0:
                # Nach SIGTERM "stirbt" der Prozess
                raise ProcessLookupError

        with (
            patch("os.kill", side_effect=track_kill),
            patch("transcribe._is_whisper_go_process", return_value=True),
        ):
            _cleanup_stale_pid_file()

        # Signal 0 (Ping) und SIGTERM wurden gesendet
        assert (12345, 0) in kill_signals
        import signal

        assert (12345, signal.SIGTERM) in kill_signals
        # PID-File wurde gelöscht
        assert not transcribe.PID_FILE.exists()

    def test_permission_error_handled(self, temp_files):
        """PermissionError wird abgefangen."""
        transcribe.PID_FILE.write_text("12345")

        with patch("os.kill", side_effect=PermissionError):
            # Sollte nicht crashen
            _cleanup_stale_pid_file()

        # PID-File bleibt (keine Berechtigung zum Löschen)
        assert transcribe.PID_FILE.exists()
