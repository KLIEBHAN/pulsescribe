"""LLM-Nachbearbeitung für whisper_go.

Enthält Funktionen für die Nachbearbeitung von Transkripten mit LLMs
(OpenAI, OpenRouter, Groq).
"""

import argparse
import logging
import os

from .prompts import get_prompt_for_context
from .context import detect_context
from utils.timing import log_preview
from utils.logging import get_session_id
from utils.env import get_env_bool_default

# Zentrale Konfiguration importieren
from config import (
    DEFAULT_REFINE_MODEL,
    DEFAULT_GROQ_REFINE_MODEL,
    OPENROUTER_BASE_URL,
)

logger = logging.getLogger("whisper_go")

# Groq-Client Singleton
_groq_client = None


def _get_groq_client():
    """Gibt Groq-Client Singleton zurück (Lazy Init).

    Spart ~30-50ms pro Aufruf durch Connection-Reuse.
    """
    global _groq_client
    if _groq_client is None:
        from groq import Groq

        api_key = os.getenv("GROQ_API_KEY")
        if not api_key:
            raise ValueError("GROQ_API_KEY nicht gesetzt")
        _groq_client = Groq(api_key=api_key)
        logger.debug(f"[{get_session_id()}] Groq-Client initialisiert")
    return _groq_client


def _get_refine_client(provider: str):
    """Erstellt Client für Nachbearbeitung (OpenAI, OpenRouter oder Groq)."""
    if provider == "groq":
        return _get_groq_client()

    from openai import OpenAI

    if provider == "openrouter":
        api_key = os.getenv("OPENROUTER_API_KEY")
        if not api_key:
            raise ValueError("OPENROUTER_API_KEY nicht gesetzt")
        return OpenAI(base_url=OPENROUTER_BASE_URL, api_key=api_key)

    # Default: OpenAI (nutzt OPENAI_API_KEY automatisch)
    return OpenAI()


def _extract_message_content(content) -> str:
    """Extrahiert Text aus OpenAI/OpenRouter Message-Content (String, Liste oder None)."""
    if content is None:
        return ""
    if isinstance(content, list):
        # Liste von Content-Parts → Text-Parts extrahieren
        return "".join(
            part.get("text", "") if isinstance(part, dict) else str(part)
            for part in content
        ).strip()
    return content.strip()


