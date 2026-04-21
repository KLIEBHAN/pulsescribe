"""Tests for local-backend UI normalization and persistence defaults."""

from utils.local_backend import (
    DEFAULT_LOCAL_BACKEND,
    get_cpu_threads_limit,
    get_local_advanced_ui_state,
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


def test_normalize_local_backend_accepts_supported_aliases() -> None:
    assert normalize_local_backend("openai-whisper") == "whisper"
    assert normalize_local_backend("faster-whisper") == "faster"
    assert normalize_local_backend("mlx-whisper") == "mlx"
    assert normalize_local_backend("lightning-whisper-mlx") == "lightning"


def test_should_remove_local_backend_env_only_for_auto() -> None:
    assert should_remove_local_backend_env(None) is True
    assert should_remove_local_backend_env("auto") is True
    assert should_remove_local_backend_env("AUTO") is True
    assert should_remove_local_backend_env("whisper") is False
    assert should_remove_local_backend_env("faster") is False
    assert should_remove_local_backend_env("faster-whisper") is False


def test_get_cpu_threads_limit_uses_minimum_fallback() -> None:
    assert get_cpu_threads_limit(None) == 32
    assert get_cpu_threads_limit(0) == 32
    assert get_cpu_threads_limit(-4) == 32
    assert get_cpu_threads_limit(8) == 32


def test_get_cpu_threads_limit_allows_high_core_systems() -> None:
    assert get_cpu_threads_limit(64) == 64
    assert get_cpu_threads_limit(128) == 128


def test_local_advanced_ui_state_guides_cloud_mode_to_local() -> None:
    state = get_local_advanced_ui_state("deepgram", "auto")

    assert state.show_general is False
    assert state.show_faster is False
    assert state.show_lightning is False
    assert "Local Whisper mode" in state.guidance


def test_local_advanced_ui_state_only_reveals_selected_backend_controls() -> None:
    faster = get_local_advanced_ui_state("local", "faster")
    lightning = get_local_advanced_ui_state("local", "lightning")
    auto = get_local_advanced_ui_state("local", "auto")

    assert faster.show_general is True
    assert faster.show_faster is True
    assert faster.show_lightning is False

    assert lightning.show_general is True
    assert lightning.show_faster is False
    assert lightning.show_lightning is True

    assert auto.show_general is True
    assert auto.show_faster is False
    assert auto.show_lightning is False
    assert "recommended local defaults" in auto.guidance
