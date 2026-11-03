"""Background loading of the own server's model list for the Settings model picker.

Both native settings windows create one ``ServerModelLoader`` and call
``request(base_url, deliver)``. ``deliver(models, error, generation)`` runs on a
worker thread (or inline for an empty URL); the UI marshals it to its main thread
and applies it only if ``is_latest(generation)`` still holds there, so a slow answer
for an old URL never overwrites the list for the current one.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

logger = logging.getLogger("pulsescribe.ui.server_models")

Deliver = Callable[[list[str], Exception | None, int], None]


def _fetch_models(base_url: str) -> list[str]:
    from providers.openai_compatible import list_server_models

    return list_server_models(base_url)


class ServerModelLoader:
    """Fetch model IDs off the UI thread and drop answers to superseded requests."""

    def __init__(self, fetch: Callable[[str], list[str]] = _fetch_models) -> None:
        self._fetch = fetch
        self._lock = threading.Lock()
        self._generation = 0

    def is_latest(self, generation: int) -> bool:
        """True while no newer request was started; check right before applying a result."""
        with self._lock:
            return generation == self._generation

    def request(self, base_url: str, deliver: Deliver) -> None:
        """Start loading the models of ``base_url``; an empty URL delivers no models."""
        with self._lock:
            self._generation += 1
            generation = self._generation
        url = (base_url or "").strip()
        if not url:
            deliver([], None, generation)
            return

        def run() -> None:
            models: list[str] = []
            error: Exception | None = None
            try:
                models = self._fetch(url)
            except Exception as exc:  # network, HTTP or format errors end up in the UI
                error = exc
                logger.info("Server-Modelle nicht geladen: %s", type(exc).__name__)
            if self.is_latest(generation):
                deliver(models, error, generation)

        threading.Thread(target=run, name="ServerModelLoader", daemon=True).start()


__all__ = ["ServerModelLoader"]
