from __future__ import annotations

import logging
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
from typing import cast

import pytest

import providers._client_cache as client_cache_mod
from providers._client_cache import EnvClientCache, build_cached_env_client_getter
from providers._language import is_auto_language, normalize_auto_language
from providers._response_utils import (
    log_transcription_result,
    require_text_response,
    serialize_openai_response,
)
from providers._transcription_request import (
    build_transcription_params,
    execute_audio_file_request,
    execute_audio_transcription_request,
    resolve_transcription_request,
)


class _FakeJsonResponse:
    text = "serialized text"

    def model_dump_json(self, *, indent: int = 2) -> str:
        assert indent == 2
        return '{\n  "text": "serialized text"\n}'


def _test_logger() -> logging.Logger:
    return logging.getLogger("tests.provider_helpers")


def test_is_auto_language_recognizes_empty_and_auto_like_values() -> None:
    assert is_auto_language(None) is True
    assert is_auto_language("") is True
    assert is_auto_language(" auto ") is True
    assert is_auto_language("de") is False


def test_normalize_auto_language_returns_none_for_auto_like_values() -> None:
    assert normalize_auto_language(None) is None
    assert normalize_auto_language("") is None
    assert normalize_auto_language(" auto ") is None


def test_normalize_auto_language_preserves_explicit_language_codes() -> None:
    assert normalize_auto_language(" de ") == "de"
    assert normalize_auto_language("pt-BR") == "pt-BR"


def test_resolve_transcription_request_normalizes_language_and_uses_default_model(
    tmp_path,
) -> None:
    audio_file = tmp_path / "sample.wav"
    audio_file.write_bytes(b"a" * 2500)

    request = resolve_transcription_request(
        audio_file,
        model=None,
        default_model="whisper-1",
        language=" auto ",
    )

    assert request.model == "whisper-1"
    assert request.language is None
    assert request.audio_kb == 2


def test_build_transcription_params_merges_extra_params_and_omits_empty_language() -> None:
    assert build_transcription_params(
        model="whisper-1",
        language=None,
        extra_params={"response_format": "json", "temperature": 0.0},
    ) == {
        "model": "whisper-1",
        "response_format": "json",
        "temperature": 0.0,
    }


def test_build_transcription_params_accepts_read_only_mappings() -> None:
    assert build_transcription_params(
        model="whisper-1",
        language="de",
        extra_params=MappingProxyType(
            {"response_format": "json", "temperature": 0.0}
        ),
    ) == {
        "model": "whisper-1",
        "response_format": "json",
        "temperature": 0.0,
        "language": "de",
    }


def test_execute_audio_file_request_closes_file_after_callback(tmp_path) -> None:
    audio_file = tmp_path / "sample.wav"
    audio_file.write_bytes(b"audio")
    observed: dict[str, object] = {}

    def _fake_request(**kwargs):
        sdk_file = kwargs["file"]
        observed["file"] = sdk_file
        observed["closed_during_call"] = sdk_file.closed
        observed["payload"] = sdk_file.read()
        return "ok"

    result = execute_audio_file_request(
        audio_file,
        request_callable=_fake_request,
        build_params=lambda sdk_file: {"file": sdk_file, "model": "whisper-1"},
    )

    assert result == "ok"
    assert observed["closed_during_call"] is False
    assert observed["payload"] == b"audio"
    assert observed["file"].closed is True


def test_execute_audio_file_request_accepts_read_only_param_mappings(tmp_path) -> None:
    audio_file = tmp_path / "sample.wav"
    audio_file.write_bytes(b"audio")

    def _fake_request(**kwargs):
        assert kwargs["model"] == "whisper-1"
        return kwargs["file"].read()

    result = execute_audio_file_request(
        audio_file,
        request_callable=_fake_request,
        build_params=lambda sdk_file: MappingProxyType(
            {"file": sdk_file, "model": "whisper-1"}
        ),
    )

    assert result == b"audio"


def test_execute_audio_transcription_request_supports_custom_file_payload(
    tmp_path,
) -> None:
    audio_file = tmp_path / "sample.wav"
    audio_file.write_bytes(b"audio")
    observed: dict[str, object] = {}

    def _fake_request(**kwargs):
        observed.update(kwargs)
        return "ok"

    result = execute_audio_transcription_request(
        audio_file,
        request_callable=_fake_request,
        model="whisper-1",
        language="de",
        extra_params={"response_format": "text"},
        build_file_payload=lambda path, sdk_file: (path.name, sdk_file),
    )

    assert result == "ok"
    assert observed["model"] == "whisper-1"
    assert observed["language"] == "de"
    assert observed["response_format"] == "text"
    file_payload = cast(tuple[str, object], observed["file"])
    assert file_payload[0] == "sample.wav"
    assert getattr(file_payload[1], "closed") is True


