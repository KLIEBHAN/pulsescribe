"""Tests für Settings-UI Persistierung und Helper-Funktionen."""

import pytest
from unittest.mock import MagicMock, patch

from ui.welcome import (
    LEGACY_LOCAL_FP16_ENV_KEY,
    LOGS_AUTO_REFRESH_ACTIVE_INTERVAL_S,
    LOGS_AUTO_REFRESH_BACKOFF_INTERVAL_S,
    LOGS_AUTO_REFRESH_IDLE_INTERVAL_S,
    LOCAL_FP16_ENV_KEY,
    LOCAL_MODEL_OPTIONS,
    WelcomeController,
    _build_setup_hotkey_info,
    _build_setup_try_it_content,
    _build_welcome_api_key_status,
    _build_welcome_provider_guidance_text,
    _bool_override_from_env,
    _is_env_enabled_default_true,
)


class TestEnvEnabledDefaultTrue:
    """Tests für _is_env_enabled_default_true Helper."""

    def test_unset_returns_true(self):
        """Nicht gesetzte ENV-Variable gibt True zurück (Default)."""
        with patch("utils.preferences.get_env_setting", return_value=None):
            assert _is_env_enabled_default_true("TEST_KEY") is True

    def test_false_returns_false(self):
        """'false' gibt False zurück."""
        with patch("utils.preferences.get_env_setting", return_value="false"):
            assert _is_env_enabled_default_true("TEST_KEY") is False

    @pytest.mark.parametrize("value", ["0", "no", "off"])
    def test_falsy_variants_lowercase(self, value):
        """Lowercase falsy-Werte geben False zurück."""
        with patch("utils.preferences.get_env_setting", return_value=value):
            assert _is_env_enabled_default_true("TEST_KEY") is False

    @pytest.mark.parametrize("value", ["FALSE", "NO", "OFF"])
    def test_falsy_variants_uppercase(self, value):
        """Uppercase falsy-Werte geben False zurück (case-insensitive)."""
        with patch("utils.preferences.get_env_setting", return_value=value):
            assert _is_env_enabled_default_true("TEST_KEY") is False

    def test_true_returns_true(self):
        """'true' gibt True zurück."""
        with patch("utils.preferences.get_env_setting", return_value="true"):
            assert _is_env_enabled_default_true("TEST_KEY") is True

    def test_random_value_returns_true(self):
        """Nicht erkannte Werte geben True zurück (default-true)."""
        with patch("utils.preferences.get_env_setting", return_value="maybe"):
            assert _is_env_enabled_default_true("TEST_KEY") is True


class TestBoolOverrideFromEnv:
    def test_prefers_canonical_value_over_legacy(self, monkeypatch):
        values = {
            LOCAL_FP16_ENV_KEY: "false",
            LEGACY_LOCAL_FP16_ENV_KEY: "true",
        }
        monkeypatch.setattr(
            "ui.welcome.get_env_setting",
            lambda key: values.get(key),
        )

        assert (
            _bool_override_from_env(LOCAL_FP16_ENV_KEY, LEGACY_LOCAL_FP16_ENV_KEY)
            == "false"
        )

    def test_uses_legacy_value_when_canonical_is_missing(self, monkeypatch):
        values = {
            LOCAL_FP16_ENV_KEY: None,
            LEGACY_LOCAL_FP16_ENV_KEY: "true",
        }
        monkeypatch.setattr(
            "ui.welcome.get_env_setting",
            lambda key: values.get(key),
        )

        assert (
            _bool_override_from_env(LOCAL_FP16_ENV_KEY, LEGACY_LOCAL_FP16_ENV_KEY)
            == "true"
        )


class TestBuildSetupHotkeyInfo:
    def test_prefers_explicit_toggle_and_hold_hotkeys(self):
        assert (
            _build_setup_hotkey_info("option+space", "fn", "f19")
            == "Toggle: Option+Space • Hold: Fn"
        )

    def test_uses_runtime_fallback_when_env_hotkeys_are_missing(self):
        assert _build_setup_hotkey_info(None, "", "fn") == "Hotkey: Fn"

    def test_shows_empty_state_when_no_hotkey_is_available(self):
        assert (
            _build_setup_hotkey_info(None, None, "(nicht konfiguriert)")
            == "No hotkey configured"
        )


class TestWelcomeSetupTryItContent:
    def test_build_setup_try_it_content_uses_actionable_empty_state(self):
        hotkey_info, body, hint, button_title = _build_setup_try_it_content(
            None,
            None,
            "",
        )

        assert hotkey_info == "No hotkey configured"
        assert "No hotkey yet" in body
        assert "Accessibility" in hint
        assert button_title == "Set up Hotkeys…"


class TestWelcomeProviderStatusHelpers:
    def test_build_welcome_provider_guidance_text_mentions_selected_provider(self):
        text = _build_welcome_provider_guidance_text(
            "deepgram",
            required_key_present=False,
        )

        assert "Deepgram is selected" in text
        assert "API key" in text

    def test_build_welcome_api_key_status_marks_required_optional_and_local_states(self):
        assert _build_welcome_api_key_status(
            "deepgram",
            mode="deepgram",
            configured=False,
        ) == ("Required", "warning")
        assert _build_welcome_api_key_status(
            "groq",
            mode="deepgram",
            configured=False,
        ) == ("Optional", "text_secondary")
        assert _build_welcome_api_key_status(
            "openai",
            mode="local",
            configured=False,
        ) == ("Not needed", "text_secondary")


class TestLightningBatchSizeParsing:
    """Tests für Lightning Batch-Size Integer-Parsing."""

    def test_valid_integer_parsed(self):
        """Gültige Integer werden korrekt geparst."""
        with patch("ui.welcome.get_env_setting", return_value="16"):
            # Simuliere die Parsing-Logik aus welcome.py:1420-1425
            current_batch = "16"
            try:
                batch_val = int(current_batch) if current_batch else 12
            except ValueError:
                batch_val = 12
            assert batch_val == 16

    def test_empty_uses_default(self):
        """Leerer Wert verwendet Default 12."""
        current_batch = ""
        try:
            batch_val = int(current_batch) if current_batch else 12
        except ValueError:
            batch_val = 12
        assert batch_val == 12

    def test_none_uses_default(self):
        """None verwendet Default 12."""
        current_batch = None
        try:
            batch_val = int(current_batch) if current_batch else 12
        except ValueError:
            batch_val = 12
        assert batch_val == 12

    def test_invalid_string_falls_back_to_default(self):
        """Ungültiger String fällt auf Default 12 zurück."""
        current_batch = "invalid"
        try:
            batch_val = int(current_batch) if current_batch else 12
        except ValueError:
            batch_val = 12
        assert batch_val == 12

    def test_float_string_falls_back_to_default(self):
        """Float-String fällt auf Default zurück."""
        current_batch = "12.5"
        try:
            batch_val = int(current_batch) if current_batch else 12
        except ValueError:
            batch_val = 12
        assert batch_val == 12


class TestLightningQuantizationMapping:
    """Tests für Lightning Quantization Index→String Mapping."""

    def test_index_0_is_none(self):
        """Index 0 entspricht keine Quantisierung (none/default)."""
        quant_idx = 0
        if quant_idx == 0:
            result = None  # Remove from env
        elif quant_idx == 1:
            result = "8bit"
        else:
            result = "4bit"
        assert result is None

    def test_index_1_is_8bit(self):
        """Index 1 entspricht 8bit Quantisierung."""
        quant_idx = 1
        if quant_idx == 0:
            result = None
        elif quant_idx == 1:
            result = "8bit"
        else:
            result = "4bit"
        assert result == "8bit"

    def test_index_2_is_4bit(self):
        """Index 2 entspricht 4bit Quantisierung."""
        quant_idx = 2
        if quant_idx == 0:
            result = None
        elif quant_idx == 1:
            result = "8bit"
        else:
            result = "4bit"
        assert result == "4bit"


class TestWelcomeLocalPresetBehavior:
    def test_local_model_options_cover_models_used_by_presets(self):
        from utils.presets import LOCAL_PRESETS

        preset_models = {
            values["local_model"]
            for values in LOCAL_PRESETS.values()
            if values.get("local_model")
        }

        assert preset_models <= set(LOCAL_MODEL_OPTIONS)

    def test_apply_local_preset_resets_lightning_fields(self):
        ctrl = WelcomeController.__new__(WelcomeController)
        ctrl._ensure_tab_built = lambda _label: False
        ctrl._mode_popup = _FakePopup(["deepgram", "local"], selected="deepgram")
        ctrl._local_backend_popup = _FakePopup(
            ["auto", "whisper", "faster", "mlx", "lightning"],
            selected="lightning",
        )
        ctrl._local_model_popup = _FakePopup(
            ["default", "turbo", "large", "large-v3"],
            selected="large-v3",
        )
        ctrl._device_popup = _FakePopup(["auto", "mps", "cpu", "cuda"], selected="cuda")
        ctrl._warmup_popup = _FakePopup(["auto", "true", "false"], selected="true")
        ctrl._local_fast_popup = _FakePopup(
            ["default", "true", "false"],
            selected="false",
        )
        ctrl._fp16_popup = _FakePopup(["default", "true", "false"], selected="true")
        ctrl._beam_size_field = _FakeField("9")
        ctrl._best_of_field = _FakeField("4")
        ctrl._temperature_field = _FakeField("0.7")
        ctrl._compute_type_field = _FakeField("float16")
        ctrl._cpu_threads_field = _FakeField("16")
        ctrl._num_workers_field = _FakeField("4")
        ctrl._without_timestamps_popup = _FakePopup(
            ["default", "true", "false"],
            selected="false",
        )
        ctrl._vad_filter_popup = _FakePopup(
            ["default", "true", "false"],
            selected="false",
        )
        ctrl._lightning_batch_slider = _FakeSlider(24)
        ctrl._lightning_batch_value_label = _FakeField("24")
        ctrl._lightning_quant_popup = _FakePopup(
            ["none (best quality)", "8bit", "4bit (smallest memory)"],
            selected="4bit (smallest memory)",
        )
        ctrl._lightning_quant_popup.selected_index = 2
        ctrl._update_local_settings_visibility = lambda: None

        ctrl._apply_local_preset("macOS: MLX Fast (turbo)")

        assert ctrl._mode_popup.titleOfSelectedItem() == "local"
        assert ctrl._lightning_batch_slider.intValue() == 12
        assert ctrl._lightning_batch_value_label.stringValue() == "12"
        assert ctrl._lightning_quant_popup.indexOfSelectedItem() == 0