def refine_transcript(
    transcript: str,
    model: str | None = None,
    prompt: str | None = None,
    provider: str | None = None,
    context: str | None = None,
) -> str:
    """Nachbearbeitung mit LLM (Flow-Style). Kontext-aware Prompts.

    Args:
        transcript: Das zu verfeinernde Transkript
        model: LLM-Modell (default: gpt-5-nano oder llama-3.3 für Groq)
        prompt: Custom Prompt (überschreibt Kontext-Prompt)
        provider: LLM-Provider (openai, openrouter, groq)
        context: Kontext-Typ für Prompt-Auswahl (email, chat, code, default)

    Returns:
        Das nachbearbeitete Transkript
    """
    # Import for timed_operation
    try:
        from utils.timing import timed_operation
    except ImportError:
        from contextlib import contextmanager

        @contextmanager
        def timed_operation(name):
            yield

    session_id = get_session_id()

    # Leeres Transkript → nichts zu tun
    if not transcript or not transcript.strip():
        logger.debug(f"[{session_id}] Leeres Transkript, überspringe Nachbearbeitung")
        return transcript

    # Kontext-spezifischen Prompt wählen (falls nicht explizit übergeben)
    # Auch leere Strings werden wie None behandelt (Fallback auf Kontext-Prompt)
    if not prompt:
        effective_context, app_name, source = detect_context(context)
        prompt = get_prompt_for_context(effective_context)
        # Detailliertes Logging mit Quelle
        if app_name:
            logger.info(
                f"[{session_id}] Kontext: {effective_context} (Quelle: {source}, App: {app_name})"
            )
        else:
            logger.info(
                f"[{session_id}] Kontext: {effective_context} (Quelle: {source})"
            )

    # Provider und Modell zur Laufzeit bestimmen (CLI > ENV > Default)
    effective_provider = (
        provider or os.getenv("WHISPER_GO_REFINE_PROVIDER", "openai")
    ).lower()

    # Provider-spezifisches Default-Modell (Groq nutzt Llama, andere GPT-5)
    if model:
        effective_model = model
    elif os.getenv("WHISPER_GO_REFINE_MODEL"):
        effective_model = os.getenv("WHISPER_GO_REFINE_MODEL")
    elif effective_provider == "groq":
        effective_model = DEFAULT_GROQ_REFINE_MODEL
    else:
        effective_model = DEFAULT_REFINE_MODEL

    logger.info(
        f"[{session_id}] LLM-Nachbearbeitung: provider={effective_provider}, model={effective_model}"
    )
    logger.debug(f"[{session_id}] Input: {len(transcript)} Zeichen")

    client = _get_refine_client(effective_provider)
    full_prompt = f"{prompt}\n\nTranskript:\n{transcript}"

    with timed_operation("LLM-Nachbearbeitung"):
        if effective_provider == "groq":
            # Groq nutzt chat.completions API (wie OpenRouter)
            response = client.chat.completions.create(
                model=effective_model,
                messages=[{"role": "user", "content": full_prompt}],
            )
            result = _extract_message_content(response.choices[0].message.content)
        elif effective_provider == "openrouter":
            # OpenRouter API-Aufruf vorbereiten
            create_kwargs = {
                "model": effective_model,
                "messages": [{"role": "user", "content": full_prompt}],
            }

            # Provider-Routing konfigurieren (optional)
            provider_order = os.getenv("OPENROUTER_PROVIDER_ORDER")
            if provider_order:
                providers = [p.strip() for p in provider_order.split(",")]
                allow_fallbacks = get_env_bool_default(
                    "OPENROUTER_ALLOW_FALLBACKS", True
                )
                create_kwargs["extra_body"] = {
                    "provider": {
                        "order": providers,
                        "allow_fallbacks": allow_fallbacks,
                    }
                }
                logger.info(
                    f"[{session_id}] OpenRouter Provider: {', '.join(providers)} "
                    f"(fallbacks: {allow_fallbacks})"
                )

            response = client.chat.completions.create(**create_kwargs)
            result = _extract_message_content(response.choices[0].message.content)
        else:
            # OpenAI responses API
            api_params = {"model": effective_model, "input": full_prompt}
            # GPT-5 nutzt "reasoning" API – "minimal" für schnelle Korrekturen
            # statt tiefgehender Analyse (spart Tokens und Latenz)
            if effective_model.startswith("gpt-5"):
                api_params["reasoning"] = {"effort": "minimal"}
            response = client.responses.create(**api_params)
            result = response.output_text.strip()

    logger.debug(f"[{session_id}] Output: {log_preview(result)}")
    return result


def maybe_refine_transcript(transcript: str, args: argparse.Namespace) -> str:
    """Wendet LLM-Nachbearbeitung an, falls aktiviert. Gibt Rohtext bei Fehler zurück.

    Args:
        transcript: Das zu verfeinernde Transkript
        args: CLI-Argumente mit refine, no_refine, refine_model, refine_provider, context

    Returns:
        Das nachbearbeitete Transkript oder Original bei Fehler/Deaktivierung
    """
    from openai import APIError, APIConnectionError, RateLimitError

    if not args.refine or args.no_refine:
        return transcript

    try:
        return refine_transcript(
            transcript,
            model=args.refine_model,
            provider=args.refine_provider,
            context=getattr(args, "context", None),
        )
    except ValueError as e:
        # Fehlende API-Keys (z.B. OPENROUTER_API_KEY)
        logger.warning(f"LLM-Nachbearbeitung übersprungen: {e}")
        return transcript
    except (APIError, APIConnectionError, RateLimitError) as e:
        logger.warning(f"LLM-Nachbearbeitung fehlgeschlagen: {e}")
        return transcript
