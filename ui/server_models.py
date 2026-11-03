"""Background loading of the own server's model list for the Settings model picker.

Both native settings windows create one ``ServerModelLoader`` and call
``request(base_url, deliver)``. ``deliver(models, error)`` runs on a worker thread;
the UI marshals it to its main thread. Only the newest request is delivered, so a
slow answer for an old URL never overwrites the list for the current one.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

logger = logging.getLogger("pulsescribe.ui.server_models")

Deliver = Callable[[list[str], Exception | None], None]


def _fetch_models(base_url: str) -> list[str]:
    from providers.openai_compatible import list_server_models

    return list_server_models(base_url)


class ServerModelLoader:
    """Fetch model IDs off the UI thread and drop answers to superseded requests."""

    def __init__(self, fetch: Callable[[str], list[str]] = _fetch_models) -> None:
        self._fetch = fetch
        self._lock = threading.Lock()
        self._generation = 0

    def request(self, base_url: str, deliver: Deliver) -> None:
        """Start loading the models of ``base_url``; an empty URL delivers no models."""
        with self._lock:
            self._generation += 1
            generation = self._generation
        url = (base_url or "").strip()
        if not url:
            deliver([], None)
            return

        def run() -> None:
            models: list[str] = []
            error: Exception | None = None
            try:
                models = self._fetch(url)
            except Exception as exc:  # network, HTTP or format errors end up in the UI
                error = exc
                logger.info("Server-Modelle nicht geladen: %s", type(exc).__name__)
            with self._lock:
                if generation != self._generation:
                    return
            deliver(models, error)

        threading.Thread(target=run, name="ServerModelLoader", daemon=True).start()


__all__ = ["ServerModelLoader"]
