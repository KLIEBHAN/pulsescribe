"""Integration-Tests für LLM-Refine Fallback-Verhalten."""

from unittest.mock import Mock, patch


class TestMaybeRefineTranscript:
    """Tests für maybe_refine_transcript() - Fehlerbehandlung und Fallbacks."""

    def test_no_refine_returns_raw(self):
        """Ohne --refine wird Rohtext zurückgegeben."""
        from transcribe import maybe_refine_transcript

        args = Mock(refine=False, no_refine=False)

        result = maybe_refine_transcript("Rohtext", args)

        assert result == "Rohtext"

    def test_no_refine_flag_returns_raw(self):
        """--no-refine überschreibt --refine und gibt Rohtext zurück."""
        from transcribe import maybe_refine_transcript

        args = Mock(refine=True, no_refine=True)

        result = maybe_refine_transcript("Rohtext", args)

        assert result == "Rohtext"

    def test_api_error_returns_raw(self):
        """Bei API-Fehler wird Rohtext zurückgegeben."""
        from openai import APIError
        from transcribe import maybe_refine_transcript

        args = Mock(
            refine=True,
            no_refine=False,
            refine_model=None,
            refine_provider=None,
            context=None,
        )

        with patch("transcribe.refine_transcript") as mock_refine:
            mock_refine.side_effect = APIError(
                message="Service unavailable",
                request=Mock(),
                body=None,
            )

            result = maybe_refine_transcript("Rohtext", args)

        assert result == "Rohtext"

    def test_missing_api_key_returns_raw(self):
        """Bei fehlendem API-Key (ValueError) wird Rohtext zurückgegeben."""
        from transcribe import maybe_refine_transcript

        args = Mock(
            refine=True,
            no_refine=False,
            refine_model=None,
            refine_provider=None,
            context=None,
        )

        with patch("transcribe.refine_transcript") as mock_refine:
            mock_refine.side_effect = ValueError("OPENROUTER_API_KEY nicht gesetzt")

            result = maybe_refine_transcript("Rohtext", args)

        assert result == "Rohtext"

    def test_rate_limit_returns_raw(self):
        """Bei Rate-Limit wird Rohtext zurückgegeben."""
        from openai import RateLimitError
        from transcribe import maybe_refine_transcript

        args = Mock(
            refine=True,
            no_refine=False,
            refine_model=None,
            refine_provider=None,
            context=None,
        )

        with patch("transcribe.refine_transcript") as mock_refine:
            mock_refine.side_effect = RateLimitError(
                message="Rate limit exceeded",
                response=Mock(status_code=429),
                body=None,
            )

            result = maybe_refine_transcript("Rohtext", args)

        assert result == "Rohtext"

    def test_successful_refine(self):
        """Bei erfolgreichem Refine wird verarbeiteter Text zurückgegeben."""
        from transcribe import maybe_refine_transcript

        args = Mock(
            refine=True,
            no_refine=False,
            refine_model=None,
            refine_provider=None,
            context=None,
        )

        with patch("transcribe.refine_transcript") as mock_refine:
            mock_refine.return_value = "Verarbeiteter Text"

            result = maybe_refine_transcript("Rohtext", args)

        assert result == "Verarbeiteter Text"
        mock_refine.assert_called_once()