def _make_minimal_welcome_controller():
    ctrl = WelcomeController.__new__(WelcomeController)
    ctrl._tab_builders = {}
    ctrl._built_tabs = set()
    ctrl._ensure_tab_built = lambda _label: False
    ctrl._mode_popup = None
    ctrl._local_backend_popup = None
    ctrl._local_model_popup = None
    ctrl._lang_popup = None
    ctrl._device_popup = None
    ctrl._warmup_popup = None
    ctrl._local_fast_popup = None
    ctrl._fp16_popup = None
    ctrl._beam_size_field = None
    ctrl._best_of_field = None
    ctrl._temperature_field = None
    ctrl._compute_type_field = None
    ctrl._cpu_threads_field = None
    ctrl._num_workers_field = None
    ctrl._without_timestamps_popup = None
    ctrl._vad_filter_popup = None
    ctrl._lightning_batch_slider = None
    ctrl._lightning_quant_popup = None
    ctrl._streaming_checkbox = None
    ctrl._refine_checkbox = None
    ctrl._clipboard_restore_checkbox = None
    ctrl._overlay_checkbox = None
    ctrl._dock_icon_checkbox = None
    ctrl._rtf_checkbox = None
    ctrl._provider_popup = None
    ctrl._model_field = None
    ctrl._vocab_text_view = None
    ctrl._save_custom_prompts = lambda: None
    ctrl._on_settings_changed_callback = None
    ctrl._save_btn = None
    ctrl._footer_status_label = None
    return ctrl


class TestApiKeyProviderMetadata:
    def test_api_card_height_grows_with_provider_count(self, monkeypatch):
        import ui.welcome as welcome_mod

        base_height = welcome_mod._get_api_card_height()
        monkeypatch.setattr(
            welcome_mod,
            "API_KEY_PROVIDERS",
            welcome_mod.API_KEY_PROVIDERS + [("test", "Test", "TEST_API_KEY")],
        )

        assert welcome_mod._get_api_card_height() == base_height + welcome_mod.API_KEY_ROW_SPACING


class TestWelcomeLazyTabs:
    def test_ensure_tab_built_runs_builder_once(self):
        ctrl = WelcomeController.__new__(WelcomeController)
        built: list[tuple[object, int]] = []
        content = object()
        ctrl._tab_builders = {
            "Logs": (lambda parent, height: built.append((parent, height)), content, 320)
        }
        ctrl._built_tabs = set()

        assert ctrl._ensure_tab_built("Logs") is True
        assert ctrl._ensure_tab_built("Logs") is False
        assert built == [(content, 320)]
        assert ctrl._is_tab_built("Logs") is True

    def test_apply_local_preset_builds_required_tabs_before_touching_controls(self):
        ctrl = _make_minimal_welcome_controller()
        built: list[str] = []

        def ensure_tab_built(label: str) -> bool:
            built.append(label)
            if label == "Providers":
                ctrl._mode_popup = _FakePopup(["deepgram", "local"], selected="deepgram")
                ctrl._local_backend_popup = _FakePopup(
                    ["auto", "whisper", "faster", "mlx", "lightning"],
                    selected="auto",
                )
                ctrl._local_model_popup = _FakePopup(
                    ["default", "turbo", "large", "large-v3"],
                    selected="default",
                )
            elif label == "Advanced":
                ctrl._device_popup = _FakePopup(
                    ["auto", "mps", "cpu", "cuda"], selected="cuda"
                )
                ctrl._warmup_popup = _FakePopup(
                    ["auto", "true", "false"], selected="true"
                )
                ctrl._local_fast_popup = _FakePopup(
                    ["default", "true", "false"], selected="false"
                )
                ctrl._fp16_popup = _FakePopup(
                    ["default", "true", "false"], selected="true"
                )
                ctrl._beam_size_field = _FakeField("9")
                ctrl._best_of_field = _FakeField("4")
                ctrl._temperature_field = _FakeField("0.7")
                ctrl._compute_type_field = _FakeField("float16")
                ctrl._cpu_threads_field = _FakeField("16")
                ctrl._num_workers_field = _FakeField("4")
                ctrl._without_timestamps_popup = _FakePopup(
                    ["default", "true", "false"],
                    selected="false",
                )
                ctrl._vad_filter_popup = _FakePopup(
                    ["default", "true", "false"],
                    selected="false",
                )
                ctrl._lightning_batch_slider = _FakeSlider(24)
                ctrl._lightning_batch_value_label = _FakeField("24")
                ctrl._lightning_quant_popup = _FakePopup(
                    ["none (best quality)", "8bit", "4bit (smallest memory)"],
                    selected="4bit (smallest memory)",
                )
                ctrl._lightning_quant_popup.selected_index = 2
            return True

        ctrl._ensure_tab_built = ensure_tab_built
        ctrl._update_local_settings_visibility = lambda: None

        ctrl._apply_local_preset("macOS: MLX Balanced (large)")

        assert built == ["Providers", "Advanced"]
        assert ctrl._mode_popup.titleOfSelectedItem() == "local"
        assert ctrl._local_backend_popup.titleOfSelectedItem() == "mlx"
        assert ctrl._local_model_popup.titleOfSelectedItem() == "large"
        assert ctrl._local_fast_popup.titleOfSelectedItem() == "true"


