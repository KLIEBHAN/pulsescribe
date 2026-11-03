"""Tests for loading the own server's model list for the Settings picker."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from ui.server_models import ServerModelLoader


def _collect():
    results: list[tuple[list[str], Exception | None]] = []
    done = threading.Event()

    def deliver(models, error, _generation):
        results.append((models, error))
        done.set()

    return results, done, deliver


def test_empty_url_delivers_no_models_without_fetching():
    loader = ServerModelLoader(fetch=lambda url: pytest.fail("must not fetch"))
    results, _done, deliver = _collect()
    loader.request("  ", deliver)
    assert results == [([], None)]


def test_delivers_models_and_errors_off_the_calling_thread():
    calls = []

    def fetch(url):
        calls.append((url, threading.current_thread().name))
        if "broken" in url:
            raise ConnectionError("down")
        return ["whisper-1", "large-v3"]

    loader = ServerModelLoader(fetch=fetch)
    results, done, deliver = _collect()
    loader.request(" http://mac-mini:7860/v1 ", deliver)
    assert done.wait(2)
    assert results == [(["whisper-1", "large-v3"], None)]
    assert calls[0] == ("http://mac-mini:7860/v1", "ServerModelLoader")

    results.clear()
    done.clear()
    loader.request("http://broken/v1", deliver)
    assert done.wait(2)
    models, error = results[0]
    assert models == [] and isinstance(error, ConnectionError)


def test_is_latest_tracks_the_newest_request():
    tokens = []
    loader = ServerModelLoader(fetch=lambda url: [])
    loader.request("", lambda models, error, generation: tokens.append(generation))
    loader.request("", lambda models, error, generation: tokens.append(generation))
    assert loader.is_latest(tokens[1]) and not loader.is_latest(tokens[0])


def test_only_the_newest_request_is_delivered():
    release_old = threading.Event()

    def fetch(url):
        if url == "http://old/v1":
            release_old.wait(2)
            return ["old-model"]
        return ["new-model"]

    loader = ServerModelLoader(fetch=fetch)
    results, done, deliver = _collect()
    loader.request("http://old/v1", deliver)
    loader.request("http://new/v1", deliver)
    assert done.wait(2)
    release_old.set()
    threading.Event().wait(0.1)  # give the superseded worker time to finish
    assert results == [(["new-model"], None)]


def test_list_server_models_reads_openai_model_list(monkeypatch):
    pytest.importorskip("openai")
    from providers.openai_compatible import list_server_models

    seen = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - http.server API
            seen.update(path=self.path, auth=self.headers.get("Authorization", ""))
            if self.path != "/v1/models":
                self.send_response(404)
                self.end_headers()
                return
            payload = json.dumps({"object": "list", "data": [
                {"id": "whisper-1", "object": "model", "created": 0, "owned_by": "local"},
                {"id": "large-v3", "object": "model", "owned_by": "local"},
                {"id": "whisper-1", "object": "model", "created": 0, "owned_by": "local"},
            ]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    monkeypatch.delenv("OPENAI_COMPATIBLE_API_KEY", raising=False)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        assert list_server_models(f"{base}/v1") == ["whisper-1", "large-v3"]
        assert seen == {"path": "/v1/models", "auth": "Bearer not-needed"}
        with pytest.raises(Exception):
            list_server_models(f"{base}/missing")
    finally:
        server.shutdown()
        server.server_close()
