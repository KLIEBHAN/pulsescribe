from __future__ import annotations

import logging

import pytest

from providers._client_cache import EnvClientCache


def _test_logger() -> logging.Logger:
    return logging.getLogger("tests.client_cache")


def test_env_client_cache_clears_stale_state_when_env_var_is_missing(monkeypatch) -> None:
    cache = EnvClientCache()
    cache._client = object()
    cache._signature = "stale-key"

    monkeypatch.delenv("TEST_API_KEY", raising=False)

    with pytest.raises(ValueError, match="TEST_API_KEY missing"):
        cache.get(
            env_var="TEST_API_KEY",
            missing_error="TEST_API_KEY missing",
            create_client=lambda _api_key: (_ for _ in ()).throw(
                AssertionError("create_client should not run without an API key")
            ),
            logger=_test_logger(),
            client_label="Test client",
        )

    assert cache._client is None
    assert cache._signature is None


def test_env_client_cache_reuses_cached_client_for_same_env_value(monkeypatch) -> None:
    cache = EnvClientCache()
    created_with: list[str] = []

    def _create_client(api_key: str) -> object:
        created_with.append(api_key)
        return object()

    monkeypatch.setenv("TEST_API_KEY", "key-1")

    first = cache.get(
        env_var="TEST_API_KEY",
        missing_error="TEST_API_KEY missing",
        create_client=_create_client,
        logger=_test_logger(),
        client_label="Test client",
    )
    second = cache.get(
        env_var="TEST_API_KEY",
        missing_error="TEST_API_KEY missing",
        create_client=_create_client,
        logger=_test_logger(),
        client_label="Test client",
    )

    assert first is second
    assert created_with == ["key-1"]


def test_env_client_cache_recreates_client_after_env_value_changes(monkeypatch) -> None:
    cache = EnvClientCache()
    created_with: list[str] = []

    def _create_client(api_key: str) -> dict[str, str]:
        created_with.append(api_key)
        return {"api_key": api_key}

    monkeypatch.setenv("TEST_API_KEY", "key-1")
    first = cache.get(
        env_var="TEST_API_KEY",
        missing_error="TEST_API_KEY missing",
        create_client=_create_client,
        logger=_test_logger(),
        client_label="Test client",
    )

    monkeypatch.setenv("TEST_API_KEY", "key-2")
    second = cache.get(
        env_var="TEST_API_KEY",
        missing_error="TEST_API_KEY missing",
        create_client=_create_client,
        logger=_test_logger(),
        client_label="Test client",
    )

    assert second is not first
    assert created_with == ["key-1", "key-2"]
