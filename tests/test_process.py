"""Tests für Prozess-Handling und PID-Validierung."""

import subprocess
from unittest.mock import Mock, patch

from transcribe import _is_whisper_go_process


class TestIsWhisperGoProcess:
    """Tests für _is_whisper_go_process() - Sicherheits-Check für PIDs."""

    def test_matching_process(self):
        """Prozess mit transcribe.py --record-daemon wird erkannt."""
        mock_result = Mock()
        mock_result.stdout = "python transcribe.py --record-daemon"

        with patch("subprocess.run", return_value=mock_result):
            assert _is_whisper_go_process(12345) is True

    def test_non_matching_process(self):
        """Fremder Prozess wird nicht erkannt."""
        mock_result = Mock()
        mock_result.stdout = "python other_script.py"

        with patch("subprocess.run", return_value=mock_result):
            assert _is_whisper_go_process(12345) is False

    def test_partial_match_transcribe_only(self):
        """Nur transcribe.py ohne --record-daemon reicht nicht."""
        mock_result = Mock()
        mock_result.stdout = "python transcribe.py --record"  # Ohne daemon

        with patch("subprocess.run", return_value=mock_result):
            assert _is_whisper_go_process(12345) is False

    def test_partial_match_daemon_only(self):
        """Nur --record-daemon ohne transcribe.py reicht nicht."""
        mock_result = Mock()
        mock_result.stdout = "python other.py --record-daemon"

        with patch("subprocess.run", return_value=mock_result):
            assert _is_whisper_go_process(12345) is False

    def test_timeout_returns_false(self):
        """Timeout gibt False zurück."""
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("ps", 1)):
            assert _is_whisper_go_process(12345) is False

    def test_other_exception_returns_false(self):
        """Andere Exceptions geben False zurück."""
        with patch("subprocess.run", side_effect=OSError("Process not found")):
            assert _is_whisper_go_process(12345) is False

    def test_empty_stdout(self):
        """Leere Ausgabe gibt False zurück."""
        mock_result = Mock()
        mock_result.stdout = ""

        with patch("subprocess.run", return_value=mock_result):
            assert _is_whisper_go_process(12345) is False

    def test_correct_ps_command(self):
        """Korrekter ps-Befehl wird aufgerufen."""
        mock_result = Mock()
        mock_result.stdout = "python transcribe.py --record-daemon"

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            _is_whisper_go_process(42)

            mock_run.assert_called_once_with(
                ["ps", "-p", "42", "-o", "command="],
                capture_output=True,
                text=True,
                timeout=1,
            )
