from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

import providers.deepgram_stream as deepgram_stream


def _response(
    transcript: str,
    *,
    is_final: bool = False,
    from_finalize: bool = False,
) -> SimpleNamespace:
    return SimpleNamespace(
        channel=SimpleNamespace(
            alternatives=[SimpleNamespace(transcript=transcript)],
        ),
        is_final=is_final,
        from_finalize=from_finalize,
    )


def test_message_handler_writes_interim_text_as_utf8(tmp_path, monkeypatch) -> None:
    interim_file = tmp_path / "interim.txt"
    state = deepgram_stream.StreamState()
    handler = deepgram_stream._create_message_handler(state, "sess")

    monkeypatch.setattr(deepgram_stream, "INTERIM_FILE", interim_file)
    monkeypatch.setattr(deepgram_stream.time, "perf_counter", lambda: 1.0)

    handler(_response("Grüße 你好"))

    assert interim_file.read_text(encoding="utf-8") == "Grüße 你好"
    assert state.last_interim_text == "Grüße 你好"


def test_message_handler_skips_duplicate_interim_writes(monkeypatch) -> None:
    state = deepgram_stream.StreamState()
    handler = deepgram_stream._create_message_handler(state, "sess")
    write_calls: list[tuple[str, str]] = []

    class _FakeInterimFile:
        def write_text(self, text: str, *, encoding: str) -> None:
            write_calls.append((text, encoding))

    perf_counter_values = iter((1.0, 1.2))

    monkeypatch.setattr(deepgram_stream, "INTERIM_FILE", _FakeInterimFile())
    monkeypatch.setattr(
        deepgram_stream.time,
        "perf_counter",
        lambda: next(perf_counter_values),
    )

    handler(_response("same interim"))
    handler(_response("same interim"))

    assert write_calls == [("same interim", "utf-8")]


def test_message_handler_keeps_final_transcripts_out_of_interim_file(monkeypatch) -> None:
    state = deepgram_stream.StreamState()
    handler = deepgram_stream._create_message_handler(state, "sess")
    write_calls: list[tuple[str, str]] = []

    class _FakeInterimFile:
        def write_text(self, text: str, *, encoding: str) -> None:
            write_calls.append((text, encoding))

    monkeypatch.setattr(deepgram_stream, "INTERIM_FILE", _FakeInterimFile())

    handler(_response("final transcript", is_final=True, from_finalize=True))

    assert state.final_transcripts == ["final transcript"]
    assert state.finalize_done.is_set()
    assert write_calls == []


def test_message_handler_logs_redacted_final_transcript(caplog) -> None:
    state = deepgram_stream.StreamState()
    handler = deepgram_stream._create_message_handler(state, "sess")

    with caplog.at_level(logging.INFO, logger="pulsescribe"):
        handler(_response("final transcript", is_final=True, from_finalize=True))

    messages = " ".join(record.getMessage() for record in caplog.records)
    assert "[sess] Final: <redacted 16 chars>" in messages
    assert "final transcript" not in messages


def test_write_interim_text_replaces_file_atomically(tmp_path) -> None:
    interim_file = tmp_path / "interim.txt"
    interim_file.write_text("old interim", encoding="utf-8")

    deepgram_stream._write_interim_text(interim_file, "new interim")

    assert interim_file.read_text(encoding="utf-8") == "new interim"
    assert not interim_file.with_name("interim.txt.tmp").exists()


def test_write_interim_text_preserves_existing_file_on_temp_write_error(
    tmp_path, monkeypatch
) -> None:
    interim_file = tmp_path / "interim.txt"
    interim_file.write_text("stable interim", encoding="utf-8")
    tmp_file = interim_file.with_name("interim.txt.tmp")

    path_cls = type(interim_file)
    original_write_text = path_cls.write_text

    def failing_write_text(self, text: str, *args, **kwargs):
        if self == tmp_file:
            raise OSError("disk full")
        return original_write_text(self, text, *args, **kwargs)

    monkeypatch.setattr(path_cls, "write_text", failing_write_text)

    with pytest.raises(OSError, match="disk full"):
        deepgram_stream._write_interim_text(interim_file, "new interim")

    assert interim_file.read_text(encoding="utf-8") == "stable interim"
    assert not tmp_file.exists()
