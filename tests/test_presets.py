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