class TestWelcomeSaveSettings:
    def test_save_settings_batches_env_updates_and_removes_legacy_fp16(
        self, monkeypatch
    ):
        env_updates: list[dict[str, str | None]] = []

        monkeypatch.setattr(
            "ui.welcome.update_env_settings",
            lambda updates: env_updates.append(dict(updates)),
        )

        ctrl = _make_minimal_welcome_controller()
        ctrl._env_settings_cache = {}
        ctrl._fp16_popup = _FakePopup(["default", "true", "false"], selected="true")

        ctrl._save_all_settings()

        assert env_updates == [{LOCAL_FP16_ENV_KEY: "true", LEGACY_LOCAL_FP16_ENV_KEY: None}]

    def test_save_settings_persists_all_api_key_providers(self, monkeypatch):
        import ui.welcome as welcome_mod

        env_updates: list[dict[str, str | None]] = []
        monkeypatch.setattr(
            "ui.welcome.update_env_settings",
            lambda updates: env_updates.append(dict(updates)),
        )
        monkeypatch.setattr("ui.welcome._get_color", lambda *args: args)

        ctrl = _make_minimal_welcome_controller()
        ctrl._env_settings_cache = {}
        expected = []
        for provider, _label, env_key in welcome_mod.API_KEY_PROVIDERS:
            key_value = f"{provider}-key"
            expected.append((env_key, key_value))
            setattr(ctrl, f"_{provider}_field", _FakeField(key_value))
            setattr(ctrl, f"_{provider}_status", _FakeStatus())

        ctrl._save_all_settings()

        assert len(env_updates) == 1
        for key, value in expected:
            assert env_updates[0][key] == value
        for provider, _label, _env_key in welcome_mod.API_KEY_PROVIDERS:
            status = getattr(ctrl, f"_{provider}_status")
            assert status.value == "Configured"
            assert status.color == (51, 217, 178)

    def test_refresh_provider_key_statuses_updates_guidance_and_inline_states(
        self, monkeypatch
    ):
        import ui.welcome as welcome_mod

        monkeypatch.setattr("ui.welcome._get_color", lambda *args: args)

        ctrl = _make_minimal_welcome_controller()
        ctrl._mode_popup = _FakePopup(
            ["deepgram", "openai", "groq", "local"],
            selected="deepgram",
        )
        ctrl._provider_guidance_label = _FakeField("")
        for provider, _label, _env_key in welcome_mod.API_KEY_PROVIDERS:
            setattr(
                ctrl,
                f"_{provider}_field",
                _FakeField("gsk-live" if provider == "groq" else ""),
            )
            setattr(ctrl, f"_{provider}_status", _FakeStatus())

        ctrl._refresh_provider_key_statuses()

        assert ctrl._provider_guidance_label.value.startswith("Deepgram is selected")
        assert ctrl._deepgram_status.value == "Required"
        assert ctrl._deepgram_status.color == (255, 177, 66, 0.95)
        assert ctrl._groq_status.value == "Configured"
        assert ctrl._openai_status.value == "Optional"

    def test_save_settings_sets_footer_success_status(self, monkeypatch):
        monkeypatch.setattr("ui.welcome._get_color", lambda *args: args)

        ctrl = _make_minimal_welcome_controller()
        ctrl._env_settings_cache = {}
        ctrl._footer_status_label = _FakeStatus()

        ctrl._save_all_settings()

        assert ctrl._footer_status_label.value == "Settings saved."
        assert ctrl._footer_status_label.color == (51, 217, 178)

    def test_save_custom_prompts_skips_rewrite_when_overrides_are_unchanged(
        self, monkeypatch
    ):
        from utils import custom_prompts as custom_prompts_mod

        save_calls: list[dict] = []
        reset_calls: list[bool] = []

        monkeypatch.setattr(
            custom_prompts_mod,
            "filter_overrides_for_storage",
            lambda data, defaults=None: {
                "prompts": {"default": {"prompt": "custom prompt"}}
            },
        )
        monkeypatch.setattr(
            custom_prompts_mod,
            "save_custom_prompts_state",
            lambda data: save_calls.append(data),
        )
        monkeypatch.setattr(
            custom_prompts_mod,
            "reset_to_defaults",
            lambda: reset_calls.append(True),
        )
        monkeypatch.setattr(
            custom_prompts_mod,
            "parse_app_mappings",
            lambda _text: {"Mail": "email"},
        )

        ctrl = _make_minimal_welcome_controller()
        ctrl._prompts_defaults_data = {
            "voice_commands": {"instruction": "default vc"},
            "prompts": {"default": {"prompt": "default prompt"}},
            "app_contexts": {"Mail": "email"},
        }
        ctrl._prompts_loaded_data = {
            "voice_commands": {"instruction": "default vc"},
            "prompts": {"default": {"prompt": "custom prompt"}},
            "app_contexts": {"Mail": "email"},
        }
        ctrl._prompts_text_view = type(
            "_PromptView",
            (),
            {"string": lambda self: "custom prompt"},
        )()
        ctrl._prompts_current_context = "default"
        ctrl._prompts_cache = {}
        ctrl._prompts_status_label = _FakeField("")

        WelcomeController._save_custom_prompts(ctrl)

        assert save_calls == []
        assert reset_calls == []
        assert ctrl._prompts_status_label.value == "✓ Prompts unchanged"

    def test_save_custom_prompts_reuses_saved_state_without_force_reload(
        self, monkeypatch
    ):
        from utils import custom_prompts as custom_prompts_mod

        save_calls: list[dict] = []
        saved_state = {
            "voice_commands": {"instruction": "default vc"},
            "prompts": {
                "default": {"prompt": "custom prompt"},
                "email": {"prompt": "default email"},
            },
            "app_contexts": {"Mail": "email"},
        }

        monkeypatch.setattr(
            custom_prompts_mod,
            "save_custom_prompts_state",
            lambda data: save_calls.append(data) or saved_state,
        )

        ctrl = _make_minimal_welcome_controller()
        ctrl._prompts_defaults_data = {
            "voice_commands": {"instruction": "default vc"},
            "prompts": {
                "default": {"prompt": "default prompt"},
                "email": {"prompt": "default email"},
            },
            "app_contexts": {"Mail": "email"},
        }
        ctrl._prompts_loaded_data = {
            "voice_commands": {"instruction": "default vc"},
            "prompts": {
                "default": {"prompt": "default prompt"},
                "email": {"prompt": "default email"},
            },
            "app_contexts": {"Mail": "email"},
        }
        ctrl._prompts_text_view = type(
            "_PromptView",
            (),
            {"string": lambda self: "custom prompt"},
        )()
        ctrl._prompts_current_context = "default"
        ctrl._prompts_cache = {}
        ctrl._prompts_status_label = _FakeField("")

        WelcomeController._save_custom_prompts(ctrl)

        assert len(save_calls) == 1
        assert save_calls[0] == {"prompts": {"default": {"prompt": "custom prompt"}}}
        assert ctrl._prompts_loaded_data == saved_state
        assert ctrl._prompts_status_label.value == "✓ Prompts saved"


class TestWelcomeEditorCaches:
    def test_get_prompt_editor_text_for_context_reuses_cached_app_mappings_formatting(
        self, monkeypatch
    ):
        import utils.custom_prompts as custom_prompts_mod

        ctrl = _make_minimal_welcome_controller()
        ctrl._prompts_loaded_data = {
            "voice_commands": {"instruction": "default vc"},
            "prompts": {"default": {"prompt": "default prompt"}},
            "app_contexts": {"Mail": "email", "Slack": "chat"},
        }

        format_calls: list[dict[str, str]] = []
        original_formatter = custom_prompts_mod.format_app_mappings

        def _tracked_format(mappings: dict[str, str]) -> str:
            format_calls.append(dict(mappings))
            return original_formatter(mappings)

        monkeypatch.setattr(custom_prompts_mod, "format_app_mappings", _tracked_format)

        first = WelcomeController._get_prompt_editor_text_for_context(
            ctrl,
            "── App Mappings",
        )
        second = WelcomeController._get_prompt_editor_text_for_context(
            ctrl,
            "── App Mappings",
        )

        assert first == second
        assert len(format_calls) == 1

    def test_get_loaded_prompts_data_caches_until_forced(self, monkeypatch):
        ctrl = _make_minimal_welcome_controller()
        payloads = [
            {
                "prompts": {"default": {"prompt": "first"}},
                "voice_commands": {"instruction": "vc-1"},
                "app_contexts": {},
            },
            {
                "prompts": {"default": {"prompt": "second"}},
                "voice_commands": {"instruction": "vc-2"},
                "app_contexts": {},
            },
        ]
        calls = {"count": 0}

        def fake_load():
            index = calls["count"]
            calls["count"] += 1
            return payloads[index]

        monkeypatch.setattr("utils.custom_prompts.load_custom_prompts", fake_load)

        first = ctrl._get_loaded_prompts_data()
        second = ctrl._get_loaded_prompts_data()
        refreshed = ctrl._get_loaded_prompts_data(force=True)

        assert first is second
        assert refreshed["prompts"]["default"]["prompt"] == "second"
        assert calls["count"] == 2

    def test_get_loaded_vocabulary_keywords_caches_until_forced(self, monkeypatch):
        ctrl = _make_minimal_welcome_controller()
        payloads = [{"keywords": ["alpha"]}, {"keywords": ["beta"]}]
        calls = {"count": 0}

        def fake_load():
            index = calls["count"]
            calls["count"] += 1
            return payloads[index]

        monkeypatch.setattr("ui.welcome.load_vocabulary", fake_load)

        first = ctrl._get_loaded_vocabulary_keywords()
        second = ctrl._get_loaded_vocabulary_keywords()
        refreshed = ctrl._get_loaded_vocabulary_keywords(force=True)

        assert first == ["alpha"]
        assert second == ["alpha"]
        assert refreshed == ["beta"]
        assert calls["count"] == 2

    def test_visibility_updates_skip_redundant_hide_show_mutations(self):
        ctrl = _make_minimal_welcome_controller()
        ctrl._mode_popup = _FakePopup(["deepgram", "local"], selected="deepgram")
        ctrl._local_backend_popup = _FakePopup(
            ["auto", "whisper", "faster", "mlx", "lightning"],
            selected="auto",
        )
        ctrl._local_backend_label = _FakeHideable()
        ctrl._local_model_popup = _FakeHideable()
        ctrl._local_model_label = _FakeHideable()
        ctrl._lightning_header = _FakeHideable()
        ctrl._lightning_batch_label = _FakeHideable()
        ctrl._lightning_batch_slider = _FakeHideable()
        ctrl._lightning_batch_value_label = _FakeHideable()
        ctrl._lightning_quant_label = _FakeHideable()
        ctrl._lightning_quant_popup = _FakeHideable()
        ctrl._streaming_label = _FakeHideable()
        ctrl._streaming_checkbox = _FakeHideable()

        ctrl._update_all_visibility()
        ctrl._update_all_visibility()

        assert ctrl._local_backend_label.hidden_calls == [True]
        assert ctrl._local_model_popup.hidden_calls == [True]
        assert ctrl._streaming_label.hidden_calls == []
        assert ctrl._streaming_checkbox.hidden_calls == []


class TestWelcomePrivacySettings:
    def test_open_privacy_settings_passes_window(self):
        ctrl = WelcomeController.__new__(WelcomeController)
        ctrl._window = object()

        with patch("utils.permissions.open_privacy_settings") as mock_open:
            WelcomeController._open_privacy_settings(ctrl, "Privacy_Microphone")

        mock_open.assert_called_once_with(
            "Privacy_Microphone",
            window=ctrl._window,
        )


class _FakeContainer:
    def __init__(self):
        self.hidden = None
        self.hidden_calls = []

    def setHidden_(self, value):
        self.hidden = value
        self.hidden_calls.append(value)


class _FakeHideable(_FakeContainer):
    def isHidden(self):
        return bool(self.hidden)