def test_execute_audio_transcription_request_accepts_read_only_extra_params(
    tmp_path,
) -> None:
    audio_file = tmp_path / "sample.wav"
    audio_file.write_bytes(b"audio")
    observed: dict[str, object] = {}

    def _fake_request(**kwargs):
        observed.update(kwargs)
        return "ok"

    result = execute_audio_transcription_request(
        audio_file,
        request_callable=_fake_request,
        model="whisper-1",
        language=None,
        extra_params=MappingProxyType({"temperature": 0.0}),
    )

    assert result == "ok"
    assert observed["temperature"] == 0.0
    assert observed["file"].closed is True


def test_execute_audio_transcription_request_uses_raw_file_payload_by_default(
    tmp_path,
) -> None:
    audio_file = tmp_path / "sample.wav"
    audio_file.write_bytes(b"audio")
    observed: dict[str, object] = {}

    def _fake_request(**kwargs):
        sdk_file = kwargs["file"]
        observed["language_present"] = "language" in kwargs
        observed["name"] = Path(sdk_file.name).name
        observed["payload"] = sdk_file.read()
        observed["closed_during_call"] = sdk_file.closed
        observed["temperature"] = kwargs["temperature"]
        return "ok"

    result = execute_audio_transcription_request(
        audio_file,
        request_callable=_fake_request,
        model="whisper-1",
        language=None,
        extra_params={"temperature": 0.0},
    )

    assert result == "ok"
    assert observed == {
        "language_present": False,
        "name": "sample.wav",
        "payload": b"audio",
        "closed_during_call": False,
        "temperature": 0.0,
    }


def test_serialize_openai_response_prefers_json_dump_for_non_text_formats() -> None:
    assert (
        serialize_openai_response(_FakeJsonResponse(), requested_format=" JSON ")
        == '{\n  "text": "serialized text"\n}'
    )


def test_serialize_openai_response_falls_back_to_text_or_string() -> None:
    assert (
        serialize_openai_response(
            SimpleNamespace(text="plain"), requested_format="text"
        )
        == "plain"
    )
    assert serialize_openai_response(object(), requested_format="text").startswith(
        "<object object at"
    )



def test_serialize_openai_response_supports_mapping_payloads() -> None:
    response = {
        "text": "mapped transcript",
        "segments": [
            {"id": 1, "text": "mapped transcript"},
        ],
    }

    assert serialize_openai_response(response, requested_format="text") == "mapped transcript"
    assert (
        serialize_openai_response(response, requested_format="json")
        == '{\n  "text": "mapped transcript",\n  "segments": [\n    {\n      "id": 1,\n      "text": "mapped transcript"\n    }\n  ]\n}'
    )



def test_require_text_response_accepts_text_shapes_and_rejects_other_payloads() -> None:
    assert (
        require_text_response("plain transcript", provider_name="Groq")
        == "plain transcript"
    )
    assert (
        require_text_response(
            {"text": "mapped transcript"}, provider_name="Groq"
        )
        == "mapped transcript"
    )
    assert (
        require_text_response(
            SimpleNamespace(text="structured transcript"), provider_name="Groq"
        )
        == "structured transcript"
    )

    with pytest.raises(TypeError, match="Unerwarteter Groq-Response-Typ"):
        require_text_response(
            SimpleNamespace(result="missing text"), provider_name="Groq"
        )


def test_log_transcription_result_redacts_transcript_text(monkeypatch) -> None:
    captured: list[tuple[tuple, dict]] = []
    logger = _test_logger()
    monkeypatch.setattr(
        logger,
        "debug",
        lambda *args, **kwargs: captured.append((args, kwargs)),
    )

    log_transcription_result(logger, "highly sensitive transcript")

    assert captured
    assert "highly sensitive transcript" not in repr(captured[-1][0])
    assert "<redacted" in repr(captured[-1][0])


def test_build_cached_env_client_getter_keeps_dependency_import_lazy(
    monkeypatch,
) -> None:
    cache = EnvClientCache()
    getter = build_cached_env_client_getter(
        cache=cache,
        env_var="TEST_API_KEY",
        missing_error="TEST_API_KEY missing",
        dependency_module="fake_sdk",
        dependency_class="FakeClient",
        logger=_test_logger(),
        client_label="Fake client",
    )

    monkeypatch.delenv("TEST_API_KEY", raising=False)
    monkeypatch.setattr(
        client_cache_mod,
        "import_module",
        lambda _name: (_ for _ in ()).throw(
            AssertionError("dependency import should stay lazy without API key")
        ),
    )

    with pytest.raises(ValueError, match="TEST_API_KEY missing"):
        getter()
