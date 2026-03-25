"""Tests for Windows onboarding IPC helpers."""

from __future__ import annotations

import json

import utils.ipc as ipc


def test_ipc_client_ignores_non_object_json_response(tmp_path, monkeypatch) -> None:
    response_file = tmp_path / "ipc_response.json"
    monkeypatch.setattr(ipc, "IPC_RESPONSE_FILE", response_file)
    response_file.write_text(json.dumps(["not", "a", "dict"]), encoding="utf-8")

    client = ipc.IPCClient()

    assert client.poll_response("cmd-123") is None


def test_ipc_server_ignores_non_object_json_command(tmp_path, monkeypatch) -> None:
    command_file = tmp_path / "ipc_command.json"
    response_file = tmp_path / "ipc_response.json"
    monkeypatch.setattr(ipc, "IPC_COMMAND_FILE", command_file)
    monkeypatch.setattr(ipc, "IPC_RESPONSE_FILE", response_file)
    command_file.write_text(json.dumps(["start_test"]), encoding="utf-8")

    handled: list[tuple[str, str]] = []
    server = ipc.IPCServer(lambda cmd_id, command: handled.append((cmd_id, command)))

    server._process_pending_command()

    assert handled == []
    assert not response_file.exists()


def test_ipc_server_start_cleans_up_stale_ipc_files(tmp_path, monkeypatch) -> None:
    command_file = tmp_path / "ipc_command.json"
    response_file = tmp_path / "ipc_response.json"
    monkeypatch.setattr(ipc, "IPC_COMMAND_FILE", command_file)
    monkeypatch.setattr(ipc, "IPC_RESPONSE_FILE", response_file)

    command_file.write_text(
        json.dumps({"id": "stale-cmd", "command": ipc.CMD_START_TEST}),
        encoding="utf-8",
    )
    response_file.write_text(
        json.dumps({"id": "stale-rsp", "status": ipc.STATUS_DONE}),
        encoding="utf-8",
    )

    started: dict[str, object] = {}

    class _FakeThread:
        def __init__(self, *, target, daemon: bool) -> None:
            started["target"] = target
            started["daemon"] = daemon

        def start(self) -> None:
            started["started"] = True

        def join(self, timeout: float | None = None) -> None:
            started["join_timeout"] = timeout

    monkeypatch.setattr(ipc.threading, "Thread", _FakeThread)

    handled: list[tuple[str, str]] = []
    server = ipc.IPCServer(lambda cmd_id, command: handled.append((cmd_id, command)))

    server.start()

    assert started["started"] is True
    assert not command_file.exists()
    assert not response_file.exists()
    assert handled == []

    server.stop()
