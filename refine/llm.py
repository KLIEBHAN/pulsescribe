"""LLM-Nachbearbeitung für PulseScribe.

Enthält Funktionen für die Nachbearbeitung von Transkripten mit LLMs
(OpenAI, OpenRouter, Groq).
"""

import logging
import os
import threading

from .prompts import get_prompt_for_context
from .context import detect_context
from utils.timing import redacted_text_summary
from utils.logging import get_session_id
from utils.env import get_env_bool_default

# Zentrale Konfiguration importieren
from config import (
    DEFAULT_REFINE_MODEL,
    DEFAULT_GEMINI_REFINE_MODEL,
    OPENROUTER_BASE_URL,
    LLM_REFINE_TIMEOUT,
)

logger = logging.getLogger("pulsescribe")
_SUPPORTED_REFINE_PROVIDERS = ("gemini", "groq", "openai", "openrouter")

# Client Singletons (Lazy Init, spart ~30-50ms pro Aufruf durch Connection-Reuse)
_client_lock = threading.Lock()
_clients: dict[str, object] = {}         # provider -> client instance
_signatures: dict[str, object] = {}      # provider -> signature for cache invalidation


def _normalize_refine_provider(provider: str) -> str:
    """Return a normalized provider name or raise for unsupported values."""
    normalized = (provider or "").strip().lower()
    if normalized in _SUPPORTED_REFINE_PROVIDERS:
        return normalized

    supported = ", ".join(_SUPPORTED_REFINE_PROVIDERS)
    raise ValueError(
        f"Unbekannter Refine-Provider '{provider}'. Unterstützt: {supported}"
    )


def _get_or_create_client(name: str, env_var: str, signature_fn, factory):
    """Double-checked locking für Thread-Safe Lazy-Init eines LLM-Clients.

    Args:
        name: Provider-Name für Logging und Cache-Key.
        env_var: Name der Umgebungsvariable für den API-Key.
        signature_fn: Berechnet Cache-Signatur aus dem API-Key.
        factory: Erstellt einen neuen Client aus dem API-Key.
    """
    api_key = os.getenv(env_var)
    if not api_key:
        raise ValueError(f"{env_var} nicht gesetzt")

    sig = signature_fn(api_key)
    if _clients.get(name) is not None and _signatures.get(name) == sig:
        return _clients[name]

    with _client_lock:
        api_key = os.getenv(env_var)
        if not api_key:
            _clients.pop(name, None)
            _signatures.pop(name, None)
            raise ValueError(f"{env_var} nicht gesetzt")
        sig = signature_fn(api_key)
        if _clients.get(name) is None or _signatures.get(name) != sig:
            _clients[name] = factory(api_key)
            _signatures[name] = sig
            logger.debug(f"[{get_session_id()}] {name}-Client initialisiert")
    return _clients[name]


def _get_groq_client():
    """Gibt Groq-Client Singleton zurück (Lazy Init, Thread-Safe)."""
    def _factory(api_key):
        from groq import Groq
        return Groq(api_key=api_key)

    return _get_or_create_client("Groq", "GROQ_API_KEY", lambda k: k, _factory)


def _get_openai_client():
    """Gibt OpenAI-Client Singleton zurück (Lazy Init, Thread-Safe)."""
    def _factory(api_key):
        from openai import OpenAI
        return OpenAI(api_key=api_key)

    return _get_or_create_client("OpenAI", "OPENAI_API_KEY", lambda k: k, _factory)


def _get_openrouter_client():
    """Gibt OpenRouter-Client Singleton zurück (Lazy Init, Thread-Safe)."""
    def _factory(api_key):
        from openai import OpenAI
        return OpenAI(base_url=OPENROUTER_BASE_URL, api_key=api_key)

    return _get_or_create_client(
        "OpenRouter", "OPENROUTER_API_KEY",
        lambda k: (OPENROUTER_BASE_URL, k), _factory,
    )


def _get_gemini_client():
    """Gibt Gemini-Client Singleton zurück (Lazy Init, Thread-Safe).

    Nutzt google-genai SDK für Gemini 3 API.
    """
    def _factory(api_key):
        from google import genai
        return genai.Client(api_key=api_key)

    return _get_or_create_client("Gemini", "GEMINI_API_KEY", lambda k: k, _factory)


def _get_refine_client(provider: str):
    """Gibt gecachten Client für Nachbearbeitung zurück (OpenAI, OpenRouter, Groq oder Gemini)."""
    provider = _normalize_refine_provider(provider)
    if provider == "groq":
        return _get_groq_client()
    if provider == "openrouter":
        return _get_openrouter_client()
    if provider == "gemini":
        return _get_gemini_client()
    return _get_openai_client()