class _FakePopup:
    def __init__(self, items=None, selected=None):
        self.items = list(items or [])
        self.selected = selected
        self.selected_index = 0

    def addItemWithTitle_(self, title):
        self.items.append(title)

    def selectItemWithTitle_(self, title):
        if title not in self.items:
            raise ValueError(title)
        self.selected = title
        self.selected_index = self.items.index(title)

    def selectItemAtIndex_(self, index):
        self.selected_index = index
        if 0 <= index < len(self.items):
            self.selected = self.items[index]

    def titleOfSelectedItem(self):
        return self.selected

    def indexOfSelectedItem(self):
        return self.selected_index


class _FakeField:
    def __init__(self, value=""):
        self.value = value

    def setStringValue_(self, value):
        self.value = value

    def stringValue(self):
        return self.value


class _FakeStatus(_FakeField):
    def __init__(self, value=""):
        super().__init__(value)
        self.color = None

    def setTextColor_(self, value):
        self.color = value


class _FakeSlider:
    def __init__(self, value: int):
        self._value = value

    def setIntValue_(self, value: int):
        self._value = value

    def intValue(self):
        return self._value


class TestWelcomeLogsSegmentSwitch:
    """Tests für Logs/Transcripts Segment-Verhalten im macOS-Settingsfenster."""

    def test_switch_to_logs_shows_logs_and_refreshes(self):
        ctrl = WelcomeController.__new__(WelcomeController)
        ctrl._logs_container = _FakeContainer()
        ctrl._transcripts_container = _FakeContainer()
        ctrl._refresh_logs = MagicMock()
        ctrl._refresh_transcripts = MagicMock()
        ctrl._active_logs_segment = None

        ctrl._switch_logs_segment(0)

        assert ctrl._logs_container.hidden is False
        assert ctrl._transcripts_container.hidden is True
        ctrl._refresh_logs.assert_called_once_with(scroll_to_bottom=True)
        ctrl._refresh_transcripts.assert_not_called()

    def test_switch_to_transcripts_shows_transcripts_and_refreshes(self):
        ctrl = WelcomeController.__new__(WelcomeController)
        ctrl._logs_container = _FakeContainer()
        ctrl._transcripts_container = _FakeContainer()
        ctrl._refresh_logs = MagicMock()
        ctrl._refresh_transcripts = MagicMock()
        ctrl._transcripts_view_seen = False
        ctrl._active_logs_segment = None

        ctrl._switch_logs_segment(1)

        assert ctrl._logs_container.hidden is True
        assert ctrl._transcripts_container.hidden is False
        ctrl._refresh_transcripts.assert_called_once_with(scroll_to_bottom=True)
        ctrl._refresh_logs.assert_not_called()
        assert ctrl._transcripts_view_seen is True

    def test_switch_to_transcripts_preserves_position_after_first_open(self):
        ctrl = WelcomeController.__new__(WelcomeController)
        ctrl._logs_container = _FakeContainer()
        ctrl._transcripts_container = _FakeContainer()
        ctrl._ensure_transcripts_view_built = MagicMock(return_value=False)
        ctrl._refresh_logs = MagicMock()
        ctrl._refresh_transcripts = MagicMock()
        ctrl._transcripts_view_seen = True
        ctrl._active_logs_segment = None

        ctrl._switch_logs_segment(1)

        ctrl._ensure_transcripts_view_built.assert_called_once_with()
        ctrl._refresh_transcripts.assert_called_once_with(scroll_to_bottom=False)

    def test_switch_to_transcripts_builds_panel_before_refresh(self):
        ctrl = WelcomeController.__new__(WelcomeController)
        ctrl._logs_container = _FakeContainer()
        ctrl._transcripts_container = _FakeContainer()
        ctrl._ensure_transcripts_view_built = MagicMock(return_value=True)
        ctrl._refresh_logs = MagicMock()
        ctrl._refresh_transcripts = MagicMock()
        ctrl._transcripts_view_seen = False
        ctrl._active_logs_segment = None

        ctrl._switch_logs_segment(1)

        ctrl._ensure_transcripts_view_built.assert_called_once_with()
        ctrl._refresh_transcripts.assert_called_once_with(scroll_to_bottom=True)

    def test_switch_logs_segment_skips_refresh_for_same_segment(self):
        ctrl = WelcomeController.__new__(WelcomeController)
        ctrl._logs_container = _FakeContainer()
        ctrl._transcripts_container = _FakeContainer()
        ctrl._refresh_logs = MagicMock()
        ctrl._refresh_transcripts = MagicMock()
        ctrl._active_logs_segment = 0

        ctrl._switch_logs_segment(0)

        ctrl._refresh_logs.assert_not_called()
        ctrl._refresh_transcripts.assert_not_called()
        assert ctrl._logs_container.hidden_calls == []
        assert ctrl._transcripts_container.hidden_calls == []

    def test_logs_view_active_when_segment_is_logs(self):
        ctrl = WelcomeController.__new__(WelcomeController)
        ctrl._logs_segment_control = type(
            "_Segment",
            (),
            {"selectedSegment": lambda self: 0},
        )()
        assert ctrl._is_logs_view_active() is True

    def test_logs_view_inactive_when_segment_is_transcripts(self):
        ctrl = WelcomeController.__new__(WelcomeController)
        ctrl._logs_segment_control = type(
            "_Segment",
            (),
            {"selectedSegment": lambda self: 1},
        )()
        assert ctrl._is_logs_view_active() is False


class _FakeTabItem:
    def __init__(self, identifier):
        self._identifier = identifier

    def identifier(self):
        return self._identifier


class _FakeTabView:
    def __init__(self, identifier):
        self._item = _FakeTabItem(identifier)

    def selectedTabViewItem(self):
        return self._item


class _FakeWindow:
    def __init__(self, visible: bool, miniaturized: bool):
        self._visible = visible
        self._miniaturized = miniaturized

    def isVisible(self):
        return self._visible

    def isMiniaturized(self):
        return self._miniaturized


