"""Provider für eigene Server mit OpenAI-kompatibler Transkriptions-API.

Sendet die Aufnahme an ``{OPENAI_COMPATIBLE_BASE_URL}/audio/transcriptions`` im
Format der OpenAI-API, etwa an einen Whisper-Server im eigenen Netz
(``http://mac-mini:7860/v1``). ``OPENAI_COMPATIBLE_API_KEY`` ist optional, weil
viele eigene Server keinen Key prüfen; der echte ``OPENAI_API_KEY`` wird hier nie
verwendet. ``OPENAI_COMPATIBLE_MODEL`` wählt das Modell des Servers.
"""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from config import DEFAULT_OPENAI_COMPATIBLE_MODEL
from utils.timing import timed_operation

from ._response_utils import log_transcription_result, require_text_response
from ._transcription_request import (
    execute_audio_transcription_request,
    resolve_transcription_request,
)

logger = logging.getLogger("pulsescribe.providers.openai_compatible")

BASE_URL_ENV_VAR = "OPENAI_COMPATIBLE_BASE_URL"
API_KEY_ENV_VAR = "OPENAI_COMPATIBLE_API_KEY"
MODEL_ENV_VAR = "OPENAI_COMPATIBLE_MODEL"
# Das OpenAI-SDK verlangt einen Key; Server ohne Authentifizierung ignorieren ihn.
_PLACEHOLDER_API_KEY = "not-needed"
_MISSING_BASE_URL_MESSAGE = (
    f"{BASE_URL_ENV_VAR} nicht gesetzt. "
    f"Bitte die Server-Adresse eintragen, z. B. {BASE_URL_ENV_VAR}=http://mac-mini:7860/v1"
)

_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}

_client_lock = threading.Lock()
_client: Any | None = None
_client_signature: tuple[str, str] | None = None


def _read_settings() -> tuple[str, str]:
    """Return (base_url, api_key) from the environment; raise if no server is set."""
    base_url = os.getenv(BASE_URL_ENV_VAR, "").strip()
    if not base_url:
        raise ValueError(_MISSING_BASE_URL_MESSAGE)
    api_key = os.getenv(API_KEY_ENV_VAR, "").strip() or _PLACEHOLDER_API_KEY
    return base_url, api_key


def _warn_if_key_sent_unencrypted(base_url: str, api_key: str) -> None:
    """Log a warning when a real server key would travel over plain HTTP to another host.

    Plain HTTP stays allowed for trusted LANs; HTTPS protects audio and key in transit.
    """
    if api_key == _PLACEHOLDER_API_KEY:
        return
    parts = urlsplit(base_url)
    if parts.scheme.lower() == "http" and (parts.hostname or "") not in _LOOPBACK_HOSTS:
        logger.warning(
            "%s wird unverschlüsselt per HTTP an %s gesendet; "
            "außerhalb eines vertrauenswürdigen Netzes HTTPS verwenden.",
            API_KEY_ENV_VAR,
            parts.hostname or "?",
        )


def _get_client() -> Any:
    """Return the cached OpenAI SDK client, recreated when URL or key change."""
    global _client, _client_signature
    signature = _read_settings()
    with _client_lock:
        if _client is None or _client_signature != signature:
            from openai import OpenAI

            base_url, api_key = signature
            _warn_if_key_sent_unencrypted(base_url, api_key)
            _client = OpenAI(base_url=base_url, api_key=api_key)
            _client_signature = signature
            logger.debug("OpenAI-kompatibler Client initialisiert")
        return _client


def _reset_client() -> None:
    """Drop the cached client; tests use it to start from a clean state."""
    global _client, _client_signature
    with _client_lock:
        _client = None
        _client_signature = None


class OpenAICompatibleProvider:
    """Transkription über einen eigenen Server mit OpenAI-kompatibler API."""

    name = "openai_compatible"

    @property
    def default_model(self) -> str:
        """Model used when the caller passes none: OPENAI_COMPATIBLE_MODEL, else whisper-1."""
        return os.getenv(MODEL_ENV_VAR, "").strip() or DEFAULT_OPENAI_COMPATIBLE_MODEL

    def supports_streaming(self) -> bool:
        return False

    def transcribe(
        self,
        audio_path: Path,
        model: str | None = None,
        language: str | None = None,
    ) -> str:
        """Lädt die Aufnahme hoch und gibt den transkribierten Text zurück.

        Args:
            audio_path: Pfad zur Audio-Datei
            model: Modellname des Servers (default: whisper-1)
            language: Sprachcode oder None für Auto-Detection
        """
        client = _get_client()
        request = resolve_transcription_request(
            audio_path,
            model=model,
            default_model=self.default_model,
            language=language,
        )
        logger.info(
            "OpenAI-kompatibel: %s @ %s, %sKB, lang=%s",
            request.model,
            urlsplit(os.getenv(BASE_URL_ENV_VAR, "")).hostname or "?",
            request.audio_kb,
            request.language or "auto",
        )

        with timed_operation(
            "OpenAI-kompatible Transkription", logger=logger, include_session=False
        ):
            response = execute_audio_transcription_request(
                audio_path,
                request_callable=client.audio.transcriptions.create,
                model=request.model,
                language=request.language,
                # json ist das Standardformat der API und wird von allen
                # kompatiblen Servern unterstützt.
                extra_params={"response_format": "json"},
            )

        result = require_text_response(response, provider_name="OpenAI-kompatibel")
        log_transcription_result(logger, result)
        return result


__all__ = ["OpenAICompatibleProvider"]
