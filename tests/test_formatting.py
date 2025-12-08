"""Tests für Formatierungs-Hilfsfunktionen."""

import pytest

from transcribe import _format_duration, _log_preview


class TestFormatDuration:
    """Tests für _format_duration() - Zeitmessung lesbar formatieren."""

    @pytest.mark.parametrize(
        "ms,expected",
        [
            (0, "0ms"),
            (500, "500ms"),
            (999, "999ms"),
            (1000, "1.00s"),
            (1500, "1.50s"),
            (2345, "2.35s"),
            (1234.56, "1.23s"),
            (500.7, "501ms"),
        ],
        ids=[
            "zero",
            "short",
            "boundary_ms",
            "boundary_s",
            "seconds",
            "rounded",
            "float_seconds",
            "float_ms",
        ],
    )
    def test_format_duration(self, ms, expected):
        """Verschiedene Dauern werden korrekt formatiert."""
        assert _format_duration(ms) == expected


class TestLogPreview:
    """Tests für _log_preview() - Text für Logs kürzen."""

    @pytest.mark.parametrize(
        "text,max_length,expected",
        [
            ("hello", 10, "hello"),
            ("hi", 100, "hi"),
            ("a" * 100, 100, "a" * 100),
            ("a" * 200, 100, "a" * 100 + "..."),
            ("", 100, ""),
            ("hello world", 5, "hello..."),
        ],
        ids=[
            "short",
            "default_max",
            "exact_length",
            "truncated",
            "empty",
            "custom_max",
        ],
    )
    def test_log_preview(self, text, max_length, expected):
        """Verschiedene Texte werden korrekt gekürzt."""
        assert _log_preview(text, max_length) == expected
