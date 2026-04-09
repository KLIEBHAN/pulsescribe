"""Shared helpers for resolving common provider transcription inputs."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO

from ._language import normalize_auto_language


@dataclass(frozen=True)
class ResolvedTranscriptionRequest:
    """Normalized provider request data used across cloud transcription backends."""

    model: str
    language: str | None
    audio_kb: int


def resolve_transcription_request(
    audio_path: Path,
    *,
    model: str | None,
    default_model: str,
    language: str | None,
) -> ResolvedTranscriptionRequest:
    """Resolve shared per-request provider inputs from CLI/env values and audio."""
    return ResolvedTranscriptionRequest(
        model=model or default_model,
        language=normalize_auto_language(language),
        audio_kb=audio_path.stat().st_size // 1024,
    )


def build_transcription_params(
    *,
    model: str,
    language: str | None,
    extra_params: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Build provider request params while omitting auto-detect languages."""
    params: dict[str, object] = {"model": model}
    if extra_params:
        params.update(dict(extra_params))
    if language:
        params["language"] = language
    return params


def execute_audio_file_request(
    audio_path: Path,
    *,
    request_callable: Callable[..., Any],
    build_params: Callable[[BinaryIO], Mapping[str, object]],
) -> Any:
    """Open an audio file once and execute a provider SDK request with it."""
    with audio_path.open("rb") as audio_file:
        return request_callable(**dict(build_params(audio_file)))


def execute_audio_transcription_request(
    audio_path: Path,
    *,
    request_callable: Callable[..., Any],
    model: str,
    language: str | None,
    extra_params: Mapping[str, object] | None = None,
    build_file_payload: Callable[[Path, BinaryIO], object] | None = None,
) -> Any:
    """Execute a file-based transcription request with shared param assembly."""

    def _build_params(audio_file: BinaryIO) -> Mapping[str, object]:
        file_payload = audio_file
        if build_file_payload is not None:
            file_payload = build_file_payload(audio_path, audio_file)
        return build_transcription_params(
            model=model,
            language=language,
            extra_params={
                "file": file_payload,
                **dict(extra_params or {}),
            },
        )

    return execute_audio_file_request(
        audio_path,
        request_callable=request_callable,
        build_params=_build_params,
    )
