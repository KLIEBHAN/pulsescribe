"""Tests für Provider-Module."""


import sys
from types import SimpleNamespace

import pytest


class TestProviderFactory:
    """Tests für Provider-Factory."""

    @pytest.mark.parametrize(
        "mode",
        ["openai", "deepgram", "groq", "local"],
        ids=["openai", "deepgram", "groq", "local"],
    )
    def test_get_provider_has_default_model(self, mode: str):
        """Provider wird erstellt und liefert ein Default-Model aus DEFAULT_MODELS."""
        from providers import DEFAULT_MODELS, get_provider

        provider = get_provider(mode)
        assert provider.name == mode
        assert provider.default_model == DEFAULT_MODELS[mode]

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


@pytest.mark.parametrize(
    ("module_name", "module_path", "env_key", "dependency_name", "class_name"),
    [
        ("openai", "providers.openai", "OPENAI_API_KEY", "openai", "OpenAI"),
        ("deepgram", "providers.deepgram", "DEEPGRAM_API_KEY", "deepgram", "DeepgramClient"),
        ("groq", "providers.groq", "GROQ_API_KEY", "groq", "Groq"),
    ],
    ids=["openai", "deepgram", "groq"],
)
def test_provider_client_reinitializes_when_api_key_changes(
    monkeypatch,
    module_name: str,
    module_path: str,
    env_key: str,
    dependency_name: str,
    class_name: str,
):
    module = __import__(module_path, fromlist=["dummy"])
    monkeypatch.setattr(module, "_client", None)
    monkeypatch.setattr(module, "_client_signature", None)

    created_with: list[str] = []

    class _FakeClient:
        def __init__(self, api_key=None, **_kwargs):
            created_with.append(api_key)

    monkeypatch.setitem(
        sys.modules,
        dependency_name,
        SimpleNamespace(**{class_name: _FakeClient}),
    )

    monkeypatch.setenv(env_key, f"{module_name}-key-1")
    first = module._get_client()
    second = module._get_client()

    assert first is second
    assert created_with == [f"{module_name}-key-1"]

    monkeypatch.setenv(env_key, f"{module_name}-key-2")
    third = module._get_client()

    assert third is not first
    assert created_with == [f"{module_name}-key-1", f"{module_name}-key-2"]
