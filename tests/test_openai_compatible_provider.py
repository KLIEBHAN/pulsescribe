"""Tests für den Provider eigener Server mit OpenAI-kompatibler API."""

from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import ModuleType, SimpleNamespace

import pytest

import providers.openai_compatible as compat
from providers.openai_compatible import OpenAICompatibleProvider


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in ("OPENAI_COMPATIBLE_BASE_URL", "OPENAI_COMPATIBLE_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    compat._reset_client()
    yield
    compat._reset_client()


@pytest.fixture
def audio_file(tmp_path):
    path = tmp_path / "audio.wav"
    path.write_bytes(b"RIFF fake wav")
    return path


@pytest.fixture
def fake_openai(monkeypatch):
    """Replace the openai module; record client construction and requests."""
    created: list[dict] = []
    requests: list[dict] = []

    class FakeOpenAI:
        def __init__(self, **kwargs):
            created.append(kwargs)

            def create(**params):
                requests.append({**params, "file_bytes": params["file"].read()})
                return SimpleNamespace(text="Hallo vom Server")

            self.audio = SimpleNamespace(transcriptions=SimpleNamespace(create=create))

    module = ModuleType("openai")
    module.OpenAI = FakeOpenAI
    monkeypatch.setitem(sys.modules, "openai", module)
    return SimpleNamespace(created=created, requests=requests)


def test_missing_base_url_fails_before_reading_audio(tmp_path, fake_openai):
    with pytest.raises(ValueError, match="OPENAI_COMPATIBLE_BASE_URL"):
        OpenAICompatibleProvider().transcribe(tmp_path / "missing.wav")
    assert fake_openai.created == []


def test_uploads_audio_with_defaults_and_never_uses_openai_key(
    monkeypatch, audio_file, fake_openai
):
    monkeypatch.setenv("OPENAI_COMPATIBLE_BASE_URL", "http://mac-mini:7860/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-real-openai-key")

    text = OpenAICompatibleProvider().transcribe(audio_file, language="auto")

    assert text == "Hallo vom Server"
    assert fake_openai.created == [
        {"base_url": "http://mac-mini:7860/v1", "api_key": "not-needed"}
    ]
    (request,) = fake_openai.requests
    assert request["model"] == "whisper-1"
    assert request["response_format"] == "json"
    assert "language" not in request
    assert request["file_bytes"] == b"RIFF fake wav"


def test_explicit_model_language_and_api_key(monkeypatch, audio_file, fake_openai):
    monkeypatch.setenv("OPENAI_COMPATIBLE_BASE_URL", " http://host/v1 ")
    monkeypatch.setenv("OPENAI_COMPATIBLE_API_KEY", "secret")

    OpenAICompatibleProvider().transcribe(audio_file, model="large-v3", language="de")

    assert fake_openai.created == [{"base_url": "http://host/v1", "api_key": "secret"}]
    assert fake_openai.requests[0]["model"] == "large-v3"
    assert fake_openai.requests[0]["language"] == "de"


def test_client_is_reused_and_recreated_when_settings_change(
    monkeypatch, audio_file, fake_openai
):
    provider = OpenAICompatibleProvider()
    monkeypatch.setenv("OPENAI_COMPATIBLE_BASE_URL", "http://a/v1")
    provider.transcribe(audio_file)
    provider.transcribe(audio_file)
    assert len(fake_openai.created) == 1

    monkeypatch.setenv("OPENAI_COMPATIBLE_BASE_URL", "http://b/v1")
    provider.transcribe(audio_file)
    monkeypatch.setenv("OPENAI_COMPATIBLE_API_KEY", "new-key")
    provider.transcribe(audio_file)
    assert [c["base_url"] for c in fake_openai.created] == ["http://a/v1", "http://b/v1", "http://b/v1"]
    assert fake_openai.created[-1]["api_key"] == "new-key"


def test_unexpected_response_raises_type_error(monkeypatch, audio_file):
    monkeypatch.setenv("OPENAI_COMPATIBLE_BASE_URL", "http://a/v1")
    client = SimpleNamespace(
        audio=SimpleNamespace(transcriptions=SimpleNamespace(create=lambda **_: 42))
    )
    monkeypatch.setattr(compat, "_get_client", lambda: client)
    with pytest.raises(TypeError, match="OpenAI-kompatibel"):
        OpenAICompatibleProvider().transcribe(audio_file)


def test_real_sdk_posts_multipart_to_audio_transcriptions(monkeypatch, audio_file):
    """The installed OpenAI SDK reaches {base_url}/audio/transcriptions with multipart."""
    pytest.importorskip("openai")
    seen: dict[str, str] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802 - http.server API
            body = self.rfile.read(int(self.headers["Content-Length"]))
            seen.update(
                path=self.path,
                content_type=self.headers["Content-Type"],
                authorization=self.headers.get("Authorization", ""),
                body=body.decode("latin-1"),
            )
            payload = json.dumps({"text": "Echt übertragen"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        monkeypatch.setenv(
            "OPENAI_COMPATIBLE_BASE_URL", f"http://127.0.0.1:{server.server_port}/v1"
        )
        text = OpenAICompatibleProvider().transcribe(audio_file, language="de")
    finally:
        server.shutdown()
        server.server_close()

    assert text == "Echt übertragen"
    assert seen["path"] == "/v1/audio/transcriptions"
    assert seen["content_type"].startswith("multipart/form-data")
    assert seen["authorization"] == "Bearer not-needed"
    assert 'name="model"' in seen["body"] and "whisper-1" in seen["body"]
    assert 'name="language"' in seen["body"]
    assert "RIFF fake wav" in seen["body"]
