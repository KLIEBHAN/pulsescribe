from __future__ import annotations

import json
import logging

from utils.windows_latency_diagnostics import (
    WindowsLatencyRun,
    diagnostics_enabled,
    start_windows_latency_run,
)


def test_latency_run_records_summary_and_jsonl(tmp_path, monkeypatch, caplog):
    times = iter([10.000, 10.020, 10.120, 10.150, 10.170])
    monkeypatch.setattr(
        "utils.windows_latency_diagnostics.time.perf_counter",
        lambda: next(times),
    )

    log_path = tmp_path / "windows_latency.jsonl"
    run = WindowsLatencyRun(
        enabled=True,
        mode="deepgram",
        streaming=True,
        log_path=log_path,
    )

    with caplog.at_level(logging.INFO, logger="pulsescribe"):
        run.mark("start_recording")
        run.mark("listening_state")
        run.mark("recording_state")
        summary = run.finish("done", chars=12)

    assert summary is not None
    assert summary["outcome"] == "done"
    assert summary["durations_ms"]["start_to_listening"] == 100.0
    assert summary["durations_ms"]["listening_to_recording"] == 30.0
    assert "Windows latency" in " ".join(r.getMessage() for r in caplog.records)

    written = json.loads(log_path.read_text(encoding="utf-8").strip())
    assert written["run_id"] == summary["run_id"]
    assert written["finish_fields"] == {"chars": 12}


def test_latency_mark_once_records_only_first_occurrence(tmp_path, monkeypatch):
    times = iter([1.0, 1.1, 1.2, 1.3])
    monkeypatch.setattr(
        "utils.windows_latency_diagnostics.time.perf_counter",
        lambda: next(times),
    )

    run = WindowsLatencyRun(enabled=True, log_path=tmp_path / "latency.jsonl")
    run.mark_once("first_audio_callback")
    run.mark_once("first_audio_callback")
    summary = run.finish("done")

    assert summary is not None
    names = [event["name"] for event in summary["events"]]
    assert names == ["first_audio_callback", "finish"]


def test_start_latency_run_returns_noop_when_disabled(monkeypatch):
    monkeypatch.setattr(
        "utils.windows_latency_diagnostics.sys.platform",
        "win32",
    )
    monkeypatch.delenv("PULSESCRIBE_WINDOWS_LATENCY_DIAGNOSTICS", raising=False)

    assert diagnostics_enabled() is False
    run = start_windows_latency_run(mode="openai", streaming=False)
    run.mark("start_recording")

    assert run.enabled is False


def test_diagnostics_enabled_on_windows_when_env_true(monkeypatch):
    monkeypatch.setattr(
        "utils.windows_latency_diagnostics.sys.platform",
        "win32",
    )
    monkeypatch.setenv("PULSESCRIBE_WINDOWS_LATENCY_DIAGNOSTICS", "true")

    assert diagnostics_enabled() is True