def _extract_message_content(content) -> str:
    """Extrahiert Text aus OpenAI/OpenRouter Message-Content (String, Liste oder None)."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, dict):
        text = content.get("text")
        return text.strip() if isinstance(text, str) else ""
    if isinstance(content, list):
        # Liste von Content-Parts → nur echte Text-Parts extrahieren
        parts: list[str] = []
        for part in content:
            parts.append(_extract_message_content(part))
        return "".join(parts).strip()

    text = getattr(content, "text", None)
    if isinstance(text, str):
        return text.strip()

    nested_parts = getattr(content, "parts", None)
    if isinstance(nested_parts, list):
        return _extract_message_content(nested_parts)

    raise TypeError(f"Unerwarteter Message-Content-Typ: {type(content)}")


def _log_detected_context(
    *,
    session_id: str,
    effective_context: str,
    source: str,
    app_name: str | None,
) -> None:
    """Log the resolved prompt context in one shared format."""
    if app_name:
        logger.info(
            f"[{session_id}] Kontext: {effective_context} (Quelle: {source}, App: {app_name})"
        )
        return

    logger.info(f"[{session_id}] Kontext: {effective_context} (Quelle: {source})")


def _resolve_refine_prompt(
    prompt: str | None,
    *,
    context: str | None,
    session_id: str,
) -> str:
    """Resolve the effective prompt while preserving current fallback behavior."""
    if prompt:
        return prompt

    effective_context, app_name, source = detect_context(context)
    resolved_prompt = get_prompt_for_context(effective_context)
    _log_detected_context(
        session_id=session_id,
        effective_context=effective_context,
        source=source,
        app_name=app_name,
    )
    return resolved_prompt


def _default_refine_model_for_provider(provider: str) -> str:
    """Return the provider-specific default model."""
    if provider == "gemini":
        return DEFAULT_GEMINI_REFINE_MODEL
    return DEFAULT_REFINE_MODEL


def _resolve_refine_provider(provider: str | None) -> str:
    """Resolve the effective refine provider from CLI/env/default values."""
    return _normalize_refine_provider(
        provider or os.getenv("PULSESCRIBE_REFINE_PROVIDER", "groq")
    )


def _resolve_refine_model(provider: str, model: str | None) -> str:
    """Resolve the effective refine model from CLI/env/provider defaults."""
    return (
        model
        or os.getenv("PULSESCRIBE_REFINE_MODEL")
        or _default_refine_model_for_provider(provider)
    )


def _resolve_refine_target(
    provider: str | None,
    model: str | None,
) -> tuple[str, str]:
    """Resolve provider and model together so callers stay in sync."""
    effective_provider = _resolve_refine_provider(provider)
    return effective_provider, _resolve_refine_model(effective_provider, model)


def _build_chat_messages(full_prompt: str) -> list[dict[str, str]]:
    """Build the shared chat-completions message payload."""
    return [{"role": "user", "content": full_prompt}]


def _extract_choice_message_text(response: object, *, provider_name: str) -> str:
    """Extract the first choice text from chat-based provider responses."""
    choices = getattr(response, "choices", None)
    if not choices:
        raise ValueError(f"{provider_name}-Antwort enthält keine choices")
    return _extract_message_content(choices[0].message.content)


def _build_openrouter_create_kwargs(
    model: str,
    full_prompt: str,
    *,
    session_id: str,
) -> dict[str, object]:
    """Build OpenRouter request kwargs, including optional routing hints."""
    create_kwargs: dict[str, object] = {
        "model": model,
        "messages": _build_chat_messages(full_prompt),
        "timeout": LLM_REFINE_TIMEOUT,
    }

    provider_order = os.getenv("OPENROUTER_PROVIDER_ORDER")
    if provider_order:
        providers = [p.strip() for p in provider_order.split(",") if p.strip()]
        if providers:
            allow_fallbacks = get_env_bool_default("OPENROUTER_ALLOW_FALLBACKS", True)
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
        else:
            logger.warning(
                f"[{session_id}] OPENROUTER_PROVIDER_ORDER ignoriert "
                "(keine gültigen Provider nach Normalisierung)"
            )

    return create_kwargs


def _resolve_gemini_thinking_level(types_module: object, model: str):
    """Choose the current Gemini thinking level based on model family."""
    thinking_level = getattr(types_module, "ThinkingLevel")
    if "flash" in model.lower():
        return thinking_level.MINIMAL
    return thinking_level.LOW


def _build_openai_api_params(model: str, full_prompt: str) -> dict[str, object]:
    """Build the OpenAI Responses API params with the existing GPT-5 tweak."""
    api_params: dict[str, object] = {
        "model": model,
        "input": full_prompt,
        "timeout": LLM_REFINE_TIMEOUT,
    }
    if model.startswith("gpt-5"):
        api_params["reasoning"] = {"effort": "minimal"}
    return api_params


def _execute_groq_refine(
    client: object,
    model: str,
    full_prompt: str,
    *,
    session_id: str,
) -> str:
    """Execute the Groq chat-completions refine request."""
    del session_id
    response = client.chat.completions.create(
        model=model,
        messages=_build_chat_messages(full_prompt),
        timeout=LLM_REFINE_TIMEOUT,
    )
    return _extract_choice_message_text(response, provider_name="Groq")


def _execute_openrouter_refine(
    client: object,
    model: str,
    full_prompt: str,
    *,
    session_id: str,
) -> str:
    """Execute the OpenRouter refine request with optional routing config."""
    response = client.chat.completions.create(
        **_build_openrouter_create_kwargs(model, full_prompt, session_id=session_id)
    )
    return _extract_choice_message_text(response, provider_name="OpenRouter")


def _execute_gemini_refine(
    client: object,
    model: str,
    full_prompt: str,
    *,
    session_id: str,
) -> str:
    """Execute the Gemini refine request with provider-specific thinking config."""
    from google.genai import types

    thinking_level = _resolve_gemini_thinking_level(types, model)
    logger.info(f"[{session_id}] Gemini thinking_level={thinking_level}")

    response = client.models.generate_content(
        model=model,
        contents=full_prompt,
        config=types.GenerateContentConfig(
            thinking_config=types.ThinkingConfig(thinking_level=thinking_level)
        ),
    )
    return (response.text or "").strip()


def _execute_openai_refine(
    client: object,
    model: str,
    full_prompt: str,
    *,
    session_id: str,
) -> str:
    """Execute the OpenAI Responses API refine request."""
    del session_id
    response = client.responses.create(**_build_openai_api_params(model, full_prompt))
    return (response.output_text or "").strip()


_REFINE_REQUEST_EXECUTORS = {
    "groq": _execute_groq_refine,
    "openrouter": _execute_openrouter_refine,
    "gemini": _execute_gemini_refine,
    "openai": _execute_openai_refine,
}


def _execute_refine_request(
    provider: str,
    client: object,
    model: str,
    full_prompt: str,
    *,
    session_id: str,
) -> str:
    """Dispatch the normalized refine request to the provider-specific executor."""
    return _REFINE_REQUEST_EXECUTORS[provider](
        client,
        model,
        full_prompt,
        session_id=session_id,
    )


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
        model: LLM-Modell (default: openai/gpt-oss-120b für Groq)
        prompt: Custom Prompt (überschreibt Kontext-Prompt)
        provider: LLM-Provider (groq, openai, openrouter)
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

    prompt = _resolve_refine_prompt(
        prompt,
        context=context,
        session_id=session_id,
    )

    effective_provider, effective_model = _resolve_refine_target(provider, model)

    logger.info(
        f"[{session_id}] LLM-Nachbearbeitung: provider={effective_provider}, model={effective_model}"
    )
    logger.debug(f"[{session_id}] Input: {len(transcript)} Zeichen")

    client = _get_refine_client(effective_provider)
    full_prompt = f"{prompt}\n\nTranskript:\n{transcript}"

    with timed_operation("LLM-Nachbearbeitung"):
        result = _execute_refine_request(
            effective_provider,
            client,
            effective_model,
            full_prompt,
            session_id=session_id,
        )

    logger.debug(f"[{session_id}] Output: {redacted_text_summary(result)}")
    return result


def maybe_refine_transcript(
    transcript: str,
    *,
    refine: bool = False,
    no_refine: bool = False,
    refine_model: str | None = None,
    refine_provider: str | None = None,
    context: str | None = None,
) -> str:
    """Wendet LLM-Nachbearbeitung an, falls aktiviert. Gibt Rohtext bei Fehler zurück.

    Args:
        transcript: Das zu verfeinernde Transkript
        refine: LLM-Nachbearbeitung aktivieren
        no_refine: LLM-Nachbearbeitung deaktivieren (ueberschreibt refine)
        refine_model: Modell fuer Nachbearbeitung
        refine_provider: Provider (openai, openrouter, groq)
        context: Kontext-Typ (email, chat, code, default)

    Returns:
        Das nachbearbeitete Transkript oder Original bei Fehler/Deaktivierung
    """
    from openai import APIError, APIConnectionError, APITimeoutError, RateLimitError

    if not refine or no_refine:
        return transcript

    try:
        result = refine_transcript(
            transcript,
            model=refine_model,
            provider=refine_provider,
            context=context,
        )
        # Fallback auf Original wenn LLM leeren String zurückgibt
        if not result or not result.strip():
            logger.warning(
                "LLM-Nachbearbeitung gab leeren String zurück, verwende Original"
            )
            return transcript
        return result
    except ValueError as e:
        # Fehlende API-Keys (z.B. OPENROUTER_API_KEY)
        logger.warning(f"LLM-Nachbearbeitung übersprungen: {e}")
        return transcript
    except APITimeoutError as e:
        logger.warning(f"LLM-Nachbearbeitung Timeout: {e}")
        return transcript
    except (APIError, APIConnectionError, RateLimitError) as e:
        logger.warning(f"LLM-Nachbearbeitung fehlgeschlagen: {e}")
        return transcript
    except Exception:
        # Generischer Fallback für unerwartete Fehler (z.B. Netzwerk, JSON-Parsing)
        logger.exception("LLM-Nachbearbeitung fehlgeschlagen (unerwartet)")
        return transcript
