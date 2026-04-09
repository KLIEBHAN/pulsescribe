"""Shared environment-backed singleton cache for cloud provider SDK clients."""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import Callable
from importlib import import_module
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

        if self._client is not None and self._signature == api_key:
            return self._client

        with self._lock:
            # Double-check after acquiring lock
            api_key = os.getenv(env_var)
            if not api_key:
                self.reset()
                raise ValueError(missing_error)
            if self._client is not None and self._signature == api_key:
                return self._client

            self._client = create_client(api_key)
            self._signature = api_key
            logger.debug("%s initialisiert", client_label)
            return self._client


def build_cached_env_client_getter(
    *,
    cache: EnvClientCache,
    env_var: str,
    missing_error: str,
    dependency_module: str,
    dependency_class: str,
    logger: logging.Logger,
    client_label: str,
) -> Callable[[], Any]:
    """Build a lazy SDK-client getter backed by ``EnvClientCache``.

    The dependency import stays lazy and only happens once a valid API key is
    available and a new client instance is actually needed.
    """

    def _create_client(api_key: str) -> Any:
        client_class = getattr(import_module(dependency_module), dependency_class)
        return client_class(api_key=api_key)

    def _get_client() -> Any:
        return cache.get(
            env_var=env_var,
            missing_error=missing_error,
            create_client=_create_client,
            logger=logger,
            client_label=client_label,
        )

    return _get_client


__all__ = ["EnvClientCache", "build_cached_env_client_getter"]