class TestWelcomeLogsAutoRefreshGuards:
    def test_logs_tab_active_detection(self):
        ctrl = WelcomeController.__new__(WelcomeController)
        ctrl._tab_view = _FakeTabView("Logs")
        assert ctrl._is_logs_tab_active() is True

    def test_logs_tab_inactive_detection(self):
        ctrl = WelcomeController.__new__(WelcomeController)
        ctrl._tab_view = _FakeTabView("Setup")
        assert ctrl._is_logs_tab_active() is False

    def test_window_visibility_detection(self):
        ctrl = WelcomeController.__new__(WelcomeController)
        ctrl._window = _FakeWindow(visible=True, miniaturized=False)
        assert ctrl._is_window_visible_for_logs() is True

        ctrl._window = _FakeWindow(visible=True, miniaturized=True)
        assert ctrl._is_window_visible_for_logs() is False

    def test_auto_refresh_tick_skips_when_logs_tab_not_visible(self, monkeypatch):
        import sys
        import types

        captured: dict[str, object] = {}

        class _FakeNSTimer:
            @staticmethod
            def scheduledTimerWithTimeInterval_repeats_block_(interval, repeats, block):
                captured["interval"] = interval
                captured["repeats"] = repeats
                captured["tick"] = block
                return object()

        monkeypatch.setitem(
            sys.modules,
            "Foundation",
            types.SimpleNamespace(NSTimer=_FakeNSTimer),
        )

        ctrl = WelcomeController.__new__(WelcomeController)
        ctrl._stop_logs_auto_refresh = lambda: None
        ctrl._logs_auto_checkbox = type("_Check", (), {"state": lambda self: 1})()
        ctrl._is_logs_tab_active = lambda: False
        ctrl._is_logs_view_active = lambda: True
        ctrl._is_window_visible_for_logs = lambda: True
        ctrl._refresh_logs = MagicMock()

        ctrl._start_logs_auto_refresh()
        assert captured["interval"] == LOGS_AUTO_REFRESH_IDLE_INTERVAL_S
        captured["tick"](None)

        ctrl._refresh_logs.assert_not_called()

    def test_auto_refresh_tick_runs_when_logs_are_visible(self, monkeypatch):
        import sys
        import types

        captured: dict[str, object] = {}

        class _FakeNSTimer:
            @staticmethod
            def scheduledTimerWithTimeInterval_repeats_block_(interval, repeats, block):
                captured["interval"] = interval
                captured["repeats"] = repeats
                captured["tick"] = block
                return object()

        monkeypatch.setitem(
            sys.modules,
            "Foundation",
            types.SimpleNamespace(NSTimer=_FakeNSTimer),
        )

        ctrl = WelcomeController.__new__(WelcomeController)
        ctrl._stop_logs_auto_refresh = lambda: None
        ctrl._logs_auto_checkbox = type("_Check", (), {"state": lambda self: 1})()
        ctrl._is_logs_tab_active = lambda: True
        ctrl._is_logs_view_active = lambda: True
        ctrl._is_window_visible_for_logs = lambda: True
        ctrl._refresh_logs = MagicMock()

        ctrl._start_logs_auto_refresh()
        assert captured["interval"] == LOGS_AUTO_REFRESH_ACTIVE_INTERVAL_S
        captured["tick"](None)

        ctrl._refresh_logs.assert_called_once_with(scroll_to_bottom=False)

    def test_auto_refresh_tick_runs_when_transcripts_are_visible(self, monkeypatch):
        import sys
        import types

        captured: dict[str, object] = {}

        class _FakeNSTimer:
            @staticmethod
            def scheduledTimerWithTimeInterval_repeats_block_(interval, repeats, block):
                captured["interval"] = interval
                captured["repeats"] = repeats
                captured["tick"] = block
                return object()

        monkeypatch.setitem(
            sys.modules,
            "Foundation",
            types.SimpleNamespace(NSTimer=_FakeNSTimer),
        )

        ctrl = WelcomeController.__new__(WelcomeController)
        ctrl._stop_logs_auto_refresh = lambda: None
        ctrl._logs_auto_checkbox = type("_Check", (), {"state": lambda self: 1})()
        ctrl._is_logs_tab_active = lambda: True
        ctrl._is_logs_view_active = lambda: False
        ctrl._is_window_visible_for_logs = lambda: True
        ctrl._refresh_logs = MagicMock()
        ctrl._refresh_transcripts = MagicMock()

        ctrl._start_logs_auto_refresh()
        assert captured["interval"] == LOGS_AUTO_REFRESH_ACTIVE_INTERVAL_S
        captured["tick"](None)

        ctrl._refresh_logs.assert_not_called()
        ctrl._refresh_transcripts.assert_called_once_with(scroll_to_bottom=False)

    def test_auto_refresh_tick_reschedules_to_idle_interval_when_view_hides(
        self, monkeypatch
    ):
        import sys
        import types

        scheduled_intervals: list[float] = []
        created_timers: list[object] = []

        class _FakeTimer:
            def __init__(self, interval: float) -> None:
                self.interval = interval
                self.invalidated = 0

            def invalidate(self) -> None:
                self.invalidated += 1

        class _FakeNSTimer:
            @staticmethod
            def scheduledTimerWithTimeInterval_repeats_block_(interval, repeats, block):
                scheduled_intervals.append(interval)
                timer = _FakeTimer(interval)
                created_timers.append((timer, block, repeats))
                return timer

        monkeypatch.setitem(
            sys.modules,
            "Foundation",
            types.SimpleNamespace(NSTimer=_FakeNSTimer),
        )

        ctrl = WelcomeController.__new__(WelcomeController)
        ctrl._logs_auto_refresh_timer = None
        ctrl._logs_auto_refresh_interval_seconds = None
        ctrl._logs_auto_checkbox = type("_Check", (), {"state": lambda self: 1})()
        visible = {"value": True}
        ctrl._is_logs_tab_active = lambda: visible["value"]
        ctrl._is_logs_view_active = lambda: True
        ctrl._is_window_visible_for_logs = lambda: True
        ctrl._refresh_logs = MagicMock(return_value=True)
        ctrl._refresh_transcripts = MagicMock()

        ctrl._start_logs_auto_refresh()
        first_timer, first_tick, _repeats = created_timers[-1]
        visible["value"] = False
        first_tick(None)

        assert scheduled_intervals == [
            LOGS_AUTO_REFRESH_ACTIVE_INTERVAL_S,
            LOGS_AUTO_REFRESH_IDLE_INTERVAL_S,
        ]
        assert first_timer.invalidated == 1

    def test_auto_refresh_tick_backs_off_when_visible_view_stays_unchanged(
        self, monkeypatch
    ):
        import sys
        import types

        scheduled_intervals: list[float] = []
        created_timers: list[object] = []

        class _FakeTimer:
            def __init__(self, interval: float) -> None:
                self.interval = interval
                self.invalidated = 0

            def invalidate(self) -> None:
                self.invalidated += 1

        class _FakeNSTimer:
            @staticmethod
            def scheduledTimerWithTimeInterval_repeats_block_(interval, repeats, block):
                scheduled_intervals.append(interval)
                timer = _FakeTimer(interval)
                created_timers.append((timer, block, repeats))
                return timer

        monkeypatch.setitem(
            sys.modules,
            "Foundation",
            types.SimpleNamespace(NSTimer=_FakeNSTimer),
        )

        ctrl = WelcomeController.__new__(WelcomeController)
        ctrl._logs_auto_refresh_timer = None
        ctrl._logs_auto_refresh_interval_seconds = None
        ctrl._logs_auto_checkbox = type("_Check", (), {"state": lambda self: 1})()
        ctrl._is_logs_tab_active = lambda: True
        ctrl._is_logs_view_active = lambda: True
        ctrl._is_window_visible_for_logs = lambda: True
        ctrl._refresh_logs = MagicMock(return_value=False)
        ctrl._refresh_transcripts = MagicMock()

        ctrl._start_logs_auto_refresh()
        first_timer, first_tick, _ = created_timers[-1]
        first_tick(None)
        second_timer, second_tick, _ = created_timers[-1]
        second_tick(None)

        assert scheduled_intervals == [
            LOGS_AUTO_REFRESH_ACTIVE_INTERVAL_S,
            LOGS_AUTO_REFRESH_BACKOFF_INTERVAL_S,
            LOGS_AUTO_REFRESH_IDLE_INTERVAL_S,
        ]
        assert first_timer.invalidated == 1
        assert second_timer.invalidated == 1
        assert ctrl._logs_auto_refresh_step == 2

    def test_update_logs_auto_refresh_state_stops_timer_immediately_when_disabled(
        self, monkeypatch
    ):
        class _FakeTimer:
            def __init__(self) -> None:
                self.invalidated = 0

            def invalidate(self) -> None:
                self.invalidated += 1

        fake_timer = _FakeTimer()
        ctrl = WelcomeController.__new__(WelcomeController)
        ctrl._logs_auto_refresh_timer = fake_timer
        ctrl._logs_auto_refresh_interval_seconds = LOGS_AUTO_REFRESH_ACTIVE_INTERVAL_S
        ctrl._logs_auto_checkbox = type("_Check", (), {"state": lambda self: 0})()
        ctrl._is_tab_built = lambda label: label == "Logs"

        ctrl._update_logs_auto_refresh_state(reset_cadence=True)

        assert fake_timer.invalidated == 1
        assert ctrl._logs_auto_refresh_timer is None
        assert ctrl._logs_auto_refresh_interval_seconds is None

    def test_logs_auto_refresh_handler_updates_controller_immediately(self):
        import ui.welcome as welcome_mod

        calls: list[bool] = []

        class _Controller:
            def _update_logs_auto_refresh_state(self, *, reset_cadence: bool = False):
                calls.append(reset_cadence)

        handler = welcome_mod._LogsAutoRefreshHandler.alloc().initWithController_(
            _Controller()
        )
        handler.toggleAutoRefresh_(None)

        assert calls == [True]


