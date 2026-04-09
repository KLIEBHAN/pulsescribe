"""Shared environment-backed singleton cache for cloud provider SDK clients."""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import Callable
from typing import Any


class EnvClientCache:
    """Cache a lazily created SDK client per API key value.

    The cache resets itself when the backing environment variable disappears,
    and recreates the client when the key changes.
    """

    def __init__(self) -> None:
        self._client: Any | None = None
        self._signature: str | None = None
        self._lock = threading.Lock()

    def reset(self) -> None:
        self._client = None
        self._signature = None

    def get(
        self,
        *,
        env_var: str,
        missing_error: str,
        create_client: Callable[[str], Any],
        logger: logging.Logger,
        client_label: str,
    ) -> Any:
        api_key = os.getenv(env_var)
        if not api_key:
            self.reset()
            raise ValueError(missing_error)

        if self._client is None or self._signature != api_key:
            with self._lock:
                api_key = os.getenv(env_var)
                if not api_key:
                    self.reset()
                    raise ValueError(missing_error)
                if self._client is None or self._signature != api_key:
                    self._client = create_client(api_key)
                    self._signature = api_key
                    logger.debug("%s initialisiert", client_label)

        return self._client
