"""Tests für Provider-Module."""


import asyncio
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


class _FakeOpenAIJsonResponse:
    text = "serialized text"

    def model_dump_json(self, *, indent=None):
        assert indent == 2
        return '{\n  "text": "serialized text"\n}'


def test_openai_provider_uses_json_api_format_for_gpt4o_text_output(
    monkeypatch, tmp_path
):
    from providers.openai import OpenAIProvider
    import providers.openai as openai_mod

    audio_file = tmp_path / "sample.wav"
    audio_file.write_bytes(b"audio")

    created_params: list[dict] = []

    def fake_create(**kwargs):
        created_params.append(kwargs)
        return SimpleNamespace(text="plain transcript")

    fake_client = SimpleNamespace(
        audio=SimpleNamespace(
            transcriptions=SimpleNamespace(create=fake_create)
        )
    )

    monkeypatch.setattr(openai_mod, "_get_client", lambda: fake_client)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    provider = OpenAIProvider()
    result = provider.transcribe(audio_file, model="gpt-4o-transcribe")

    assert result == "plain transcript"
    assert created_params[0]["response_format"] == "json"


def test_openai_provider_serializes_json_response_objects(monkeypatch, tmp_path):
    from providers.openai import OpenAIProvider
    import providers.openai as openai_mod

    audio_file = tmp_path / "sample.wav"
    audio_file.write_bytes(b"audio")

    created_params: list[dict] = []

    def fake_create(**kwargs):
        created_params.append(kwargs)
        return _FakeOpenAIJsonResponse()

    fake_client = SimpleNamespace(
        audio=SimpleNamespace(
            transcriptions=SimpleNamespace(create=fake_create)
        )
    )

    monkeypatch.setattr(openai_mod, "_get_client", lambda: fake_client)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    provider = OpenAIProvider()
    result = provider.transcribe(
        audio_file,
        model="gpt-4o-transcribe",
        response_format="json",
    )

    assert result == '{\n  "text": "serialized text"\n}'
    assert created_params[0]["response_format"] == "json"


def test_openai_provider_rejects_srt_for_gpt4o(tmp_path, monkeypatch):
    from providers.openai import OpenAIProvider

    audio_file = tmp_path / "sample.wav"
    audio_file.write_bytes(b"audio")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    provider = OpenAIProvider()

    with pytest.raises(ValueError, match="whisper-1"):
        provider.transcribe(
            audio_file,
            model="gpt-4o-transcribe",
            response_format="srt",
        )


def test_openai_provider_omits_auto_language(monkeypatch, tmp_path):
    from providers.openai import OpenAIProvider
    import providers.openai as openai_mod

    audio_file = tmp_path / "sample.wav"
    audio_file.write_bytes(b"audio")

    created_params: list[dict] = []

    def fake_create(**kwargs):
        created_params.append(kwargs)
        return SimpleNamespace(text="plain transcript")

    fake_client = SimpleNamespace(
        audio=SimpleNamespace(
            transcriptions=SimpleNamespace(create=fake_create)
        )
    )

    monkeypatch.setattr(openai_mod, "_get_client", lambda: fake_client)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    provider = OpenAIProvider()
    provider.transcribe(audio_file, language="auto")

    assert "language" not in created_params[0]


def test_groq_provider_omits_auto_language(monkeypatch, tmp_path):
    from providers.groq import GroqProvider
    import providers.groq as groq_mod

    audio_file = tmp_path / "sample.wav"
    audio_file.write_bytes(b"audio")

    created_params: list[dict] = []

    def fake_create(**kwargs):
        created_params.append(kwargs)
        return "plain transcript"

    fake_client = SimpleNamespace(
        audio=SimpleNamespace(
            transcriptions=SimpleNamespace(create=fake_create)
        )
    )

    monkeypatch.setattr(groq_mod, "_get_client", lambda: fake_client)
    monkeypatch.setenv("GROQ_API_KEY", "test-key")

    provider = GroqProvider()
    provider.transcribe(audio_file, language=" auto ")

    assert "language" not in created_params[0]