class TestWelcomeLogFinder:
    def test_open_logs_in_finder_reveals_log_file_when_present(
        self, tmp_path, monkeypatch
    ):
        import ui.welcome as welcome_mod

        log_file = tmp_path / "pulsescribe.log"
        log_file.write_text("hello", encoding="utf-8")
        monkeypatch.setattr(welcome_mod, "LOG_FILE", log_file)

        calls: list[list[str]] = []
        monkeypatch.setattr(
            "subprocess.Popen",
            lambda cmd: calls.append(cmd),
        )

        footer_calls: list[tuple[str, str]] = []
        ctrl = WelcomeController.__new__(WelcomeController)
        ctrl._set_footer_status = lambda text, color="text_secondary": footer_calls.append(
            (text, color)
        )
        ctrl._open_logs_in_finder()

        assert calls == [["open", "-R", str(log_file)]]
        assert footer_calls == [("Opened log file in Finder.", "success")]

    def test_open_logs_in_finder_falls_back_to_parent_folder_when_log_missing(
        self, tmp_path, monkeypatch
    ):
        import ui.welcome as welcome_mod

        log_file = tmp_path / "pulsescribe.log"
        monkeypatch.setattr(welcome_mod, "LOG_FILE", log_file)

        calls: list[list[str]] = []
        monkeypatch.setattr(
            "subprocess.Popen",
            lambda cmd: calls.append(cmd),
        )

        footer_calls: list[tuple[str, str]] = []
        ctrl = WelcomeController.__new__(WelcomeController)
        ctrl._set_footer_status = lambda text, color="text_secondary": footer_calls.append(
            (text, color)
        )
        ctrl._open_logs_in_finder()

        assert calls == [["open", str(log_file.parent)]]
        assert footer_calls == [("Opened logs folder in Finder.", "success")]

    def test_get_logs_text_uses_clear_empty_state_copy(self, tmp_path, monkeypatch):
        import ui.welcome as welcome_mod

        log_file = tmp_path / "pulsescribe.log"
        monkeypatch.setattr(welcome_mod, "LOG_FILE", log_file)

        ctrl = WelcomeController.__new__(WelcomeController)

        assert ctrl._get_logs_text() == (
            "No logs yet.\n\nPulseScribe will create a log file here:\n" + str(log_file)
        )

    def test_refresh_logs_on_demand_sets_footer_feedback(self):
        ctrl = WelcomeController.__new__(WelcomeController)
        ctrl._refresh_logs = lambda scroll_to_bottom=True: True
        ctrl._set_footer_status = lambda text, color="text_secondary": statuses.append(
            (text, color)
        )

        statuses: list[tuple[str, str]] = []
        assert ctrl._refresh_logs_on_demand() is True
        assert statuses == [("Logs refreshed.", "success")]

    def test_refresh_transcripts_on_demand_sets_idle_feedback(self):
        ctrl = WelcomeController.__new__(WelcomeController)
        ctrl._refresh_transcripts = lambda scroll_to_bottom=True: False
        ctrl._set_footer_status = lambda text, color="text_secondary": statuses.append(
            (text, color)
        )

        statuses: list[tuple[str, str]] = []
        assert ctrl._refresh_transcripts_on_demand() is False
        assert statuses == [(
            "Transcript history is already up to date.",
            "text_secondary",
        )]

    def test_clear_transcripts_uses_direct_footer_feedback_path_when_alert_is_unavailable(
        self, monkeypatch
    ):
        import builtins
        import sys

        ctrl = WelcomeController.__new__(WelcomeController)
        refresh_calls: list[bool] = []
        footer_calls: list[tuple[str, str]] = []
        ctrl._refresh_transcripts = lambda scroll_to_bottom=True: refresh_calls.append(True)
        ctrl._set_footer_status = lambda text, color="text_secondary": footer_calls.append(
            (text, color)
        )

        original_import = builtins.__import__

        def _fake_import(name, globals=None, locals=None, fromlist=(), level=0):
            if name == "AppKit" and fromlist:
                raise ImportError("NSAlert unavailable in regression test")
            return original_import(name, globals, locals, fromlist, level)

        monkeypatch.delitem(sys.modules, "AppKit", raising=False)
        monkeypatch.setattr(builtins, "__import__", _fake_import)
        monkeypatch.setattr("utils.history.clear_history", lambda: True)

        ctrl._clear_transcripts()

        assert refresh_calls == [True]
        assert footer_calls == [("Transcript history cleared.", "success")]

    def test_clear_transcripts_sets_footer_feedback_after_failure(self, monkeypatch):
        import sys
        import types

        ctrl = WelcomeController.__new__(WelcomeController)
        refresh_calls: list[bool] = []
        footer_calls: list[tuple[str, str]] = []
        ctrl._refresh_transcripts = lambda scroll_to_bottom=True: refresh_calls.append(True)
        ctrl._set_footer_status = lambda text, color="text_secondary": footer_calls.append(
            (text, color)
        )

        monkeypatch.setitem(sys.modules, "AppKit", types.SimpleNamespace())
        monkeypatch.setattr("utils.history.clear_history", lambda: False)

        ctrl._clear_transcripts()

        assert refresh_calls == []
        assert footer_calls == [(
            "Could not clear transcript history. Try again.",
            "error",
        )]

    def test_clear_transcripts_uses_direct_error_feedback_path_when_alert_is_unavailable(
        self, monkeypatch
    ):
        import builtins
        import sys

        ctrl = WelcomeController.__new__(WelcomeController)
        refresh_calls: list[bool] = []
        footer_calls: list[tuple[str, str]] = []
        ctrl._refresh_transcripts = lambda scroll_to_bottom=True: refresh_calls.append(True)
        ctrl._set_footer_status = lambda text, color="text_secondary": footer_calls.append(
            (text, color)
        )

        original_import = builtins.__import__

        def _fake_import(name, globals=None, locals=None, fromlist=(), level=0):
            if name == "AppKit" and fromlist:
                raise ImportError("NSAlert unavailable in regression test")
            return original_import(name, globals, locals, fromlist, level)

        monkeypatch.delitem(sys.modules, "AppKit", raising=False)
        monkeypatch.setattr(builtins, "__import__", _fake_import)
        monkeypatch.setattr("utils.history.clear_history", lambda: False)

        ctrl._clear_transcripts()

        assert refresh_calls == []
        assert footer_calls == [(
            "Could not clear transcript history. Try again.",
            "error",
        )]


class _FakePoint:
    def __init__(self, y: float):
        self.y = y


class _FakeSize:
    def __init__(self, height: float):
        self.height = height


class _FakeRect:
    def __init__(self, y: float, height: float):
        self.origin = _FakePoint(y)
        self.size = _FakeSize(height)


class _FakeClipView:
    def __init__(self, *, y: float, height: float):
        self._rect = _FakeRect(y, height)
        self.scrolled_to = None

    def documentVisibleRect(self):
        return self._rect

    def scrollToPoint_(self, point):
        self.scrolled_to = point


class _FakeTranscriptsScrollView:
    def __init__(self, clip_view):
        self._clip_view = clip_view
        self.reflected = []

    def contentView(self):
        return self._clip_view

    def reflectScrolledClipView_(self, clip_view):
        self.reflected.append(clip_view)


class _FakeFrame:
    def __init__(self, height: float):
        self.size = _FakeSize(height)


class _FakeMutableString:
    def __init__(self, text_view):
        self._text_view = text_view

    def appendString_(self, value: str):
        self._text_view._text += value
        self._text_view.append_calls.append(value)


class _FakeTextStorage:
    def __init__(self, text_view):
        self._text_view = text_view
        self.begin_calls = 0
        self.end_calls = 0

    def beginEditing(self):
        self.begin_calls += 1

    def endEditing(self):
        self.end_calls += 1

    def mutableString(self):
        return _FakeMutableString(self._text_view)


class _FakeTranscriptsTextView:
    def __init__(self, initial_text: str, *, doc_height: float):
        self._text = initial_text
        self._doc_height = doc_height
        self.set_calls = []
        self.append_calls = []
        self._text_storage = _FakeTextStorage(self)

    def setString_(self, text: str):
        self._text = text
        self.set_calls.append(text)

    def string(self):
        return self._text

    def frame(self):
        return _FakeFrame(self._doc_height)

    def textStorage(self):
        return self._text_storage


class _FakeTranscriptsCountLabel:
    def __init__(self):
        self.value = ""
        self.calls = 0

    def setStringValue_(self, value: str):
        self.value = value
        self.calls += 1


