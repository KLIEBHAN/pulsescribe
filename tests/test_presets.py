import pytest

import utils.presets as presets


def _capture_env_updates(monkeypatch):
    recorded: dict[str, str | None] = {}
    monkeypatch.setattr(
        presets,
        "update_env_settings",
        lambda updates: recorded.update(updates),
    )
    return recorded


def test_default_local_presets_do_not_treat_windows_arm_as_apple_silicon(
    monkeypatch,
) -> None:
    monkeypatch.setattr(presets.platform, "system", lambda: "Windows")
    monkeypatch.setattr(presets.platform, "machine", lambda: "ARM64")

    assert presets.is_apple_silicon() is False
    assert presets.default_local_preset_fast() == "CPU: faster int8 (turbo)"
    assert presets.default_local_preset_private() == "CPU: faster int8 (turbo)"


def test_default_local_private_preset_uses_mlx_on_macos_arm(monkeypatch) -> None:
    monkeypatch.setattr(presets.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(presets.platform, "machine", lambda: "arm64")

    assert presets.is_apple_silicon() is True
    assert presets.default_local_preset_private() == "macOS: MLX Balanced (large)"


def test_default_local_presets_fall_back_to_cpu_when_platform_detection_fails(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        presets.platform,
        "system",
        lambda: (_ for _ in ()).throw(RuntimeError("platform unavailable")),
    )

    assert presets.is_apple_silicon() is False
    assert presets.default_local_preset_fast() == "CPU: faster int8 (turbo)"
    assert presets.default_local_preset_private() == "CPU: faster int8 (turbo)"


def test_apply_local_preset_to_env_preserves_explicit_whisper_backend(
    monkeypatch,
) -> None:
    custom_presets = dict(presets.LOCAL_PRESETS)
    custom_presets["Test Whisper Preset"] = {
        "local_backend": "whisper",
        "local_model": "turbo",
    }
    monkeypatch.setattr(presets, "LOCAL_PRESETS", custom_presets)
    updates = _capture_env_updates(monkeypatch)

    assert presets.apply_local_preset_to_env("Test Whisper Preset") is True

    assert updates["PULSESCRIBE_MODE"] == "local"
    assert updates["PULSESCRIBE_LOCAL_BACKEND"] == "whisper"
    assert updates["PULSESCRIBE_LOCAL_BACKEND"] is not None


def test_apply_local_preset_to_env_normalizes_aliases_and_default_like_values(
    monkeypatch,
) -> None:
    custom_presets = dict(presets.LOCAL_PRESETS)
    custom_presets["Test Alias Preset"] = {
        "local_backend": " MLX-WHISPER ",
        "local_model": " default ",
        "device": " AUTO ",
        "warmup": " auto ",
        "local_fast": " TRUE ",
        "fp16": " default ",
        "without_timestamps": " FALSE ",
        "vad_filter": " default ",
        "compute_type": "INT8",
        "cpu_threads": "8",
    }
    monkeypatch.setattr(presets, "LOCAL_PRESETS", custom_presets)
    updates = _capture_env_updates(monkeypatch)

    assert presets.apply_local_preset_to_env("Test Alias Preset") is True

    assert updates["PULSESCRIBE_LOCAL_BACKEND"] == "mlx"
    assert updates["PULSESCRIBE_LOCAL_MODEL"] is None
    assert updates["PULSESCRIBE_DEVICE"] is None
    assert updates["PULSESCRIBE_LOCAL_WARMUP"] is None
    assert updates["PULSESCRIBE_LOCAL_FAST"] == "true"
    assert updates[presets.LOCAL_FP16_ENV_KEY] is None
    assert updates["PULSESCRIBE_LOCAL_WITHOUT_TIMESTAMPS"] == "false"
    assert updates["PULSESCRIBE_LOCAL_VAD_FILTER"] is None
    assert updates["PULSESCRIBE_LOCAL_COMPUTE_TYPE"] == "INT8"
    assert updates["PULSESCRIBE_LOCAL_CPU_THREADS"] == "8"


def test_apply_local_preset_to_env_resets_lightning_specific_defaults(
    monkeypatch,
) -> None:
    custom_presets = dict(presets.LOCAL_PRESETS)
    custom_presets["Test MLX Preset"] = {
        "local_backend": "mlx",
        "local_model": "turbo",
    }
    monkeypatch.setattr(presets, "LOCAL_PRESETS", custom_presets)
    updates = _capture_env_updates(monkeypatch)

    assert presets.apply_local_preset_to_env("Test MLX Preset") is True

    assert updates["PULSESCRIBE_LIGHTNING_BATCH_SIZE"] is None
    assert updates["PULSESCRIBE_LIGHTNING_QUANT"] is None


def test_apply_local_preset_to_env_uses_shared_defaults_without_mutating_base(
    monkeypatch,
) -> None:
    custom_presets = dict(presets.LOCAL_PRESETS)
    custom_presets["Test Minimal Preset"] = {
        "local_backend": "mlx",
        "local_model": "turbo",
    }
    monkeypatch.setattr(presets, "LOCAL_PRESETS", custom_presets)
    updates = _capture_env_updates(monkeypatch)
    original_base = dict(presets.LOCAL_PRESET_BASE)

    assert presets.apply_local_preset_to_env("Test Minimal Preset") is True

    assert updates["PULSESCRIBE_LOCAL_COMPUTE_TYPE"] is None
    assert updates["PULSESCRIBE_LOCAL_CPU_THREADS"] is None
    assert updates["PULSESCRIBE_LIGHTNING_BATCH_SIZE"] is None
    assert presets.LOCAL_PRESET_BASE == original_base


def test_apply_local_preset_to_env_migrates_legacy_fp16_key(monkeypatch) -> None:
    custom_presets = dict(presets.LOCAL_PRESETS)
    custom_presets["Test FP16 Preset"] = {
        "local_backend": "whisper",
        "local_model": "turbo",
        "fp16": "true",
    }
    monkeypatch.setattr(presets, "LOCAL_PRESETS", custom_presets)
    updates = _capture_env_updates(monkeypatch)

    assert presets.apply_local_preset_to_env("Test FP16 Preset") is True

    assert updates[presets.LOCAL_FP16_ENV_KEY] == "true"
    assert updates[presets.LEGACY_LOCAL_FP16_ENV_KEY] is None


def test_build_local_preset_env_updates_merges_defaults_before_normalizing() -> None:
    updates = presets._build_local_preset_env_updates(
        {
            "local_backend": "mlx",
            "local_model": "turbo",
        }
    )

    assert updates["PULSESCRIBE_MODE"] == "local"
    assert updates["PULSESCRIBE_LOCAL_BACKEND"] == "mlx"
    assert updates["PULSESCRIBE_LOCAL_MODEL"] == "turbo"
    assert updates["PULSESCRIBE_DEVICE"] is None
    assert updates["PULSESCRIBE_LOCAL_WARMUP"] is None
    assert updates["PULSESCRIBE_LOCAL_FAST"] is None
    assert updates["PULSESCRIBE_LIGHTNING_BATCH_SIZE"] is None
    assert updates[presets.LEGACY_LOCAL_FP16_ENV_KEY] is None


def test_apply_local_preset_to_env_returns_false_without_writing_for_unknown_presets(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        presets,
        "update_env_settings",
        lambda updates: (_ for _ in ()).throw(
            AssertionError(f"unexpected env write: {updates}")
        ),
    )

    assert presets.apply_local_preset_to_env("missing preset") is False


@pytest.mark.parametrize(
    ("preset_key", "env_key", "raw_value"),
    [
        ("local_backend", "PULSESCRIBE_LOCAL_BACKEND", " AUTO "),
        ("local_backend", "PULSESCRIBE_LOCAL_BACKEND", "unknown"),
        ("local_model", "PULSESCRIBE_LOCAL_MODEL", " DEFAULT "),
        ("device", "PULSESCRIBE_DEVICE", " AUTO "),
        ("warmup", "PULSESCRIBE_LOCAL_WARMUP", " AUTO "),
        ("local_fast", "PULSESCRIBE_LOCAL_FAST", " DEFAULT "),
        ("fp16", "PULSESCRIBE_FP16", " DEFAULT "),
        ("without_timestamps", "PULSESCRIBE_LOCAL_WITHOUT_TIMESTAMPS", " DEFAULT "),
        ("vad_filter", "PULSESCRIBE_LOCAL_VAD_FILTER", " DEFAULT "),
        ("lightning_batch_size", "PULSESCRIBE_LIGHTNING_BATCH_SIZE", " 12 "),
        ("lightning_quant", "PULSESCRIBE_LIGHTNING_QUANT", " NONE "),
    ],
)
def test_build_local_preset_env_updates_removes_default_like_values(
    preset_key, env_key, raw_value
) -> None:
    for value in (raw_value, "", " \t "):
        updates = presets._build_local_preset_env_updates({preset_key: value})
        assert env_key in updates
        assert updates[env_key] is None


def test_build_local_preset_env_updates_preserves_text_order_and_inputs() -> None:
    values = {
        "local_backend": " FASTER-WHISPER ",
        "local_model": " TURBO ",
        "device": " CPU ",
        "warmup": " FALSE ",
        "local_fast": " TRUE ",
        "fp16": " FALSE ",
        "beam_size": " 003 ",
        "best_of": " not-an-integer ",
        "temperature": " 0.0 ",
        "compute_type": " INT8 ",
        "cpu_threads": " 0 ",
        "num_workers": " 02 ",
        "without_timestamps": " FALSE ",
        "vad_filter": " TRUE ",
        "lightning_batch_size": " 012 ",
        "lightning_quant": " 8BIT ",
        "unknown_option": "ignored",
    }
    original_values = dict(values)
    original_base = dict(presets.LOCAL_PRESET_BASE)
    expected = {
        "PULSESCRIBE_MODE": "local",
        "PULSESCRIBE_LOCAL_FP16": None,
        "PULSESCRIBE_LOCAL_BACKEND": "faster",
        "PULSESCRIBE_LOCAL_MODEL": "turbo",
        "PULSESCRIBE_DEVICE": "cpu",
        "PULSESCRIBE_LOCAL_WARMUP": "false",
        "PULSESCRIBE_LOCAL_FAST": "true",
        "PULSESCRIBE_FP16": "false",
        "PULSESCRIBE_LOCAL_BEAM_SIZE": "003",
        "PULSESCRIBE_LOCAL_BEST_OF": "not-an-integer",
        "PULSESCRIBE_LOCAL_TEMPERATURE": "0.0",
        "PULSESCRIBE_LOCAL_COMPUTE_TYPE": "INT8",
        "PULSESCRIBE_LOCAL_CPU_THREADS": "0",
        "PULSESCRIBE_LOCAL_NUM_WORKERS": "02",
        "PULSESCRIBE_LOCAL_WITHOUT_TIMESTAMPS": "false",
        "PULSESCRIBE_LOCAL_VAD_FILTER": "true",
        "PULSESCRIBE_LIGHTNING_BATCH_SIZE": "012",
        "PULSESCRIBE_LIGHTNING_QUANT": "8bit",
    }

    updates = presets._build_local_preset_env_updates(values)

    assert list(updates.items()) == list(expected.items())
    assert values == original_values
    assert presets.LOCAL_PRESET_BASE == original_base


@pytest.mark.parametrize("preset_name", presets.LOCAL_PRESETS)
def test_apply_builtin_preset_writes_one_batch(monkeypatch, preset_name) -> None:
    calls = []
    monkeypatch.setattr(presets, "update_env_settings", calls.append)
    original_preset = dict(presets.LOCAL_PRESETS[preset_name])

    assert presets.apply_local_preset_to_env(preset_name) is True

    assert len(calls) == 1
    updates = calls[0]
    assert len(updates) == 18
    assert updates["PULSESCRIBE_MODE"] == "local"
    assert updates["PULSESCRIBE_LOCAL_FP16"] is None
    assert updates["PULSESCRIBE_LOCAL_BACKEND"] == original_preset["local_backend"]
    assert updates["PULSESCRIBE_LOCAL_MODEL"] == original_preset["local_model"]
    assert updates["PULSESCRIBE_LIGHTNING_BATCH_SIZE"] is None
    assert updates["PULSESCRIBE_LIGHTNING_QUANT"] is None
    assert presets.LOCAL_PRESETS[preset_name] == original_preset
