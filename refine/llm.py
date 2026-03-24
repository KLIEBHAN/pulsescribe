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
    if isinstance(content, list):
        # Liste von Content-Parts → nur echte Text-Parts extrahieren
        parts: list[str] = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
                continue
            if isinstance(part, dict):
                text = part.get("text")
                if isinstance(text, str):
                    parts.append(text)
                continue
            text = getattr(part, "text", None)
            if isinstance(text, str):
                parts.append(text)
        return "".join(
            parts
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
    effective_provider = _normalize_refine_provider(
        provider or os.getenv("PULSESCRIBE_REFINE_PROVIDER", "groq")
    )

    # Provider-spezifisches Default-Modell (CLI > ENV > Default)
    if effective_provider == "gemini":
        default_model = DEFAULT_GEMINI_REFINE_MODEL
    else:
        default_model = DEFAULT_REFINE_MODEL
    effective_model = model or os.getenv("PULSESCRIBE_REFINE_MODEL") or default_model

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
                timeout=LLM_REFINE_TIMEOUT,
            )
            if not response.choices:
                raise ValueError("Groq-Antwort enthält keine choices")
            result = _extract_message_content(response.choices[0].message.content)
        elif effective_provider == "openrouter":
            # OpenRouter API-Aufruf vorbereiten
            create_kwargs: dict = {
                "model": effective_model,
                "messages": [{"role": "user", "content": full_prompt}],
                "timeout": LLM_REFINE_TIMEOUT,
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
            if not response.choices:
                raise ValueError("OpenRouter-Antwort enthält keine choices")
            result = _extract_message_content(response.choices[0].message.content)
        elif effective_provider == "gemini":
            from google.genai import types

            # "minimal" nur für Flash (schnellste Latenz), Pro braucht "low"
            is_flash_model = "flash" in effective_model.lower()
            thinking_level = (
                types.ThinkingLevel.MINIMAL
                if is_flash_model
                else types.ThinkingLevel.LOW
            )
            logger.info(f"[{session_id}] Gemini thinking_level={thinking_level}")

            # SDK-Timeout: 60s Default, kein zuverlässiger Override möglich
            response = client.models.generate_content(
                model=effective_model,
                contents=full_prompt,
                config=types.GenerateContentConfig(
                    thinking_config=types.ThinkingConfig(thinking_level=thinking_level)
                ),
            )
            result = (response.text or "").strip()
        else:
            # OpenAI responses API
            api_params: dict = {
                "model": effective_model,
                "input": full_prompt,
                "timeout": LLM_REFINE_TIMEOUT,
            }
            # GPT-5 nutzt "reasoning" API – "minimal" für schnelle Korrekturen
            # statt tiefgehender Analyse (spart Tokens und Latenz)
            if effective_model.startswith("gpt-5"):
                api_params["reasoning"] = {"effort": "minimal"}
            response = client.responses.create(**api_params)
            result = (response.output_text or "").strip()

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