def test_deepgram_provider_omits_auto_language(monkeypatch, tmp_path):
    from providers.deepgram import DeepgramProvider
    import providers.deepgram as deepgram_mod

    audio_file = tmp_path / "sample.wav"
    audio_file.write_bytes(b"audio")

    created_params: list[dict] = []

    def fake_transcribe_file(**kwargs):
        created_params.append(kwargs)
        return SimpleNamespace(
            results=SimpleNamespace(
                channels=[SimpleNamespace(alternatives=[SimpleNamespace(transcript="hi")])]
            )
        )

    fake_client = SimpleNamespace(
        listen=SimpleNamespace(
            v1=SimpleNamespace(media=SimpleNamespace(transcribe_file=fake_transcribe_file))
        )
    )

    monkeypatch.setattr(deepgram_mod, "_get_client", lambda: fake_client)
    monkeypatch.setenv("DEEPGRAM_API_KEY", "test-key")

    provider = DeepgramProvider()
    provider.transcribe(audio_file, language="auto")

    assert "language" not in created_params[0]


def test_deepgram_provider_streams_audio_request(monkeypatch, tmp_path):
    from providers.deepgram import DeepgramProvider
    import providers.deepgram as deepgram_mod

    audio_payload = b"0123456789" * 2048
    audio_file = tmp_path / "sample.wav"
    audio_file.write_bytes(audio_payload)

    observed: dict[str, object] = {}

    def fake_transcribe_file(**kwargs):
        request = kwargs["request"]
        observed["is_bytes"] = isinstance(request, (bytes, bytearray))
        observed["chunks"] = list(request)
        return SimpleNamespace(
            results=SimpleNamespace(
                channels=[SimpleNamespace(alternatives=[SimpleNamespace(transcript="hi")])]
            )
        )

    fake_client = SimpleNamespace(
        listen=SimpleNamespace(
            v1=SimpleNamespace(media=SimpleNamespace(transcribe_file=fake_transcribe_file))
        )
    )

    monkeypatch.setattr(deepgram_mod, "_get_client", lambda: fake_client)
    monkeypatch.setattr(deepgram_mod, "load_vocabulary", lambda: {"keywords": []})
    monkeypatch.setenv("DEEPGRAM_API_KEY", "test-key")

    provider = DeepgramProvider()
    result = provider.transcribe(audio_file)

    assert result == "hi"
    assert observed["is_bytes"] is False
    assert b"".join(observed["chunks"]) == audio_payload


def test_deepgram_stream_connection_omits_auto_language(monkeypatch):
    import providers.deepgram_stream as deepgram_stream_mod

    captured: dict[str, object] = {}

    class _FakeProtocol:
        pass

    class _FakeClient:
        def __init__(self, *, websocket):
            self.websocket = websocket

    class _FakeConnect:
        async def __aenter__(self):
            return _FakeProtocol()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    def fake_connect(url, *, extra_headers, close_timeout):
        captured["url"] = url
        captured["headers"] = extra_headers
        captured["close_timeout"] = close_timeout
        return _FakeConnect()

    monkeypatch.setitem(
        sys.modules,
        "deepgram.listen.v1.socket_client",
        SimpleNamespace(AsyncV1SocketClient=_FakeClient),
    )
    monkeypatch.setitem(
        sys.modules,
        "websockets.legacy.client",
        SimpleNamespace(connect=fake_connect),
    )

    async def _run() -> None:
        async with deepgram_stream_mod._create_deepgram_connection(
            "test-key",
            model="nova-3",
            language=" auto ",
        ) as client:
            assert isinstance(client, _FakeClient)
            assert isinstance(client.websocket, _FakeProtocol)

    asyncio.run(_run())

    assert "language=" not in str(captured["url"])
