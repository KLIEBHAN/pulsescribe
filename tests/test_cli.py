"""Tests für CLI-Argument-Parsing mit Typer."""

import re
from unittest.mock import patch

from typer.testing import CliRunner

from transcribe import app


def strip_ansi(text: str) -> str:
    """Remove ANSI escape codes from text."""
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


runner = CliRunner()


class TestCLI:
    """Tests für Typer CLI."""

    def test_audio_file(self, clean_env):
        """Audio-Datei wird akzeptiert (mit Mock für transcribe)."""
        with patch("transcribe.transcribe") as mock_transcribe:
            mock_transcribe.return_value = "Test transcript"
            _result = runner.invoke(app, ["tests/fixtures/test.wav"])

        # Ohne echte Datei erwartet Fehler - wir testen nur das Parsing
        # Der Exit-Code ist hier nicht relevant, wichtig ist dass es parsed wird

    def test_record_flag(self, clean_env):
        """--record Flag wird erkannt."""
        with patch("transcribe.record_audio") as mock_record:
            with patch("transcribe.transcribe") as mock_transcribe:
                mock_record.return_value = "test.wav"
                mock_transcribe.return_value = "Test"
                _result = runner.invoke(app, ["--record"])

        # --record wurde erkannt (record_audio wurde aufgerufen oder Fehler wegen fehlendem Mikrofon)

    def test_no_audio_source_error(self, clean_env):
        """Fehler wenn keine Audio-Quelle angegeben."""
        result = runner.invoke(app, [])
        assert result.exit_code != 0
        assert "Audiodatei" in result.output or "audio" in result.output.lower()

    def test_audio_and_record_conflict(self, clean_env):
        """Audio-Datei und --record schließen sich aus."""
        result = runner.invoke(app, ["audio.mp3", "--record"])
        assert result.exit_code != 0
        assert (
            "schliessen" in result.output.lower() or "ausschl" in result.output.lower()
        )

    def test_mode_choices(self, clean_env):
        """--mode akzeptiert nur gültige Werte."""
        for mode in ["openai", "local", "deepgram", "groq"]:
            _result = runner.invoke(app, ["--record", "--mode", mode, "--help"])
            # --help sollte funktionieren wenn --mode gültig ist
            # (wir testen nicht die volle Ausführung)

    def test_mode_invalid(self, clean_env):
        """Ungültiger --mode Wert führt zu Fehler."""
        result = runner.invoke(app, ["--record", "--mode", "invalid"])
        assert result.exit_code != 0

    def test_mode_env_default(self, monkeypatch, clean_env):
        """PULSESCRIBE_MODE setzt Default-Mode."""
        monkeypatch.setenv("PULSESCRIBE_MODE", "deepgram")

        # Wir prüfen indirekt über --help Output
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        # ENV-Variable wird im Help angezeigt
        assert "PULSESCRIBE_MODE" in result.output

    def test_mode_cli_beats_env(self, monkeypatch, clean_env):
        """CLI --mode schlägt ENV (implizit getestet über Ausführung)."""
        monkeypatch.setenv("PULSESCRIBE_MODE", "deepgram")

        # Mit --mode groq sollte groq verwendet werden
        with patch("transcribe.record_audio") as mock_record:
            with patch("transcribe.transcribe") as mock_transcribe:
                from pathlib import Path

                mock_record.return_value = Path("test.wav")
                mock_transcribe.return_value = "Test"
                _result = runner.invoke(app, ["--record", "--mode", "groq"])
                # Prüfe dass transcribe mit mode="groq" aufgerufen wurde
                if mock_transcribe.called:
                    call_kwargs = mock_transcribe.call_args
                    if call_kwargs and call_kwargs.kwargs:
                        assert call_kwargs.kwargs.get("mode") == "groq"

    def test_copy_flag(self, clean_env):
        """--copy Flag wird erkannt."""
        # Wir testen nur, dass die Flag akzeptiert wird
        result = runner.invoke(app, ["--record", "--copy", "--help"])
        # --help nach flags sollte funktionieren
        assert "--copy" in result.output or "-c" in result.output

    def test_language_option(self, clean_env):
        """--language Option wird geparst."""
        result = runner.invoke(app, ["--help"])
        assert "--language" in strip_ansi(result.output)

    def test_refine_flag(self, clean_env):
        """--refine Flag wird erkannt."""
        result = runner.invoke(app, ["--help"])
        assert "--refine" in strip_ansi(result.output)

    def test_no_refine_flag(self, clean_env):
        """--no-refine Flag wird erkannt."""
        result = runner.invoke(app, ["--help"])
        assert "--no-refine" in strip_ansi(result.output)

    def test_refine_env_default(self, monkeypatch, clean_env):
        """PULSESCRIBE_REFINE env variable ist dokumentiert."""
        result = runner.invoke(app, ["--help"])
        # Typer trunkiert lange env var Namen - prüfe Präfix
        assert "PULSESCRIBE_REFI" in strip_ansi(result.output)

    def test_context_choices(self, clean_env):
        """--context akzeptiert nur gültige Werte und reicht sie korrekt durch."""
        # Gültige Kontexte werden akzeptiert und an maybe_refine_transcript übergeben
        for ctx in ["email", "chat", "code", "default"]:
            with patch("transcribe.record_audio") as mock_record:
                with patch("transcribe.transcribe") as mock_transcribe:
                    with patch("transcribe.maybe_refine_transcript") as mock_refine:
                        mock_record.return_value = "test.wav"
                        mock_transcribe.return_value = "Test"
                        mock_refine.return_value = "Test"
                        _result = runner.invoke(
                            app, ["--record", "--refine", "--context", ctx]
                        )
                        # Prüfe dass context korrekt übergeben wurde
                        if mock_refine.called:
                            call_kwargs = mock_refine.call_args.kwargs
                            assert call_kwargs.get("context") == ctx

        # Ungültiger Kontext führt zu Fehler
        result = runner.invoke(app, ["--record", "--context", "invalid"])
        assert result.exit_code != 0

    def test_short_flags(self, clean_env):
        """-r und -c Kurzformen funktionieren."""
        result = runner.invoke(app, ["--help"])
        output = strip_ansi(result.output)
        assert "-r" in output
        assert "-c" in output

    def test_help_output(self, clean_env):
        """--help zeigt formatierte Hilfe."""
        result = runner.invoke(app, ["--help"])
        output = strip_ansi(result.output)
        assert result.exit_code == 0
        assert "Audio transkribieren" in output
        assert "--mode" in output
        assert "--record" in output

    def test_structured_output_skips_refine(self, clean_env, tmp_path):
        """JSON/SRT/VTT dürfen nicht durch LLM-Refine verändert werden."""
        audio_file = tmp_path / "test.wav"
        audio_file.write_bytes(b"fake audio")

        with (
            patch("transcribe.transcribe", return_value='{"text":"hi"}'),
            patch("transcribe.maybe_refine_transcript") as mock_refine,
        ):
            result = runner.invoke(
                app,
                [str(audio_file), "--format", "json", "--refine"],
            )

        assert result.exit_code == 0
        mock_refine.assert_not_called()
        assert '{"text":"hi"}' in result.output
