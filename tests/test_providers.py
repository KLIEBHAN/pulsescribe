"""Tests für Provider-Module."""

from unittest.mock import Mock, patch

import pytest


class TestProviderFactory:
    """Tests für Provider-Factory."""

    def test_get_provider_openai(self):
        """OpenAI Provider wird korrekt erstellt."""
        from providers import get_provider
        provider = get_provider("openai")
        assert provider.name == "openai"
        assert provider.default_model == "gpt-4o-transcribe"

    def test_get_provider_deepgram(self):
        """Deepgram Provider wird korrekt erstellt."""
        from providers import get_provider
        provider = get_provider("deepgram")
        assert provider.name == "deepgram"
        assert provider.default_model == "nova-3"

    def test_get_provider_groq(self):
        """Groq Provider wird korrekt erstellt."""
        from providers import get_provider
        provider = get_provider("groq")
        assert provider.name == "groq"
        assert provider.default_model == "whisper-large-v3"

    def test_get_provider_local(self):
        """Local Provider wird korrekt erstellt."""
        from providers import get_provider
        provider = get_provider("local")
        assert provider.name == "local"
        assert provider.default_model == "turbo"

    def test_get_provider_unknown_raises(self):
        """Unbekannter Provider wirft ValueError."""
        from providers import get_provider
        with pytest.raises(ValueError, match="Unbekannter Provider"):
            get_provider("unknown")


class TestDefaultModels:
    """Tests für Default-Modelle."""

    def test_default_models_complete(self):
        """Alle Provider haben Default-Modelle."""
        from providers import DEFAULT_MODELS
        assert "openai" in DEFAULT_MODELS
        assert "deepgram" in DEFAULT_MODELS
        assert "groq" in DEFAULT_MODELS
        assert "local" in DEFAULT_MODELS


class TestProviderInterface:
    """Tests für Provider-Interface."""

    def test_openai_supports_streaming(self):
        """OpenAI unterstützt kein Streaming."""
        from providers import get_provider
        provider = get_provider("openai")
        assert provider.supports_streaming() is False

    def test_deepgram_supports_streaming(self):
        """Deepgram REST unterstützt kein Streaming."""
        from providers import get_provider
        provider = get_provider("deepgram")
        assert provider.supports_streaming() is False

    def test_groq_supports_streaming(self):
        """Groq unterstützt kein Streaming."""
        from providers import get_provider
        provider = get_provider("groq")
        assert provider.supports_streaming() is False

    def test_local_supports_streaming(self):
        """Lokales Whisper unterstützt kein Streaming."""
        from providers import get_provider
        provider = get_provider("local")
        assert provider.supports_streaming() is False


class TestProviderValidation:
    """Tests für API-Key Validierung."""

    def test_openai_validates_on_transcribe(self, monkeypatch):
        """OpenAI validiert API-Key bei transcribe()."""
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        from providers import get_provider
        provider = get_provider("openai")

        from pathlib import Path
        with pytest.raises(ValueError, match="OPENAI_API_KEY"):
            provider.transcribe(Path("/tmp/test.wav"))

    def test_deepgram_validates_on_transcribe(self, monkeypatch):
        """Deepgram validiert API-Key bei transcribe()."""
        monkeypatch.delenv("DEEPGRAM_API_KEY", raising=False)
        from providers import get_provider
        provider = get_provider("deepgram")

        from pathlib import Path
        with pytest.raises(ValueError, match="DEEPGRAM_API_KEY"):
            provider.transcribe(Path("/tmp/test.wav"))

    def test_groq_validates_on_transcribe(self, monkeypatch):
        """Groq validiert API-Key bei transcribe()."""
        monkeypatch.delenv("GROQ_API_KEY", raising=False)
        from providers import get_provider
        provider = get_provider("groq")

        from pathlib import Path
        with pytest.raises(ValueError, match="GROQ_API_KEY"):
            provider.transcribe(Path("/tmp/test.wav"))