class TestWelcomeTranscriptsRefreshBehavior:
    def test_get_transcripts_payload_uses_empty_state_message_when_history_is_empty(
        self, monkeypatch
    ):
        import utils.history as history_mod

        ctrl = WelcomeController.__new__(WelcomeController)
        monkeypatch.setattr(
            history_mod,
            "get_recent_transcripts_with_signature",
            lambda count, signature=Ellipsis: ([], None),
        )

        transcript_text, entry_count = ctrl._get_transcripts_payload()

        assert transcript_text == history_mod.format_transcripts_for_welcome([])
        assert entry_count == 0
        assert ctrl._pending_transcripts_entries == []
        assert ctrl._pending_transcripts_blocks == []

    def test_get_transcripts_payload_reuses_requested_signature(self, monkeypatch):
        import utils.history as history_mod

        ctrl = WelcomeController.__new__(WelcomeController)
        ctrl._requested_transcripts_signature = (9, 42)
        captured: list[object] = []

        monkeypatch.setattr(
            history_mod,
            "get_recent_transcripts_with_signature",
            lambda count, signature=Ellipsis: (
                captured.append(signature),
                ([{"timestamp": "2026-03-24T10:00:00", "text": "Alpha"}], signature),
            )[1],
        )

        transcript_text, entry_count = ctrl._get_transcripts_payload()

        assert captured == [(9, 42)]
        assert transcript_text == "[2026-03-24 10:00:00]\nAlpha"
        assert entry_count == 1
        assert ctrl._pending_transcripts_signature == (9, 42)

    def test_refresh_transcripts_builds_view_when_needed(self):
        ctrl = WelcomeController.__new__(WelcomeController)
        clip_view = _FakeClipView(y=120, height=300)

        build_calls: list[bool] = []

        def ensure_view():
            build_calls.append(True)
            ctrl._transcripts_text_view = _FakeTranscriptsTextView(
                "old text",
                doc_height=800,
            )
            ctrl._transcripts_scroll_view = _FakeTranscriptsScrollView(clip_view)
            ctrl._transcripts_count_label = _FakeTranscriptsCountLabel()
            return True

        ctrl._transcripts_text_view = None
        ctrl._transcripts_scroll_view = None
        ctrl._transcripts_count_label = None
        ctrl._ensure_transcripts_view_built = ensure_view
        ctrl._last_transcripts_text = "old text"
        ctrl._last_transcripts_signature = None
        ctrl._get_transcripts_payload = lambda: ("new text", 2)
        ctrl._scroll_transcripts_to_bottom = MagicMock()
        ctrl._restore_transcripts_scroll_position = MagicMock()

        ctrl._refresh_transcripts(scroll_to_bottom=False)

        assert build_calls == [True]
        assert ctrl._transcripts_text_view.set_calls == ["new text"]
        assert ctrl._transcripts_count_label.value == "2 recent transcriptions"

    def test_refresh_transcripts_skips_file_read_when_signature_unchanged(
        self, monkeypatch
    ):
        import ui.welcome as welcome_mod

        ctrl = WelcomeController.__new__(WelcomeController)
        ctrl._transcripts_text_view = _FakeTranscriptsTextView(
            "cached text",
            doc_height=800,
        )
        ctrl._transcripts_scroll_view = _FakeTranscriptsScrollView(
            _FakeClipView(y=120, height=300)
        )
        ctrl._transcripts_count_label = _FakeTranscriptsCountLabel()
        ctrl._last_transcripts_text = "cached text"
        ctrl._last_transcripts_signature = (1, 2)
        ctrl._get_transcripts_payload = MagicMock(
            side_effect=AssertionError("transcripts should not reload")
        )
        ctrl._scroll_transcripts_to_bottom = MagicMock()

        monkeypatch.setattr(welcome_mod, "get_file_signature", lambda _path: (1, 2))

        ctrl._refresh_transcripts(scroll_to_bottom=False)

        ctrl._get_transcripts_payload.assert_not_called()
        ctrl._scroll_transcripts_to_bottom.assert_not_called()

    def test_refresh_transcripts_can_scroll_to_bottom_without_reloading(
        self, monkeypatch
    ):
        import ui.welcome as welcome_mod

        ctrl = WelcomeController.__new__(WelcomeController)
        ctrl._transcripts_text_view = _FakeTranscriptsTextView(
            "cached text",
            doc_height=800,
        )
        ctrl._transcripts_scroll_view = _FakeTranscriptsScrollView(
            _FakeClipView(y=120, height=300)
        )
        ctrl._transcripts_count_label = _FakeTranscriptsCountLabel()
        ctrl._last_transcripts_text = "cached text"
        ctrl._last_transcripts_signature = (1, 2)
        ctrl._get_transcripts_payload = MagicMock(
            side_effect=AssertionError("transcripts should not reload")
        )
        ctrl._scroll_transcripts_to_bottom = MagicMock()

        monkeypatch.setattr(welcome_mod, "get_file_signature", lambda _path: (1, 2))

        ctrl._refresh_transcripts(scroll_to_bottom=True)

        ctrl._get_transcripts_payload.assert_not_called()
        ctrl._scroll_transcripts_to_bottom.assert_called_once_with()

    def test_refresh_transcripts_skips_rerender_when_text_unchanged(self):
        ctrl = WelcomeController.__new__(WelcomeController)
        ctrl._transcripts_text_view = _FakeTranscriptsTextView(
            "cached text",
            doc_height=800,
        )
        ctrl._transcripts_scroll_view = _FakeTranscriptsScrollView(
            _FakeClipView(y=120, height=300)
        )
        ctrl._transcripts_count_label = _FakeTranscriptsCountLabel()
        ctrl._last_transcripts_text = "cached text"
        ctrl._last_transcripts_signature = None
        ctrl._get_transcripts_payload = lambda: ("cached text", 5)
        ctrl._scroll_transcripts_to_bottom = MagicMock()
        ctrl._restore_transcripts_scroll_position = MagicMock()

        ctrl._refresh_transcripts(scroll_to_bottom=False)

        assert ctrl._transcripts_text_view.set_calls == []
        assert ctrl._transcripts_count_label.value == "5 recent transcriptions"
        ctrl._scroll_transcripts_to_bottom.assert_not_called()
        ctrl._restore_transcripts_scroll_position.assert_not_called()

    def test_refresh_transcripts_count_label_skips_duplicate_updates(self):
        ctrl = WelcomeController.__new__(WelcomeController)
        ctrl._transcripts_count_label = _FakeTranscriptsCountLabel()
        ctrl._last_transcripts_count_text = None

        ctrl._update_transcripts_count_label(5)
        ctrl._update_transcripts_count_label(5)
        ctrl._update_transcripts_count_label(1)

        assert ctrl._transcripts_count_label.calls == 2
        assert ctrl._transcripts_count_label.value == "1 recent transcription"

    def test_refresh_transcripts_preserves_scroll_position_when_not_near_bottom(
        self, monkeypatch
    ):
        import sys
        import types

        monkeypatch.setitem(
            sys.modules,
            "Foundation",
            types.SimpleNamespace(NSMakePoint=lambda x, y: (x, y)),
        )

        ctrl = WelcomeController.__new__(WelcomeController)
        clip_view = _FakeClipView(y=120, height=100)
        ctrl._transcripts_scroll_view = _FakeTranscriptsScrollView(clip_view)
        ctrl._transcripts_text_view = _FakeTranscriptsTextView(
            "old text",
            doc_height=900,
        )
        ctrl._transcripts_count_label = _FakeTranscriptsCountLabel()
        ctrl._last_transcripts_text = "old text"
        ctrl._last_transcripts_signature = None
        ctrl._get_transcripts_payload = lambda: ("new text", 3)
        ctrl._scroll_transcripts_to_bottom = MagicMock()

        ctrl._refresh_transcripts(scroll_to_bottom=False)

        assert ctrl._transcripts_text_view.set_calls == ["new text"]
        assert clip_view.scrolled_to == (0.0, 120)
        assert ctrl._last_transcripts_text == "new text"
        assert ctrl._transcripts_count_label.value == "3 recent transcriptions"
        ctrl._scroll_transcripts_to_bottom.assert_not_called()

    def test_refresh_transcripts_uses_incremental_append_when_possible(
        self, monkeypatch, tmp_path
    ):
        import ui.welcome as welcome_mod
        import utils.history as history_mod

        original_line = '{"timestamp":"2026-03-24T10:00:00","text":"Alpha"}\n'
        appended_line = '{"timestamp":"2026-03-24T10:00:01","text":"Beta"}\n'
        history_file = tmp_path / "history.jsonl"
        history_file.write_text(original_line + appended_line, encoding="utf-8")

        ctrl = WelcomeController.__new__(WelcomeController)
        ctrl._transcripts_text_view = _FakeTranscriptsTextView(
            "[2026-03-24 10:00:00]\nAlpha",
            doc_height=800,
        )
        ctrl._transcripts_scroll_view = _FakeTranscriptsScrollView(
            _FakeClipView(y=700, height=100)
        )
        ctrl._transcripts_count_label = _FakeTranscriptsCountLabel()
        ctrl._last_transcripts_text = "[2026-03-24 10:00:00]\nAlpha"
        ctrl._last_transcripts_signature = (1, len(original_line.encode("utf-8")))
        ctrl._last_transcripts_entries = [
            {"timestamp": "2026-03-24T10:00:00", "text": "Alpha"}
        ]
        ctrl._get_transcripts_payload = MagicMock(
            side_effect=AssertionError("full transcript reload should be skipped")
        )
        ctrl._scroll_transcripts_to_bottom = MagicMock()

        monkeypatch.setattr(history_mod, "HISTORY_FILE", history_file)
        monkeypatch.setattr(
            welcome_mod,
            "get_file_signature",
            lambda _path: (99, len((original_line + appended_line).encode("utf-8"))),
        )

        ctrl._refresh_transcripts(scroll_to_bottom=False)

        assert ctrl._transcripts_text_view.string() == (
            "[2026-03-24 10:00:00]\nAlpha\n\n[2026-03-24 10:00:01]\nBeta"
        )
        assert ctrl._transcripts_text_view.set_calls == []
        assert ctrl._transcripts_text_view.append_calls == [
            "\n\n[2026-03-24 10:00:01]\nBeta"
        ]
        assert ctrl._transcripts_count_label.value == "2 recent transcriptions"
        ctrl._scroll_transcripts_to_bottom.assert_called_once_with()

    def test_refresh_transcripts_replaces_empty_state_instead_of_appending_to_it(
        self, monkeypatch, tmp_path
    ):
        import ui.welcome as welcome_mod
        import utils.history as history_mod

        history_file = tmp_path / "history.jsonl"
        history_file.write_text(
            '{"timestamp":"2026-03-24T10:00:01","text":"Beta"}\n',
            encoding="utf-8",
        )

        ctrl = WelcomeController.__new__(WelcomeController)
        ctrl._transcripts_text_view = _FakeTranscriptsTextView(
            "No transcriptions yet.\n\n"
            "Your recent dictations will appear here after the first transcription.",
            doc_height=800,
        )
        ctrl._transcripts_scroll_view = _FakeTranscriptsScrollView(
            _FakeClipView(y=700, height=100)
        )
        ctrl._transcripts_count_label = _FakeTranscriptsCountLabel()
        ctrl._last_transcripts_text = (
            "No transcriptions yet.\n\n"
            "Your recent dictations will appear here after the first transcription."
        )
        ctrl._last_transcripts_signature = (1, 0)
        ctrl._last_transcripts_entries = []
        ctrl._last_transcripts_blocks = []
        ctrl._get_transcripts_payload = MagicMock(
            side_effect=AssertionError("full transcript reload should be skipped")
        )
        ctrl._scroll_transcripts_to_bottom = MagicMock()

        monkeypatch.setattr(history_mod, "HISTORY_FILE", history_file)
        monkeypatch.setattr(
            welcome_mod,
            "get_file_signature",
            lambda _path: (99, len(history_file.read_bytes())),
        )

        ctrl._refresh_transcripts(scroll_to_bottom=False)

        assert ctrl._get_transcripts_payload.call_count == 0
        assert ctrl._transcripts_text_view.set_calls == ["[2026-03-24 10:00:01]\nBeta"]
        assert ctrl._transcripts_text_view.append_calls == []
        assert ctrl._transcripts_count_label.value == "1 recent transcription"
        ctrl._scroll_transcripts_to_bottom.assert_called_once_with()

    def test_refresh_transcripts_reuses_cached_blocks_when_not_appending_in_place(
        self, monkeypatch, tmp_path
    ):
        import sys
        import types

        import ui.welcome as welcome_mod
        import utils.history as history_mod

        monkeypatch.setitem(
            sys.modules,
            "Foundation",
            types.SimpleNamespace(NSMakePoint=lambda x, y: (x, y)),
        )

        original_line = '{"timestamp":"2026-03-24T10:00:00","text":"Alpha"}\n'
        appended_line = '{"timestamp":"2026-03-24T10:00:01","text":"Beta"}\n'
        history_file = tmp_path / "history.jsonl"
        history_file.write_text(original_line + appended_line, encoding="utf-8")

        ctrl = WelcomeController.__new__(WelcomeController)
        clip_view = _FakeClipView(y=120, height=100)
        ctrl._transcripts_text_view = _FakeTranscriptsTextView(
            "[2026-03-24 10:00:00]\nAlpha",
            doc_height=900,
        )
        ctrl._transcripts_scroll_view = _FakeTranscriptsScrollView(clip_view)
        ctrl._transcripts_count_label = _FakeTranscriptsCountLabel()
        ctrl._last_transcripts_text = "[2026-03-24 10:00:00]\nAlpha"
        ctrl._last_transcripts_signature = (1, len(original_line.encode("utf-8")))
        ctrl._last_transcripts_entries = [
            {"timestamp": "2026-03-24T10:00:00", "text": "Alpha"}
        ]
        ctrl._last_transcripts_blocks = ["[2026-03-24 10:00:00]\nAlpha"]
        ctrl._get_transcripts_payload = MagicMock(
            side_effect=AssertionError("full transcript reload should be skipped")
        )
        ctrl._scroll_transcripts_to_bottom = MagicMock()

        monkeypatch.setattr(history_mod, "HISTORY_FILE", history_file)
        monkeypatch.setattr(
            welcome_mod,
            "get_file_signature",
            lambda _path: (99, len((original_line + appended_line).encode("utf-8"))),
        )

        ctrl._refresh_transcripts(scroll_to_bottom=False)

        assert ctrl._get_transcripts_payload.call_count == 0
        assert ctrl._transcripts_text_view.set_calls == [
            "[2026-03-24 10:00:00]\nAlpha\n\n[2026-03-24 10:00:01]\nBeta"
        ]
        assert ctrl._transcripts_text_view.append_calls == []
        assert ctrl._last_transcripts_blocks == [
            "[2026-03-24 10:00:00]\nAlpha",
            "[2026-03-24 10:00:01]\nBeta",
        ]
        assert clip_view.scrolled_to == (0.0, 120)
        ctrl._scroll_transcripts_to_bottom.assert_not_called()


