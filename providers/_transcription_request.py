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


def _resolve_model_name(model: str | None, default_model: str) -> str:
    """Return the explicit request model or the provider default."""
    return model or default_model


def _measure_audio_size_kb(audio_path: Path) -> int:
    """Measure the request audio size in kilobytes for stable logging."""
    return audio_path.stat().st_size // 1024


def resolve_transcription_request(
    audio_path: Path,
    *,
    model: str | None,
    default_model: str,
    language: str | None,
) -> ResolvedTranscriptionRequest:
    """Resolve shared per-request provider inputs from CLI/env values and audio."""
    return ResolvedTranscriptionRequest(
        model=_resolve_model_name(model, default_model),
        language=normalize_auto_language(language),
        audio_kb=_measure_audio_size_kb(audio_path),
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


def _resolve_audio_file_payload(
    audio_path: Path,
    audio_file: BinaryIO,
    *,
    build_file_payload: Callable[[Path, BinaryIO], object] | None,
) -> object:
    """Build the provider-specific file payload while keeping the default simple."""
    if build_file_payload is None:
        return audio_file
    return build_file_payload(audio_path, audio_file)


@dataclass(frozen=True)
class _AudioTranscriptionRequestSpec:
    """Shared specification for one file-based cloud transcription request."""

    model: str
    language: str | None
    extra_params: Mapping[str, object] | None = None
    build_file_payload: Callable[[Path, BinaryIO], object] | None = None

    def build_params(self, audio_path: Path, audio_file: BinaryIO) -> dict[str, object]:
        """Assemble the provider request params for an already opened audio file."""
        return build_transcription_params(
            model=self.model,
            language=self.language,
            extra_params={
                "file": _resolve_audio_file_payload(
                    audio_path,
                    audio_file,
                    build_file_payload=self.build_file_payload,
                ),
                **dict(self.extra_params or {}),
            },
        )


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
    request_spec = _AudioTranscriptionRequestSpec(
        model=model,
        language=language,
        extra_params=extra_params,
        build_file_payload=build_file_payload,
    )
    return execute_audio_file_request(
        audio_path,
        request_callable=request_callable,
        build_params=lambda audio_file: request_spec.build_params(audio_path, audio_file),
    )
