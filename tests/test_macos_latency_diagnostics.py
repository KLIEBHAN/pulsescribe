import json
import threading
from unittest.mock import patch

from utils.macos_latency_diagnostics import (
    MacOSLatencyRun,
    diagnostics_enabled,
    start_macos_latency_run,
)


def test_diagnostics_are_opt_in(monkeypatch):
    monkeypatch.delenv("PULSESCRIBE_MACOS_LATENCY_DIAGNOSTICS", raising=False)
    assert diagnostics_enabled() is False

    monkeypatch.setenv("PULSESCRIBE_MACOS_LATENCY_DIAGNOSTICS", "true")
    with patch("utils.macos_latency_diagnostics.sys.platform", "darwin"):
        assert diagnostics_enabled() is True


def test_disabled_run_is_noop_and_does_not_write(tmp_path):
    path = tmp_path / "latency.jsonl"
    run = start_macos_latency_run(enabled=False, log_path=path)

    run.mark("hotkey_accepted")
    assert run.finish("success") is None
    assert not path.exists()


def test_summary_has_stable_durations_and_mark_once(tmp_path):
    path = tmp_path / "latency.jsonl"
    clock = iter([10.0, 10.010, 10.020, 10.050, 10.080, 10.100])

    with (
        patch("utils.macos_latency_diagnostics.time.perf_counter", side_effect=clock),
        patch.dict(
            "os.environ",
            {"PULSESCRIBE_MACOS_LATENCY_DIAGNOSTICS_FILE": "true"},
        ),
    ):
        run = MacOSLatencyRun(
            enabled=True,
            mode="local",
            streaming=False,
            log_path=path,
        )
        run.mark("hotkey_accepted")
        run.mark_once("audio_stream_started")
        run.mark_once("audio_stream_started")
        run.mark("stop_requested")
        run.mark("paste_done")
        summary = run.finish("success")

    assert summary is not None
    assert summary["durations_ms"]["hotkey_to_audio_ready"] == 10.0
    assert summary["durations_ms"]["release_to_paste_done"] == 30.0
    assert summary["durations_ms"]["end_to_end"] == 90.0
    assert [event["name"] for event in summary["events"]].count(
        "audio_stream_started"
    ) == 1
    assert run.finish("success") is None

    written = json.loads(path.read_text(encoding="utf-8"))
    assert written["durations_ms"] == summary["durations_ms"]


def test_event_fields_drop_text_bearing_values(tmp_path):
    path = tmp_path / "latency.jsonl"
    secret = "private transcript text"
    run = MacOSLatencyRun(enabled=True, log_path=path)

    run.mark(
        "provider_event",
        transcript=secret,
        application="Mail",
        elapsed_ms=12.5,
        cached=True,
    )
    run.finish("error", error_type="RuntimeError")

    serialized = path.read_text(encoding="utf-8")
    assert secret not in serialized
    assert "Mail" not in serialized
    summary = json.loads(serialized)
    assert summary["events"][0]["fields"] == {
        "cached": True,
        "elapsed_ms": 12.5,
    }
    assert summary["error_type"] == "RuntimeError"


def test_mark_once_and_finish_are_atomic_across_threads(tmp_path):
    path = tmp_path / "latency.jsonl"
    run = MacOSLatencyRun(enabled=True, log_path=path)
    mark_barrier = threading.Barrier(8)

    def mark_once():
        mark_barrier.wait()
        run.mark_once("first_audio_callback")

    mark_threads = [threading.Thread(target=mark_once) for _ in range(8)]
    for thread in mark_threads:
        thread.start()
    for thread in mark_threads:
        thread.join()

    finish_barrier = threading.Barrier(8)
    results = []

    def finish():
        finish_barrier.wait()
        results.append(run.finish("success"))

    finish_threads = [threading.Thread(target=finish) for _ in range(8)]
    for thread in finish_threads:
        thread.start()
    for thread in finish_threads:
        thread.join()

    assert sum(result is not None for result in results) == 1
    summary = next(result for result in results if result is not None)
    assert [event["name"] for event in summary["events"]].count(
        "first_audio_callback"
    ) == 1
    assert len(path.read_text(encoding="utf-8").splitlines()) == 1


def test_jsonl_write_failure_is_non_fatal(tmp_path):
    run = MacOSLatencyRun(enabled=True, log_path=tmp_path)
    run.mark("hotkey_accepted")

    assert run.finish("error", error_type="OSError") is not None
