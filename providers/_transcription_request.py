"""Shared helpers for resolving common provider transcription inputs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

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
