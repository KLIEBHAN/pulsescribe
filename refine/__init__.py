"""LLM-Nachbearbeitung für whisper_go.

Bietet Funktionen für die Nachbearbeitung von Transkripten mit LLMs.

Usage:
    from refine import refine_transcript, detect_context

    context, app, source = detect_context()
    refined = refine_transcript(transcript, context=context)
"""

from .context import detect_context, get_context_for_app
from .llm import refine_transcript, maybe_refine_transcript

__all__ = [
    "refine_transcript",
    "maybe_refine_transcript",
    "detect_context",
    "get_context_for_app",
]
