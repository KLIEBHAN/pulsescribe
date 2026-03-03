"""Tests for local-backend UI normalization and persistence defaults."""

from utils.local_backend import (
    DEFAULT_LOCAL_BACKEND,
    normalize_local_backend,
    should_remove_local_backend_env,
)


def test_normalize_local_backend_defaults_to_auto() -> None:
    assert normalize_local_backend(None) == DEFAULT_LOCAL_BACKEND
    assert normalize_local_backend("") == DEFAULT_LOCAL_BACKEND
    assert normalize_local_backend("  invalid  ") == DEFAULT_LOCAL_BACKEND


def test_normalize_local_backend_accepts_valid_values_case_insensitive() -> None:
    assert normalize_local_backend("AUTO") == "auto"
    assert normalize_local_backend("whisper") == "whisper"
    assert normalize_local_backend("Faster") == "faster"
    assert normalize_local_backend("mlx") == "mlx"
    assert normalize_local_backend("LIGHTNING") == "lightning"


def test_should_remove_local_backend_env_only_for_auto() -> None:
    assert should_remove_local_backend_env(None) is True
    assert should_remove_local_backend_env("auto") is True
    assert should_remove_local_backend_env("AUTO") is True
    assert should_remove_local_backend_env("whisper") is False
    assert should_remove_local_backend_env("faster") is False
