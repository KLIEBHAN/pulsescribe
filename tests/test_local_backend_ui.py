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


def test_available_backend_options_filter_only_in_the_packaged_app(monkeypatch) -> None:
    import utils.local_backend as lb

    options = ["auto", "whisper", "faster", "mlx", "lightning"]
    monkeypatch.setattr(lb, "is_local_backend_installed", lambda b: b in {"mlx", "faster"})
    monkeypatch.setattr(lb, "is_frozen_app", lambda: False)
    assert lb.available_local_backend_options(options) == options
    monkeypatch.setattr(lb, "is_frozen_app", lambda: True)
    assert lb.available_local_backend_options(options) == ["auto", "faster", "mlx"]


def test_is_local_backend_installed_checks_modules_without_importing(monkeypatch) -> None:
    import utils.local_backend as lb

    seen = []
    monkeypatch.setattr(lb.importlib.util, "find_spec", lambda name: seen.append(name) or None)
    assert lb.is_local_backend_installed("lightning") is False
    assert lb.is_local_backend_installed("unknown") is False
    assert seen == ["lightning_whisper_mlx"]


def test_advanced_options_apply_only_where_the_backend_uses_them() -> None:
    import utils.local_backend as lb

    def options(backend, platform):
        return {o for o in lb.ADVANCED_OPTION_BACKENDS if lb.advanced_option_applies(o, backend, platform=platform)}

    assert options("mlx", "darwin") == {
        "preset", "warmup", "idle_unload", "live_preview", "fast", "fp16", "best_of", "temperature",
    }
    assert "live_preview" not in options("mlx", "win32")  # only the macOS daemon previews
    assert options("lightning", "darwin") == {"preset", "warmup", "idle_unload", "lightning_batch", "lightning_quant"}
    assert "idle_unload" not in options("faster", "win32")  # only the macOS daemon unloads
    assert "device" not in options("faster", "darwin")  # no GPU path for faster-whisper on macOS
    assert {"device", "beam_size", "compute_type"} <= options("faster", "win32")
    assert "fp16" not in options("faster", "win32")
    assert set(lb.ADVANCED_OPTION_HELP) == set(lb.ADVANCED_OPTION_BACKENDS)


def test_live_preview_needs_a_fast_model() -> None:
    import utils.local_backend as lb

    for model in (
        "large", "large-v2", "large-v3", " Large-V3 ",
        "mlx-community/whisper-large-v3-mlx", "mlx-community/whisper-large-v3-mlx-8bit",
        "mlx-community/whisper-large-v2-mlx-q4", "whisper-large-v3",
    ):
        assert not lb.live_preview_supports_model(model), model
    for model in (
        None, "", "default", "turbo", "small", "large-en", "large-v3-turbo",
        "mlx-community/whisper-large-v3-turbo", "mlx-community/whisper-large-v3-turbo-8bit",
        "mlx-community/distil-whisper-large-v3", "mlx-community/whisper-small-mlx",
    ):
        assert lb.live_preview_supports_model(model), model
        assert lb.live_preview_model_hint(model) is None

    hint = lb.live_preview_model_hint("large-v3")
    assert hint is not None and "large-v3" in hint and "turbo" in hint


def test_live_preview_setting_has_three_values() -> None:
    import utils.local_backend as lb

    for value in (None, "", "true", "1", "anything"):
        assert lb.normalize_live_preview(value) == lb.LIVE_PREVIEW_ON
    for value in ("false", " False ", "0", "off"):
        assert lb.normalize_live_preview(value) == lb.LIVE_PREVIEW_OFF
    assert lb.normalize_live_preview(" Streaming ") == lb.LIVE_PREVIEW_STREAMING


def test_effective_local_backend_resolves_auto_like_the_provider(monkeypatch) -> None:
    import platform

    import utils.local_backend as lb

    installed = {"mlx", "faster"}
    monkeypatch.setattr(lb, "is_local_backend_installed", lambda b: b in installed)
    monkeypatch.setattr(platform, "machine", lambda: "arm64")
    assert lb.effective_local_backend("auto", platform="darwin") == "mlx"
    assert lb.effective_local_backend(None, platform="win32") == "faster"
    assert lb.effective_local_backend("lightning", platform="darwin") == "lightning"
    installed.clear()
    assert lb.effective_local_backend("auto", platform="darwin") == "whisper"


def test_advanced_value_error_accepts_defaults_and_rejects_out_of_range() -> None:
    from utils.local_backend import advanced_value_error, first_advanced_value_error

    assert advanced_value_error("beam_size", "") is None
    assert advanced_value_error("beam_size", " 5 ") is None
    assert "1 to 20" in advanced_value_error("beam_size", "0")
    assert "1 to 10" in advanced_value_error("best_of", "abc")
    assert advanced_value_error("cpu_threads", "0") is None
    assert advanced_value_error("temperature", "0.0,0.2, 0.4") is None
    assert "decimal separator" in advanced_value_error("temperature", "0,5")  # German decimal comma
    assert "decimal separator" in advanced_value_error("temperature", "warm")
    # Hidden options never block saving: MLX has no beam search.
    assert first_advanced_value_error({"beam_size": "99"}, "mlx", platform="darwin") is None
    assert "Beam size" in first_advanced_value_error({"beam_size": "99"}, "faster", platform="win32")


def test_cpu_threads_bound_follows_detected_cores(monkeypatch) -> None:
    import utils.local_backend as lb

    monkeypatch.setattr(lb.os, "cpu_count", lambda: 64)
    assert lb.advanced_int_range("cpu_threads") == (0, 64)
    assert lb.advanced_value_error("cpu_threads", "64") is None
    assert lb.advanced_value_error("cpu_threads", "65") is not None