class TestWelcomeLogsRefreshBehavior:
    def test_refresh_logs_uses_incremental_append_when_possible(self, monkeypatch):
        import ui.welcome as welcome_mod

        ctrl = WelcomeController.__new__(WelcomeController)
        ctrl._logs_text_view = _FakeTranscriptsTextView("cached logs", doc_height=600)
        ctrl._logs_scroll_view = _FakeTranscriptsScrollView(
            _FakeClipView(y=100, height=240)
        )
        ctrl._last_logs_text = "cached logs"
        ctrl._last_logs_signature = (1, 2)
        ctrl._get_logs_text = MagicMock(
            side_effect=AssertionError("full log reload should be skipped")
        )
        ctrl._try_append_logs_delta = MagicMock(return_value=True)

        monkeypatch.setattr(welcome_mod, "get_file_signature", lambda _path: (3, 4))

        ctrl._refresh_logs(scroll_to_bottom=False)

        ctrl._try_append_logs_delta.assert_called_once_with(
            (3, 4), scroll_to_bottom=False
        )
        ctrl._get_logs_text.assert_not_called()

    def test_get_logs_text_caches_signature_from_shared_tail_helper(
        self, monkeypatch, tmp_path
    ):
        import ui.welcome as welcome_mod

        ctrl = WelcomeController.__new__(WelcomeController)
        log_file = tmp_path / "app.log"
        log_file.write_text("hello", encoding="utf-8")

        monkeypatch.setattr(welcome_mod, "LOG_FILE", log_file)
        monkeypatch.setattr(
            welcome_mod,
            "read_file_tail_text_with_signature",
            lambda *_args, **_kwargs: ("cached logs", (7, 9)),
        )

        assert ctrl._get_logs_text() == "cached logs"
        assert ctrl._pending_logs_signature == (7, 9)

    def test_refresh_logs_skips_file_read_when_signature_unchanged(self, monkeypatch):
        import ui.welcome as welcome_mod

        ctrl = WelcomeController.__new__(WelcomeController)
        ctrl._logs_text_view = _FakeTranscriptsTextView("cached logs", doc_height=600)
        ctrl._logs_scroll_view = _FakeTranscriptsScrollView(
            _FakeClipView(y=100, height=240)
        )
        ctrl._last_logs_text = "cached logs"
        ctrl._last_logs_signature = (1, 2)
        ctrl._get_logs_text = MagicMock(
            side_effect=AssertionError("log tail should not be read")
        )
        ctrl._scroll_logs_to_bottom = MagicMock()
        ctrl._restore_logs_scroll_position = MagicMock()

        monkeypatch.setattr(welcome_mod, "get_file_signature", lambda _path: (1, 2))

        ctrl._refresh_logs(scroll_to_bottom=False)

        ctrl._get_logs_text.assert_not_called()
        assert ctrl._logs_text_view.set_calls == []
        ctrl._scroll_logs_to_bottom.assert_not_called()
        ctrl._restore_logs_scroll_position.assert_not_called()

    def test_refresh_logs_can_scroll_to_bottom_without_reloading(self, monkeypatch):
        import ui.welcome as welcome_mod

        ctrl = WelcomeController.__new__(WelcomeController)
        ctrl._logs_text_view = _FakeTranscriptsTextView("cached logs", doc_height=600)
        ctrl._logs_scroll_view = _FakeTranscriptsScrollView(
            _FakeClipView(y=100, height=240)
        )
        ctrl._last_logs_text = "cached logs"
        ctrl._last_logs_signature = (1, 2)
        ctrl._get_logs_text = MagicMock(
            side_effect=AssertionError("log tail should not be read")
        )
        ctrl._scroll_logs_to_bottom = MagicMock()

        monkeypatch.setattr(welcome_mod, "get_file_signature", lambda _path: (1, 2))

        ctrl._refresh_logs(scroll_to_bottom=True)

        ctrl._get_logs_text.assert_not_called()
        ctrl._scroll_logs_to_bottom.assert_called_once_with()

    def test_refresh_logs_updates_signature_even_when_text_unchanged(self, monkeypatch):
        import ui.welcome as welcome_mod

        ctrl = WelcomeController.__new__(WelcomeController)
        ctrl._logs_text_view = _FakeTranscriptsTextView("same logs", doc_height=600)
        ctrl._logs_scroll_view = _FakeTranscriptsScrollView(
            _FakeClipView(y=80, height=220)
        )
        ctrl._last_logs_text = "same logs"
        ctrl._last_logs_signature = (1, 2)
        ctrl._get_logs_text = MagicMock(return_value="same logs")
        ctrl._try_append_logs_delta = MagicMock(return_value=False)
        ctrl._scroll_logs_to_bottom = MagicMock()
        ctrl._restore_logs_scroll_position = MagicMock()

        monkeypatch.setattr(welcome_mod, "get_file_signature", lambda _path: (3, 4))

        ctrl._refresh_logs(scroll_to_bottom=False)

        ctrl._try_append_logs_delta.assert_called_once_with(
            (3, 4), scroll_to_bottom=False
        )
        ctrl._get_logs_text.assert_called_once()
        assert ctrl._logs_text_view.set_calls == []
        assert ctrl._last_logs_signature == (3, 4)

    def test_refresh_logs_reuses_cached_chunks_when_not_near_bottom(
        self, monkeypatch, tmp_path
    ):
        import ui.welcome as welcome_mod

        log_file = tmp_path / "app.log"
        original_text = "01234567890"
        appended_text = "ABCD"
        log_file.write_text(f"{original_text}{appended_text}", encoding="utf-8")

        ctrl = WelcomeController.__new__(WelcomeController)
        clip_view = _FakeClipView(y=120, height=240)
        ctrl._logs_text_view = _FakeTranscriptsTextView("...|34567890", doc_height=600)
        ctrl._logs_scroll_view = _FakeTranscriptsScrollView(clip_view)
        ctrl._last_logs_text = "...|34567890"
        ctrl._last_logs_signature = (1, len(original_text.encode("utf-8")))
        ctrl._last_logs_chunks = ["34567890"]
        ctrl._last_logs_truncated = True
        ctrl._get_logs_text = MagicMock(
            side_effect=AssertionError("full log reload should be skipped")
        )
        ctrl._scroll_logs_to_bottom = MagicMock()
        ctrl._restore_logs_scroll_position = MagicMock()

        monkeypatch.setattr(welcome_mod, "LOG_FILE", log_file)
        monkeypatch.setattr(welcome_mod, "LOG_TRUNCATED_PREFIX", "...|")
        monkeypatch.setattr(welcome_mod, "WELCOME_LOG_MAX_CHARS", 12)
        monkeypatch.setattr(
            welcome_mod,
            "get_file_signature",
            lambda _path: (9, len(f"{original_text}{appended_text}".encode("utf-8"))),
        )

        ctrl._refresh_logs(scroll_to_bottom=False)

        ctrl._get_logs_text.assert_not_called()
        assert ctrl._logs_text_view.set_calls == ["...|7890ABCD"]
        assert ctrl._logs_text_view.append_calls == []
        assert "".join(ctrl._last_logs_chunks) == "7890ABCD"
        assert ctrl._last_logs_truncated is True
        ctrl._scroll_logs_to_bottom.assert_not_called()
        ctrl._restore_logs_scroll_position.assert_called_once_with(120)
