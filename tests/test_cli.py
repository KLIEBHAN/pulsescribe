"""Tests für CLI-Argument-Parsing."""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from transcribe import parse_args


class TestParseArgs:
    """Tests für parse_args() - CLI-Validierung."""

    def test_audio_file(self, clean_env):
        """Audio-Datei wird als Path geparst."""
        with patch.object(sys, "argv", ["transcribe.py", "audio.mp3"]):
            args = parse_args()

        assert args.audio == Path("audio.mp3")

    def test_record_flag(self, clean_env):
        """--record Flag wird erkannt."""
        with patch.object(sys, "argv", ["transcribe.py", "--record"]):
            args = parse_args()

        assert args.record is True
        assert args.audio is None

    def test_record_daemon_flag(self, clean_env):
        """--record-daemon Flag wird erkannt."""
        with patch.object(sys, "argv", ["transcribe.py", "--record-daemon"]):
            args = parse_args()

        assert args.record_daemon is True

    def test_no_audio_source_error(self, clean_env):
        """Fehler wenn keine Audio-Quelle angegeben."""
        with patch.object(sys, "argv", ["transcribe.py"]):
            with pytest.raises(SystemExit):
                parse_args()

    def test_audio_and_record_conflict(self, clean_env):
        """Audio-Datei und --record schließen sich aus."""
        with patch.object(sys, "argv", ["transcribe.py", "audio.mp3", "--record"]):
            with pytest.raises(SystemExit):
                parse_args()

    def test_record_and_daemon_conflict(self, clean_env):
        """--record und --record-daemon schließen sich aus."""
        with patch.object(
            sys, "argv", ["transcribe.py", "--record", "--record-daemon"]
        ):
            with pytest.raises(SystemExit):
                parse_args()

    def test_mode_choices(self, clean_env):
        """--mode akzeptiert nur gültige Werte."""
        for mode in ["api", "local", "deepgram", "groq"]:
            with patch.object(
                sys, "argv", ["transcribe.py", "--record", "--mode", mode]
            ):
                args = parse_args()
                assert args.mode == mode

    def test_mode_invalid(self, clean_env):
        """Ungültiger --mode Wert führt zu Fehler."""
        with patch.object(
            sys, "argv", ["transcribe.py", "--record", "--mode", "invalid"]
        ):
            with pytest.raises(SystemExit):
                parse_args()

    def test_mode_env_default(self, monkeypatch, clean_env):
        """WHISPER_GO_MODE setzt Default-Mode."""
        monkeypatch.setenv("WHISPER_GO_MODE", "deepgram")

        with patch.object(sys, "argv", ["transcribe.py", "--record"]):
            args = parse_args()

        assert args.mode == "deepgram"

    def test_mode_cli_beats_env(self, monkeypatch, clean_env):
        """CLI --mode schlägt ENV."""
        monkeypatch.setenv("WHISPER_GO_MODE", "deepgram")

        with patch.object(sys, "argv", ["transcribe.py", "--record", "--mode", "groq"]):
            args = parse_args()

        assert args.mode == "groq"

    def test_copy_flag(self, clean_env):
        """--copy Flag wird erkannt."""
        with patch.object(sys, "argv", ["transcribe.py", "--record", "--copy"]):
            args = parse_args()

        assert args.copy is True

    def test_language_option(self, clean_env):
        """--language Option wird geparst."""
        with patch.object(
            sys, "argv", ["transcribe.py", "--record", "--language", "de"]
        ):
            args = parse_args()

        assert args.language == "de"

    def test_refine_flag(self, clean_env):
        """--refine Flag wird erkannt."""
        with patch.object(sys, "argv", ["transcribe.py", "--record", "--refine"]):
            args = parse_args()

        assert args.refine is True

    def test_no_refine_flag(self, clean_env):
        """--no-refine Flag wird erkannt."""
        with patch.object(sys, "argv", ["transcribe.py", "--record", "--no-refine"]):
            args = parse_args()

        assert args.no_refine is True

    def test_refine_env_default(self, monkeypatch, clean_env):
        """WHISPER_GO_REFINE=true setzt Default."""
        monkeypatch.setenv("WHISPER_GO_REFINE", "true")

        with patch.object(sys, "argv", ["transcribe.py", "--record"]):
            args = parse_args()

        assert args.refine is True

    def test_context_choices(self, clean_env):
        """--context akzeptiert nur gültige Werte."""
        for ctx in ["email", "chat", "code", "default"]:
            with patch.object(
                sys, "argv", ["transcribe.py", "--record", "--context", ctx]
            ):
                args = parse_args()
                assert args.context == ctx

    def test_no_streaming_flag(self, clean_env):
        """--no-streaming Flag wird erkannt."""
        with patch.object(sys, "argv", ["transcribe.py", "--record", "--no-streaming"]):
            args = parse_args()

        assert args.no_streaming is True

    def test_short_flags(self, clean_env):
        """-r und -c Kurzformen funktionieren."""
        with patch.object(sys, "argv", ["transcribe.py", "-r", "-c"]):
            args = parse_args()

        assert args.record is True
        assert args.copy is True
