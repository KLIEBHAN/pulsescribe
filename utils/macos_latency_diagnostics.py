"""Opt-in, privacy-safe latency diagnostics for the macOS daemon.

The tracer records monotonic relative timings and small numeric/boolean metadata.
It never records audio, transcript/clipboard text, prompts, hotkeys, application
names, or API credentials. Enable it with
``PULSESCRIBE_MACOS_LATENCY_DIAGNOSTICS=true``.
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

_ENABLE_ENV = "PULSESCRIBE_MACOS_LATENCY_DIAGNOSTICS"
_FILE_ENV = "PULSESCRIBE_MACOS_LATENCY_DIAGNOSTICS_FILE"
_PATH_ENV = "PULSESCRIBE_MACOS_LATENCY_DIAGNOSTICS_PATH"
_DEFAULT_LOG_FILENAME = "macos_latency.jsonl"

_SUMMARY_PAIRS = {
    "hotkey_to_audio_ready": ("hotkey_accepted", "audio_stream_started"),
    "hotkey_to_first_audio": ("hotkey_accepted", "first_audio_callback"),
    "release_to_transcribe_start": ("stop_requested", "transcribe_start"),
    "transcribe_duration": ("transcribe_start", "transcribe_done"),
    "refine_duration": ("refine_start", "refine_done"),
    "result_publish_to_main": ("result_published", "result_received_main"),
    "result_to_paste_done": ("result_received_main", "paste_done"),
    "paste_duration": ("paste_start", "paste_done"),
    "history_duration": ("history_start", "history_done"),
    "release_to_paste_done": ("stop_requested", "paste_done"),
    "end_to_end": ("hotkey_accepted", "finish"),
}


def _env_bool(name: str, *, default: bool) -> bool:
    parsed = parse_bool(os.getenv(name))
    return default if parsed is None else parsed


def diagnostics_enabled() -> bool:
    """Return whether macOS latency diagnostics are enabled."""
    return sys.platform == "darwin" and _env_bool(_ENABLE_ENV, default=False)


def _default_log_path() -> Path:
    override = (os.getenv(_PATH_ENV) or "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / ".pulsescribe" / "logs" / _DEFAULT_LOG_FILENAME


def _safe_fields(fields: dict[str, Any]) -> dict[str, int | float | bool | None]:
    """Keep only scalar non-text metadata in event payloads."""
    return {
        str(key): value
        for key, value in fields.items()
        if value is None or isinstance(value, (int, float, bool))
    }


class MacOSLatencyRun:
    """Thread-safe trace for one accepted macOS recording run."""

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

    def _append_event_locked(
        self,
        name: str,
        *,
        now: float,
        fields: dict[str, Any],
    ) -> None:
        previous = self._events[-1]["t_ms"] if self._events else 0.0
        t_ms = (now - self._start) * 1000
        event: dict[str, Any] = {
            "name": name,
            "t_ms": round(t_ms, 3),
            "dt_ms": round(t_ms - previous, 3),
        }
        safe_fields = _safe_fields(fields)
        if safe_fields:
            event["fields"] = safe_fields
        self._events.append(event)
        self._event_names.add(name)

    def mark(self, name: str, **fields: Any) -> None:
        """Record an event relative to run start."""
        if not self.enabled:
            return

        now = time.perf_counter()
        with self._lock:
            if self._finished:
                return
            self._append_event_locked(name, now=now, fields=fields)

    def mark_once(self, name: str, **fields: Any) -> None:
        """Record an event only once."""
        if not self.enabled:
            return
        with self._lock:
            if self._finished or name in self._event_names:
                return
            self._append_event_locked(
                name,
                now=time.perf_counter(),
                fields=fields,
            )

    def event(self, name: str, fields: dict[str, Any] | None = None) -> None:
        """Callback adapter for lower-level provider latency events."""
        self.mark(name, **(fields or {}))

    def finish(
        self,
        outcome: str,
        *,
        error_type: str | None = None,
    ) -> dict[str, Any] | None:
        """Finalize and emit the run summary. Repeated calls are no-ops."""
        if not self.enabled:
            return None

        now = time.perf_counter()
        with self._lock:
            if self._finished:
                return None
            self._append_event_locked("finish", now=now, fields={})
            self._finished = True
            summary = self._build_summary(outcome=outcome, error_type=error_type)
        self._log_summary(summary)
        if self._write_file:
            self._append_jsonl(summary)
        return summary

    def _event_time_map(self) -> dict[str, float]:
        return {event["name"]: float(event["t_ms"]) for event in self._events}

    def _durations(self) -> dict[str, float]:
        times = self._event_time_map()
        durations: dict[str, float] = {}
        for label, (start_event, end_event) in _SUMMARY_PAIRS.items():
            if start_event in times and end_event in times:
                durations[label] = round(times[end_event] - times[start_event], 3)
        return durations

    def _build_summary(
        self,
        *,
        outcome: str,
        error_type: str | None,
    ) -> dict[str, Any]:
        total_ms = self._events[-1]["t_ms"] if self._events else 0.0
        summary: dict[str, Any] = {
            "run_id": self.run_id,
            "outcome": outcome,
            "mode": self.mode,
            "streaming": self.streaming,
            "total_ms": total_ms,
            "durations_ms": self._durations(),
            "events": list(self._events),
        }
        if error_type:
            summary["error_type"] = error_type
        return summary

    def _log_summary(self, summary: dict[str, Any]) -> None:
        durations = summary.get("durations_ms", {})
        details = (
            ", ".join(
                f"{name}={format_duration(value)}"
                for name, value in durations.items()
                if isinstance(value, (int, float))
            )
            or "no paired durations"
        )
        total = summary.get("total_ms", 0.0)
        self._logger.info(
            "macOS latency run=%s outcome=%s mode=%s streaming=%s total=%s %s",
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
            self._logger.debug("macOS latency diagnostics write failed: %s", exc)


_DISABLED_RUN = MacOSLatencyRun(enabled=False)


def start_macos_latency_run(
    *,
    mode: str | None = None,
    streaming: bool | None = None,
    logger: logging.Logger | None = None,
    enabled: bool | None = None,
    log_path: Path | None = None,
) -> MacOSLatencyRun:
    """Create a run, returning a shared no-op tracer when disabled."""
    is_enabled = diagnostics_enabled() if enabled is None else enabled
    if not is_enabled:
        return _DISABLED_RUN
    return MacOSLatencyRun(
        enabled=True,
        mode=mode,
        streaming=streaming,
        logger=logger,
        log_path=log_path,
    )


__all__ = [
    "MacOSLatencyRun",
    "diagnostics_enabled",
    "start_macos_latency_run",
]
