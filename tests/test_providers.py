"""Tests für Provider-Module."""


import asyncio
import builtins
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest


class TestProviderFactory:
    """Tests für Provider-Factory."""

    @pytest.mark.parametrize(
        "mode",
        ["openai", "deepgram", "deepgram_stream", "groq", "local"],
        ids=["openai", "deepgram", "deepgram_stream", "groq", "local"],
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
        assert "deepgram_stream" in DEFAULT_MODELS
        assert "groq" in DEFAULT_MODELS
        assert "local" in DEFAULT_MODELS

    def test_get_default_model_unknown_falls_back_to_whisper_1(self):
        """Unbekannte Provider sollen denselben sicheren Fallback behalten."""
        from providers import get_default_model

        assert get_default_model("unknown") == "whisper-1"


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

    def test_deepgram_stream_supports_streaming(self):
        """Deepgram WebSocket Provider unterstützt Streaming."""
        from providers import get_provider
        provider = get_provider("deepgram_stream")
        assert provider.supports_streaming() is True

    def test_local_supports_streaming(self):
        """Lokales Whisper unterstützt kein Streaming."""
        from providers import get_provider
        provider = get_provider("local")
        assert provider.supports_streaming() is False


def test_get_provider_local_reports_slim_build_import_error(monkeypatch):
    from providers import get_provider

    real_import = builtins.__import__

    def _patched_import(name, globals=None, locals=None, fromlist=(), level=0):
        if (
            name == "local"
            and level == 1
            and globals
            and globals.get("__package__") == "providers"
        ):
            raise ImportError("mock missing local backend")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", _patched_import)

    with pytest.raises(ValueError, match="Lokaler Modus nicht verfügbar") as exc_info:
        get_provider("local")

    assert "Original-Fehler: mock missing local backend" in str(exc_info.value)


def test_get_provider_non_local_import_error_bubbles_up(monkeypatch):
    from providers import get_provider

    real_import = builtins.__import__

    def _patched_import(name, globals=None, locals=None, fromlist=(), level=0):
        if (
            name == "openai"
            and level == 1
            and globals
            and globals.get("__package__") == "providers"
        ):
            raise ImportError("mock missing openai backend")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", _patched_import)

    with pytest.raises(ImportError, match="mock missing openai backend"):
        get_provider("openai")


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
def test_provider_client_skips_sdk_import_when_api_key_is_missing(
    monkeypatch,
    module_name: str,
    module_path: str,
    env_key: str,
    dependency_name: str,
    class_name: str,
):
    module = __import__(module_path, fromlist=["dummy"])
    module._client_cache.reset()

    class _ImportShouldNotRun:
        def __init__(self, api_key=None, **_kwargs):
            raise AssertionError("SDK client should not be constructed without API key")

    monkeypatch.setitem(
        sys.modules,
        dependency_name,
        SimpleNamespace(**{class_name: _ImportShouldNotRun}),
    )
    monkeypatch.delenv(env_key, raising=False)

    with pytest.raises(ValueError, match=env_key):
        module._get_client()


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
    module._client_cache.reset()

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


@pytest.mark.parametrize(
    ("module_name", "module_path", "env_key", "dependency_name", "class_name"),
    [
        ("openai", "providers.openai", "OPENAI_API_KEY", "openai", "OpenAI"),
        ("deepgram", "providers.deepgram", "DEEPGRAM_API_KEY", "deepgram", "DeepgramClient"),
        ("groq", "providers.groq", "GROQ_API_KEY", "groq", "Groq"),
    ],
    ids=["openai", "deepgram", "groq"],
)
def test_provider_client_recovers_after_api_key_is_removed(
    monkeypatch,
    module_name: str,
    module_path: str,
    env_key: str,
    dependency_name: str,
    class_name: str,
):
    module = __import__(module_path, fromlist=["dummy"])
    module._client_cache.reset()

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

    monkeypatch.delenv(env_key, raising=False)
    with pytest.raises(ValueError, match=env_key):
        module._get_client()

    monkeypatch.setenv(env_key, f"{module_name}-key-2")
    second = module._get_client()

    assert second is not first
    assert created_with == [f"{module_name}-key-1", f"{module_name}-key-2"]


class _FakeOpenAIJsonResponse:
    text = "serialized text"

    def model_dump_json(self, *, indent=None):
        assert indent == 2
        return '{\n  "text": "serialized text"\n}'


def _build_openai_test_client(observed: dict[str, object]) -> SimpleNamespace:
    def fake_create(**kwargs):
        observed.update(kwargs)
        return SimpleNamespace(text="plain transcript")

    return SimpleNamespace(
        audio=SimpleNamespace(
            transcriptions=SimpleNamespace(create=fake_create)
        )
    )


def _build_groq_test_client(observed: dict[str, object]) -> SimpleNamespace:
    def fake_create(**kwargs):
        observed.update(kwargs)
        return "plain transcript"

    return SimpleNamespace(
        audio=SimpleNamespace(
            transcriptions=SimpleNamespace(create=fake_create)
        )
    )


def _build_deepgram_test_client(observed: dict[str, object]) -> SimpleNamespace:
    def fake_transcribe_file(**kwargs):
        observed.update(kwargs)
        return SimpleNamespace(
            results=SimpleNamespace(
                channels=[SimpleNamespace(alternatives=[SimpleNamespace(transcript="plain transcript")])]
            )
        )

    return SimpleNamespace(
        listen=SimpleNamespace(
            v1=SimpleNamespace(media=SimpleNamespace(transcribe_file=fake_transcribe_file))
        )
    )


@pytest.mark.parametrize(
    ("module_path", "provider_class_name", "env_key", "client_builder"),
    [
        ("providers.openai", "OpenAIProvider", "OPENAI_API_KEY", _build_openai_test_client),
        ("providers.groq", "GroqProvider", "GROQ_API_KEY", _build_groq_test_client),
        (
            "providers.deepgram",
            "DeepgramProvider",
            "DEEPGRAM_API_KEY",
            _build_deepgram_test_client,
        ),
    ],
    ids=["openai", "groq", "deepgram"],
)
def test_cloud_providers_use_default_model_when_none(
    monkeypatch,
    tmp_path,
    module_path: str,
    provider_class_name: str,
    env_key: str,
    client_builder,
):
    module = __import__(module_path, fromlist=[provider_class_name])
    provider_class = getattr(module, provider_class_name)

    audio_file = tmp_path / "sample.wav"
    audio_file.write_bytes(b"audio")

    observed: dict[str, object] = {}
    monkeypatch.setattr(module, "_get_client", lambda: client_builder(observed))
    if module_path == "providers.deepgram":
        monkeypatch.setattr(module, "load_vocabulary", lambda: {"keywords": []})
    monkeypatch.setenv(env_key, "test-key")

    provider = provider_class()
    assert provider.transcribe(audio_file) == "plain transcript"
    assert observed["model"] == provider.default_model


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


def test_openai_provider_text_output_falls_back_to_model_dump_json(
    monkeypatch, tmp_path
):
    from providers.openai import OpenAIProvider
    import providers.openai as openai_mod

    audio_file = tmp_path / "sample.wav"
    audio_file.write_bytes(b"audio")

    class _JsonOnlyResponse:
        def model_dump_json(self, *, indent=None):
            assert indent == 2
            return '{\n  "text": "serialized text"\n}'

    fake_client = SimpleNamespace(
        audio=SimpleNamespace(
            transcriptions=SimpleNamespace(create=lambda **_kwargs: _JsonOnlyResponse())
        )
    )

    monkeypatch.setattr(openai_mod, "_get_client", lambda: fake_client)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    provider = OpenAIProvider()
    assert (
        provider.transcribe(
            audio_file,
            model="gpt-4o-transcribe",
            response_format="text",
        )
        == '{\n  "text": "serialized text"\n}'
    )


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


def test_openai_provider_streams_file_handle_to_sdk(monkeypatch, tmp_path):
    from providers.openai import OpenAIProvider
    import providers.openai as openai_mod

    audio_file = tmp_path / "sample.wav"
    audio_file.write_bytes(b"audio")

    observed: dict[str, object] = {}

    def fake_create(**kwargs):
        sdk_file = kwargs["file"]
        observed["is_bytes"] = isinstance(sdk_file, (bytes, bytearray))
        observed["name"] = Path(sdk_file.name).name
        observed["payload"] = sdk_file.read()
        observed["language"] = kwargs.get("language")
        observed["response_format"] = kwargs["response_format"]
        return SimpleNamespace(text="plain transcript")

    fake_client = SimpleNamespace(
        audio=SimpleNamespace(
            transcriptions=SimpleNamespace(create=fake_create)
        )
    )

    monkeypatch.setattr(openai_mod, "_get_client", lambda: fake_client)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    provider = OpenAIProvider()
    assert provider.transcribe(audio_file, language="de") == "plain transcript"

    assert observed == {
        "is_bytes": False,
        "name": "sample.wav",
        "payload": b"audio",
        "language": "de",
        # gpt-4o-transcribe ist JSON-only; text wird intern als json angefragt.
        "response_format": "json",
    }



def test_openai_provider_closes_file_handle_after_sdk_call(monkeypatch, tmp_path):
    from providers.openai import OpenAIProvider
    import providers.openai as openai_mod

    audio_file = tmp_path / "sample.wav"
    audio_file.write_bytes(b"audio")

    observed: dict[str, object] = {}

    def fake_create(**kwargs):
        sdk_file = kwargs["file"]
        observed["file"] = sdk_file
        observed["closed_during_call"] = sdk_file.closed
        return SimpleNamespace(text="plain transcript")

    fake_client = SimpleNamespace(
        audio=SimpleNamespace(
            transcriptions=SimpleNamespace(create=fake_create)
        )
    )

    monkeypatch.setattr(openai_mod, "_get_client", lambda: fake_client)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    provider = OpenAIProvider()
    assert provider.transcribe(audio_file) == "plain transcript"

    assert observed["closed_during_call"] is False
    assert observed["file"].closed is True



def test_openai_response_format_helpers_normalize_case_and_whitespace() -> None:
    from providers._response_utils import serialize_openai_response
    from providers.openai import _resolve_api_response_format

    assert _resolve_api_response_format("gpt-4o-transcribe", " JSON ") == "json"
    assert _resolve_api_response_format("whisper-1", " VTT ") == "vtt"
    assert (
        serialize_openai_response(_FakeOpenAIJsonResponse(), requested_format=" JSON ")
        == '{\n  "text": "serialized text"\n}'
    )



def test_openai_provider_redacts_debug_result_logging(monkeypatch, tmp_path):
    from providers.openai import OpenAIProvider
    import providers.openai as openai_mod

    audio_file = tmp_path / "sample.wav"
    audio_file.write_bytes(b"audio")
    captured: list[tuple[tuple, dict]] = []

    fake_client = SimpleNamespace(
        audio=SimpleNamespace(
            transcriptions=SimpleNamespace(
                create=lambda **_kwargs: SimpleNamespace(text="secret transcript")
            )
        )
    )

    monkeypatch.setattr(openai_mod, "_get_client", lambda: fake_client)
    monkeypatch.setattr(
        openai_mod.logger,
        "debug",
        lambda *args, **kwargs: captured.append((args, kwargs)),
    )
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    provider = OpenAIProvider()
    assert provider.transcribe(audio_file) == "secret transcript"

    assert captured
    assert "secret transcript" not in repr(captured[-1][0])
    assert "<redacted" in repr(captured[-1][0])


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


def test_groq_provider_passes_named_file_tuple_and_stable_defaults(
    monkeypatch, tmp_path
):
    from providers.groq import GroqProvider
    import providers.groq as groq_mod

    audio_file = tmp_path / "sample.wav"
    audio_file.write_bytes(b"audio")

    observed: dict[str, object] = {}

    def fake_create(**kwargs):
        filename, sdk_file = kwargs["file"]
        observed["filename"] = filename
        observed["payload"] = sdk_file.read()
        observed["language"] = kwargs.get("language")
        observed["response_format"] = kwargs["response_format"]
        observed["temperature"] = kwargs["temperature"]
        return "plain transcript"

    fake_client = SimpleNamespace(
        audio=SimpleNamespace(
            transcriptions=SimpleNamespace(create=fake_create)
        )
    )

    monkeypatch.setattr(groq_mod, "_get_client", lambda: fake_client)
    monkeypatch.setenv("GROQ_API_KEY", "test-key")

    provider = GroqProvider()
    assert provider.transcribe(audio_file, language="de") == "plain transcript"

    assert observed == {
        "filename": "sample.wav",
        "payload": b"audio",
        "language": "de",
        "response_format": "text",
        "temperature": 0.0,
    }



def test_groq_provider_closes_file_handle_after_sdk_call(monkeypatch, tmp_path):
    from providers.groq import GroqProvider
    import providers.groq as groq_mod

    audio_file = tmp_path / "sample.wav"
    audio_file.write_bytes(b"audio")

    observed: dict[str, object] = {}

    def fake_create(**kwargs):
        _filename, sdk_file = kwargs["file"]
        observed["file"] = sdk_file
        observed["closed_during_call"] = sdk_file.closed
        return "plain transcript"

    fake_client = SimpleNamespace(
        audio=SimpleNamespace(
            transcriptions=SimpleNamespace(create=fake_create)
        )
    )

    monkeypatch.setattr(groq_mod, "_get_client", lambda: fake_client)
    monkeypatch.setenv("GROQ_API_KEY", "test-key")

    provider = GroqProvider()
    assert provider.transcribe(audio_file) == "plain transcript"

    assert observed["closed_during_call"] is False
    assert observed["file"].closed is True



def test_groq_provider_accepts_response_objects_with_text_attribute(
    monkeypatch, tmp_path
):
    from providers.groq import GroqProvider
    import providers.groq as groq_mod

    audio_file = tmp_path / "sample.wav"
    audio_file.write_bytes(b"audio")

    fake_client = SimpleNamespace(
        audio=SimpleNamespace(
            transcriptions=SimpleNamespace(
                create=lambda **_kwargs: SimpleNamespace(text="plain transcript")
            )
        )
    )

    monkeypatch.setattr(groq_mod, "_get_client", lambda: fake_client)
    monkeypatch.setenv("GROQ_API_KEY", "test-key")

    provider = GroqProvider()
    assert provider.transcribe(audio_file) == "plain transcript"


def test_groq_provider_rejects_unexpected_response_objects(monkeypatch, tmp_path):
    from providers.groq import GroqProvider
    import providers.groq as groq_mod

    audio_file = tmp_path / "sample.wav"
    audio_file.write_bytes(b"audio")

    fake_client = SimpleNamespace(
        audio=SimpleNamespace(
            transcriptions=SimpleNamespace(create=lambda **_kwargs: object())
        )
    )

    monkeypatch.setattr(groq_mod, "_get_client", lambda: fake_client)
    monkeypatch.setenv("GROQ_API_KEY", "test-key")

    provider = GroqProvider()
    with pytest.raises(TypeError, match="Unerwarteter Groq-Response-Typ"):
        provider.transcribe(audio_file)


def test_groq_provider_redacts_debug_result_logging(monkeypatch, tmp_path):
    from providers.groq import GroqProvider
    import providers.groq as groq_mod

    audio_file = tmp_path / "sample.wav"
    audio_file.write_bytes(b"audio")
    captured: list[tuple[tuple, dict]] = []

    fake_client = SimpleNamespace(
        audio=SimpleNamespace(
            transcriptions=SimpleNamespace(
                create=lambda **_kwargs: "secret transcript"
            )
        )
    )

    monkeypatch.setattr(groq_mod, "_get_client", lambda: fake_client)
    monkeypatch.setattr(
        groq_mod.logger,
        "debug",
        lambda *args, **kwargs: captured.append((args, kwargs)),
    )
    monkeypatch.setenv("GROQ_API_KEY", "test-key")

    provider = GroqProvider()
    assert provider.transcribe(audio_file) == "secret transcript"

    assert captured
    assert "secret transcript" not in repr(captured[-1][0])
    assert "<redacted" in repr(captured[-1][0])


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


def test_deepgram_provider_keeps_rest_defaults_and_explicit_language(
    monkeypatch, tmp_path
):
    from providers.deepgram import DeepgramProvider
    import providers.deepgram as deepgram_mod

    audio_file = tmp_path / "sample.wav"
    audio_file.write_bytes(b"audio")

    observed: dict[str, object] = {}

    def fake_transcribe_file(**kwargs):
        observed.update(kwargs)
        observed["payload"] = b"".join(kwargs["request"])
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
    monkeypatch.setattr(deepgram_mod, "load_vocabulary", lambda: {"keywords": ["API"]})
    monkeypatch.setenv("DEEPGRAM_API_KEY", "test-key")

    provider = DeepgramProvider()
    assert provider.transcribe(audio_file, model="nova-3", language="de") == "hi"

    assert observed["model"] == "nova-3"
    assert observed["language"] == "de"
    assert observed["smart_format"] is True
    assert observed["punctuate"] is True
    assert observed["keyterm"] == ["API"]
    assert observed["payload"] == b"audio"



def test_deepgram_provider_redacts_debug_result_logging(monkeypatch, tmp_path):
    from providers.deepgram import DeepgramProvider
    import providers.deepgram as deepgram_mod

    audio_file = tmp_path / "sample.wav"
    audio_file.write_bytes(b"audio")
    captured: list[tuple[tuple, dict]] = []

    fake_client = SimpleNamespace(
        listen=SimpleNamespace(
            v1=SimpleNamespace(
                media=SimpleNamespace(
                    transcribe_file=lambda **_kwargs: SimpleNamespace(
                        results=SimpleNamespace(
                            channels=[
                                SimpleNamespace(
                                    alternatives=[
                                        SimpleNamespace(transcript="secret transcript")
                                    ]
                                )
                            ]
                        )
                    )
                )
            )
        )
    )

    monkeypatch.setattr(deepgram_mod, "_get_client", lambda: fake_client)
    monkeypatch.setattr(deepgram_mod, "load_vocabulary", lambda: {"keywords": []})
    monkeypatch.setattr(
        deepgram_mod.logger,
        "debug",
        lambda *args, **kwargs: captured.append((args, kwargs)),
    )
    monkeypatch.setenv("DEEPGRAM_API_KEY", "test-key")

    provider = DeepgramProvider()
    assert provider.transcribe(audio_file) == "secret transcript"

    assert captured
    assert "secret transcript" not in repr(captured[-1][0])
    assert "<redacted" in repr(captured[-1][0])


@pytest.mark.parametrize(
    ("model", "expected_field"),
    [("nova-3", "keyterm"), ("nova-2", "keywords")],
    ids=["nova-3-keyterm", "legacy-keywords"],
)
def test_deepgram_provider_maps_vocabulary_to_model_specific_request_fields(
    monkeypatch, tmp_path, model: str, expected_field: str
):
    from providers.deepgram import DeepgramProvider
    import providers.deepgram as deepgram_mod

    audio_file = tmp_path / "sample.wav"
    audio_file.write_bytes(b"audio")

    observed: dict[str, object] = {}

    def fake_transcribe_file(**kwargs):
        observed.update(kwargs)
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
    monkeypatch.setattr(
        deepgram_mod,
        "load_vocabulary",
        lambda: {"keywords": [f"kw{i}" for i in range(120)]},
    )
    monkeypatch.setenv("DEEPGRAM_API_KEY", "test-key")

    provider = DeepgramProvider()
    provider.transcribe(audio_file, model=model)

    assert expected_field in observed
    assert len(observed[expected_field]) == 100
    assert ("keywords" if expected_field == "keyterm" else "keyterm") not in observed


def test_deepgram_provider_returns_empty_string_when_transcript_is_missing(
    monkeypatch, tmp_path
):
    from providers.deepgram import DeepgramProvider
    import providers.deepgram as deepgram_mod

    audio_file = tmp_path / "sample.wav"
    audio_file.write_bytes(b"audio")

    fake_client = SimpleNamespace(
        listen=SimpleNamespace(
            v1=SimpleNamespace(
                media=SimpleNamespace(
                    transcribe_file=lambda **_kwargs: SimpleNamespace(
                        results=SimpleNamespace(channels=[])
                    )
                )
            )
        )
    )

    monkeypatch.setattr(deepgram_mod, "_get_client", lambda: fake_client)
    monkeypatch.setattr(deepgram_mod, "load_vocabulary", lambda: {"keywords": []})
    monkeypatch.setenv("DEEPGRAM_API_KEY", "test-key")

    provider = DeepgramProvider()
    assert provider.transcribe(audio_file) == ""


def test_deepgram_provider_returns_empty_string_when_alternatives_are_missing(
    monkeypatch, tmp_path
):
    from providers.deepgram import DeepgramProvider
    import providers.deepgram as deepgram_mod

    audio_file = tmp_path / "sample.wav"
    audio_file.write_bytes(b"audio")

    fake_client = SimpleNamespace(
        listen=SimpleNamespace(
            v1=SimpleNamespace(
                media=SimpleNamespace(
                    transcribe_file=lambda **_kwargs: SimpleNamespace(
                        results=SimpleNamespace(
                            channels=[SimpleNamespace(alternatives=[])]
                        )
                    )
                )
            )
        )
    )

    monkeypatch.setattr(deepgram_mod, "_get_client", lambda: fake_client)
    monkeypatch.setattr(deepgram_mod, "load_vocabulary", lambda: {"keywords": []})
    monkeypatch.setenv("DEEPGRAM_API_KEY", "test-key")

    provider = DeepgramProvider()
    assert provider.transcribe(audio_file) == ""


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
