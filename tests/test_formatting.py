"""Tests für Formatierungs-Hilfsfunktionen."""

from transcribe import _format_duration, _log_preview


class TestFormatDuration:
    """Tests für _format_duration() - Zeitmessung lesbar formatieren."""

    def test_milliseconds_short(self):
        """Kurze Zeiten werden in Millisekunden angezeigt."""
        assert _format_duration(0) == "0ms"
        assert _format_duration(500) == "500ms"

    def test_milliseconds_boundary(self):
        """Grenzwert: 999ms bleibt in Millisekunden."""
        assert _format_duration(999) == "999ms"

    def test_seconds_boundary(self):
        """Grenzwert: 1000ms wird zu Sekunden."""
        assert _format_duration(1000) == "1.00s"

    def test_seconds_decimal(self):
        """Sekunden werden mit 2 Dezimalstellen angezeigt."""
        assert _format_duration(1500) == "1.50s"
        assert _format_duration(2345) == "2.35s"  # Gerundet

    def test_float_input(self):
        """Float-Eingaben werden korrekt verarbeitet."""
        assert _format_duration(1234.56) == "1.23s"
        assert _format_duration(500.7) == "501ms"  # Gerundet auf ganze ms


class TestLogPreview:
    """Tests für _log_preview() - Text für Logs kürzen."""

    def test_short_text_unchanged(self):
        """Kurze Texte bleiben unverändert."""
        assert _log_preview("hello", 10) == "hello"
        assert _log_preview("hi") == "hi"

    def test_exact_length_unchanged(self):
        """Text mit exakter max_length bleibt unverändert."""
        text = "a" * 100
        assert _log_preview(text, 100) == text

    def test_long_text_truncated(self):
        """Lange Texte werden mit Ellipsis gekürzt."""
        text = "a" * 200
        result = _log_preview(text, 100)
        assert result == "a" * 100 + "..."
        assert len(result) == 103  # 100 + "..."

    def test_empty_string(self):
        """Leerer String bleibt leer."""
        assert _log_preview("") == ""

    def test_custom_max_length(self):
        """Benutzerdefinierte max_length funktioniert."""
        assert _log_preview("hello world", 5) == "hello..."
        assert _log_preview("hello world", 50) == "hello world"
