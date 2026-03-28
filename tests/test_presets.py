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
