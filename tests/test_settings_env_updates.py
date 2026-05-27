from utils.settings_env_updates import SettingsEnvUpdateBuilder


def test_builder_sets_common_optional_values() -> None:
    builder = SettingsEnvUpdateBuilder()

    builder.set_present("PULSESCRIBE_MODE", " local ")
    builder.set_optional("PULSESCRIBE_LANGUAGE", " auto ", remove_when={"auto"})
    builder.set_optional("PULSESCRIBE_DEVICE", " CPU ", remove_when={"auto"}, lower=True)
    builder.set_optional("PULSESCRIBE_LOCAL_MODEL", " default ", remove_when={"default"})

    assert builder.build() == {
        "PULSESCRIBE_MODE": "local",
        "PULSESCRIBE_LANGUAGE": None,
        "PULSESCRIBE_DEVICE": "cpu",
        "PULSESCRIBE_LOCAL_MODEL": None,
    }


def test_builder_handles_local_backend_and_bool_defaults() -> None:
    builder = SettingsEnvUpdateBuilder()

    builder.set_local_backend("PULSESCRIBE_LOCAL_BACKEND", "openai-whisper")
    builder.set_enabled_default_true("PULSESCRIBE_OVERLAY", True)
    builder.set_enabled_default_true("PULSESCRIBE_STREAMING", False)
    builder.set_enabled_default_false("PULSESCRIBE_SHOW_RTF", True)
    builder.set_bool_string("PULSESCRIBE_REFINE", False)

    assert builder.build() == {
        "PULSESCRIBE_LOCAL_BACKEND": "whisper",
        "PULSESCRIBE_OVERLAY": None,
        "PULSESCRIBE_STREAMING": "false",
        "PULSESCRIBE_SHOW_RTF": "true",
        "PULSESCRIBE_REFINE": "false",
    }


def test_builder_validates_optional_int_without_overwriting_invalid_values() -> None:
    builder = SettingsEnvUpdateBuilder()

    builder.set_optional_int("PULSESCRIBE_LOCAL_BEAM_SIZE", " 5 ")
    builder.set_optional_int("PULSESCRIBE_LOCAL_BEST_OF", "")
    builder.set_optional_int("PULSESCRIBE_LOCAL_CPU_THREADS", "fast")

    assert builder.build() == {
        "PULSESCRIBE_LOCAL_BEAM_SIZE": "5",
        "PULSESCRIBE_LOCAL_BEST_OF": None,
    }


def test_builder_normalizes_lightning_values() -> None:
    builder = SettingsEnvUpdateBuilder()

    builder.set_lightning_batch("PULSESCRIBE_LIGHTNING_BATCH_SIZE", 12)
    builder.set_lightning_quant_from_index("PULSESCRIBE_LIGHTNING_QUANT", 1)

    assert builder.build() == {
        "PULSESCRIBE_LIGHTNING_BATCH_SIZE": None,
        "PULSESCRIBE_LIGHTNING_QUANT": "8bit",
    }
