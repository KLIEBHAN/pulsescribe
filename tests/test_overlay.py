"""Tests für overlay.py – Untertitel-Overlay."""

import pytest

from overlay import truncate_text, MAX_TEXT_LENGTH


class TestTruncateText:
    """Tests für truncate_text() – Text für Overlay kürzen."""

    @pytest.mark.parametrize(
        "text,max_length,expected",
        [
            ("Hallo", 100, "Hallo"),
            ("Kurz", MAX_TEXT_LENGTH, "Kurz"),
            ("a" * 100, 100, "a" * 100),
            ("a" * 120, 100, "a" * 100 + "…"),
            ("", 100, ""),
            ("  Hallo  ", 100, "Hallo"),
            (
                "Dies ist ein sehr langer Text der definitiv gekürzt werden muss weil er zu lang ist und mehr als hundert Zeichen hat",
                100,
                "Dies ist ein sehr langer Text der definitiv gekürzt werden muss weil er zu lang ist und mehr als hun…",
            ),
            ("Test ", 4, "Test"),
            ("Test  ", 4, "Test"),
        ],
        ids=[
            "short_text",
            "default_max",
            "exact_length",
            "truncated",
            "empty",
            "strips_whitespace",
            "realistic_long_text",
            "trailing_space_exact",
            "trailing_spaces_truncate",
        ],
    )
    def test_truncate_text(self, text, max_length, expected):
        """Verschiedene Texte werden korrekt gekürzt."""
        assert truncate_text(text, max_length) == expected

    def test_uses_ellipsis_unicode(self):
        """Verwendet Unicode-Ellipsis (…) statt drei Punkte (...)."""
        result = truncate_text("a" * 100, 10)
        assert result.endswith("…")
        assert not result.endswith("...")

    def test_default_max_length(self):
        """Default max_length ist MAX_TEXT_LENGTH (120)."""
        long_text = "a" * 150
        result = truncate_text(long_text)
        assert len(result) == MAX_TEXT_LENGTH + 1  # +1 für Ellipsis
