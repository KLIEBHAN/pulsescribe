"""Structured latency diagnostics for the Windows daemon.

The tracer is intentionally lightweight and privacy-safe: it records event names,
relative timings, and small metadata such as mode/provider flags, never audio or
transcript text. It is opt-in via ``PULSESCRIBE_WINDOWS_LATENCY_DIAGNOSTICS``.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from utils.env import parse_bool
from utils.timing import format_duration

_ENABLE_ENV = "PULSESCRIBE_WINDOWS_LATENCY_DIAGNOSTICS"
_FILE_ENV = "PULSESCRIBE_WINDOWS_LATENCY_DIAGNOSTICS_FILE"
_DEFAULT_LOG_FILENAME = "windows_latency.jsonl"

_SUMMARY_PAIRS = {
    "start_to_listening": ("start_recording", "listening_state"),
    "listening_to_recording": ("listening_state", "recording_state"),
    "start_to_first_audio": ("start_recording", "first_audio_callback"),
    "stop_to_transcribing": ("stop_requested", "transcribing_state"),
    "stop_to_deepgram_return": ("stop_requested", "deepgram_core_return"),
    "stop_to_rest_done": ("stop_requested", "rest_transcribe_done"),
    "result_to_paste_done": ("result_ready", "paste_done"),
}


def _env_bool(name: str, *, default: bool) -> bool:
    parsed = parse_bool(os.getenv(name))
    return default if parsed is None else parsed


def diagnostics_enabled() -> bool:
    """Return whether Windows latency diagnostics are enabled for this process."""
    return sys.platform == "win32" and _env_bool(_ENABLE_ENV, default=False)


def _default_log_path() -> Path:
    return Path.home() / ".pulsescribe" / "logs" / _DEFAULT_LOG_FILENAME


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return str(value)


class WindowsLatencyRun:
    """A single recording-run latency trace."""

    def __init__(
        self,
        *,
        enabled: bool,
        mode: str | None = None,
        streaming: bool | None = None,
        logger: logging.Logger | None = None,
        log_path: Path | None = None,
    ) -> None:
        self.enabled = enabled
        self.run_id = uuid.uuid4().hex[:8]
        self.mode = mode
        self.streaming = streaming
        self._logger = logger or logging.getLogger("pulsescribe")
        self._log_path = log_path if log_path is not None else _default_log_path()
        self._write_file = _env_bool(_FILE_ENV, default=True)
        self._start = time.perf_counter()
        self._events: list[dict[str, Any]] = []
        self._event_names: set[str] = set()
        self._finished = False
        self._lock = threading.Lock()

    def mark(self, name: str, **fields: Any) -> None:
        """Record an event timestamp relative to run start."""
        if not self.enabled or self._finished:
            return

        now = time.perf_counter()
        with self._lock:
            previous = self._events[-1]["t_ms"] if self._events else 0.0
            t_ms = (now - self._start) * 1000
            event = {
                "name": name,
                "t_ms": round(t_ms, 3),
                "dt_ms": round(t_ms - previous, 3),
            }
            if fields:
                event["fields"] = _json_safe(fields)
            self._events.append(event)
            self._event_names.add(name)

    def mark_once(self, name: str, **fields: Any) -> None:
        """Record an event only the first time it occurs in this run."""
        if not self.enabled:
            return
        with self._lock:
            if name in self._event_names:
                return
        self.mark(name, **fields)

    def finish(self, outcome: str, **fields: Any) -> dict[str, Any] | None:
        """Finalize and emit a compact summary. Safe to call repeatedly."""
        if not self.enabled:
            return None

        with self._lock:
            if self._finished:
                return None

        self.mark("finish", outcome=outcome, **fields)
        with self._lock:
            self._finished = True
        summary = self._build_summary(outcome=outcome, fields=fields)
        self._log_summary(summary)
        if self._write_file:
            self._append_jsonl(summary)
        return summary

    def event(self, name: str, fields: dict[str, Any] | None = None) -> None:
        """Callback-compatible event adapter for lower-level providers."""
        self.mark(name, **(fields or {}))

    def _event_time_map(self) -> dict[str, float]:
        return {event["name"]: float(event["t_ms"]) for event in self._events}

    def _durations(self) -> dict[str, float]:
        times = self._event_time_map()
        durations: dict[str, float] = {}
        for label, (start_event, end_event) in _SUMMARY_PAIRS.items():
            if start_event in times and end_event in times:
                durations[label] = round(times[end_event] - times[start_event], 3)
        return durations

    def _build_summary(self, *, outcome: str, fields: dict[str, Any]) -> dict[str, Any]:
        total_ms = self._events[-1]["t_ms"] if self._events else 0.0
        return {
            "run_id": self.run_id,
            "outcome": outcome,
            "mode": self.mode,
            "streaming": self.streaming,
            "total_ms": total_ms,
            "durations_ms": self._durations(),
            "finish_fields": _json_safe(fields),
            "events": list(self._events),
        }

    def _log_summary(self, summary: dict[str, Any]) -> None:
        durations = summary.get("durations_ms", {})
        duration_parts = [
            f"{name}={format_duration(value)}"
            for name, value in durations.items()
            if isinstance(value, (int, float))
        ]
        details = ", ".join(duration_parts) if duration_parts else "no paired durations"
        total = summary.get("total_ms", 0.0)
        self._logger.info(
            "Windows latency run=%s outcome=%s mode=%s streaming=%s total=%s %s",
            summary.get("run_id"),
            summary.get("outcome"),
            summary.get("mode"),
            summary.get("streaming"),
            format_duration(float(total) if isinstance(total, (int, float)) else 0.0),
            details,
        )

    def _append_jsonl(self, summary: dict[str, Any]) -> None:
        try:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
            with self._log_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(summary, ensure_ascii=False, sort_keys=True))
                handle.write("\n")
        except OSError as exc:
            self._logger.debug("Windows latency diagnostics write failed: %s", exc)


_DISABLED_RUN = WindowsLatencyRun(enabled=False)


def start_windows_latency_run(
    *,
    mode: str | None = None,
    streaming: bool | None = None,
    logger: logging.Logger | None = None,
    enabled: bool | None = None,
    log_path: Path | None = None,
) -> WindowsLatencyRun:
    """Create a latency run, returning a no-op object when disabled."""
    is_enabled = diagnostics_enabled() if enabled is None else enabled
    if not is_enabled:
        return _DISABLED_RUN
    return WindowsLatencyRun(
        enabled=True,
        mode=mode,
        streaming=streaming,
        logger=logger,
        log_path=log_path,
    )


__all__ = [
    "WindowsLatencyRun",
    "diagnostics_enabled",
    "start_windows_latency_run",
]
