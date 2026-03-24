import utils.presets as presets


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

    saved: list[tuple[str, str]] = []
    removed: list[str] = []
    monkeypatch.setattr(
        presets, "save_env_setting", lambda key, value: saved.append((key, value))
    )
    monkeypatch.setattr(presets, "remove_env_setting", removed.append)

    assert presets.apply_local_preset_to_env("Test Whisper Preset") is True

    assert ("PULSESCRIBE_MODE", "local") in saved
    assert ("PULSESCRIBE_LOCAL_BACKEND", "whisper") in saved
    assert "PULSESCRIBE_LOCAL_BACKEND" not in removed


def test_apply_local_preset_to_env_resets_lightning_specific_defaults(
    monkeypatch,
) -> None:
    custom_presets = dict(presets.LOCAL_PRESETS)
    custom_presets["Test MLX Preset"] = {
        "local_backend": "mlx",
        "local_model": "turbo",
    }
    monkeypatch.setattr(presets, "LOCAL_PRESETS", custom_presets)

    saved: list[tuple[str, str]] = []
    removed: list[str] = []
    monkeypatch.setattr(
        presets, "save_env_setting", lambda key, value: saved.append((key, value))
    )
    monkeypatch.setattr(presets, "remove_env_setting", removed.append)

    assert presets.apply_local_preset_to_env("Test MLX Preset") is True

    assert "PULSESCRIBE_LIGHTNING_BATCH_SIZE" in removed
    assert "PULSESCRIBE_LIGHTNING_QUANT" in removed
    assert not any(
        key in {"PULSESCRIBE_LIGHTNING_BATCH_SIZE", "PULSESCRIBE_LIGHTNING_QUANT"}
        for key, _ in saved
    )
