from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

import utils.preferences as prefs
from utils.onboarding import OnboardingStep


def _isolate_prefs(tmp_path, monkeypatch):
    monkeypatch.setattr(prefs, "PREFS_FILE", tmp_path / "preferences.json")
    monkeypatch.setattr(prefs, "ENV_FILE", tmp_path / ".env")
    prefs._env_cache = None
    prefs._prefs_cache = None


def test_read_env_file_refreshes_when_mtime_is_unchanged_but_size_changes(
    tmp_path, monkeypatch
):
    _isolate_prefs(tmp_path, monkeypatch)
    prefs.ENV_FILE.write_text("PULSESCRIBE_MODE=deepgram\n", encoding="utf-8")

    original_stat = Path.stat

    def _patched_stat(self: Path, *args, **kwargs):
        stat_result = original_stat(self, *args, **kwargs)
        if self == prefs.ENV_FILE:
            return SimpleNamespace(
                st_mtime=123.0,
                st_mtime_ns=123_000_000_000,
                st_size=stat_result.st_size,
                st_ctime=stat_result.st_ctime,
                st_ctime_ns=stat_result.st_ctime_ns,
            )
        return stat_result

    monkeypatch.setattr(Path, "stat", _patched_stat)

    first_read = prefs.read_env_file()
    assert first_read["PULSESCRIBE_MODE"] == "deepgram"

    prefs.ENV_FILE.write_text("PULSESCRIBE_MODE=local\n", encoding="utf-8")
    second_read = prefs.read_env_file()
    assert second_read["PULSESCRIBE_MODE"] == "local"


def test_save_api_key_is_noop_when_value_is_unchanged(tmp_path, monkeypatch):
    _isolate_prefs(tmp_path, monkeypatch)
    prefs.ENV_FILE.write_text("DEEPGRAM_API_KEY=dg-same\n", encoding="utf-8")

    original_write_text = Path.write_text
    write_calls = 0

    def _patched_write_text(self: Path, *args, **kwargs):
        nonlocal write_calls
        if self == prefs.ENV_FILE:
            write_calls += 1
        return original_write_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", _patched_write_text)

    prefs.save_api_key("DEEPGRAM_API_KEY", "dg-same")
    assert write_calls == 0


def test_save_api_key_invalidates_cache_when_same_value_hides_external_env_changes(
    tmp_path, monkeypatch
):
    _isolate_prefs(tmp_path, monkeypatch)
    prefs.ENV_FILE.write_text(
        "PULSESCRIBE_CONTEXT=old\nDEEPGRAM_API_KEY=dg-same\n",
        encoding="utf-8",
    )

    original_stat = Path.stat

    def _patched_stat(self: Path, *args, **kwargs):
        stat_result = original_stat(self, *args, **kwargs)
        if self == prefs.ENV_FILE:
            return SimpleNamespace(
                st_mtime=123.0,
                st_mtime_ns=123_000_000_000,
                st_size=stat_result.st_size,
                st_ctime=456.0,
                st_ctime_ns=456_000_000_000,
            )
        return stat_result

    monkeypatch.setattr(Path, "stat", _patched_stat)

    assert prefs.read_env_file()["PULSESCRIBE_CONTEXT"] == "old"

    prefs.ENV_FILE.write_text(
        "PULSESCRIBE_CONTEXT=new\nDEEPGRAM_API_KEY=dg-same\n",
        encoding="utf-8",
    )

    prefs.save_api_key("DEEPGRAM_API_KEY", "dg-same")

    assert prefs.read_env_file()["PULSESCRIBE_CONTEXT"] == "new"


def test_save_api_key_preserves_following_duplicate_assignments(tmp_path, monkeypatch):
    _isolate_prefs(tmp_path, monkeypatch)
    prefs.ENV_FILE.write_text(
        "# comment\nDEEPGRAM_API_KEY=old\nDEEPGRAM_API_KEY=legacy\nPULSESCRIBE_MODE=deepgram\n",
        encoding="utf-8",
    )

    prefs.save_api_key("DEEPGRAM_API_KEY", "new")

    assert prefs.ENV_FILE.read_text(encoding="utf-8") == (
        "# comment\nDEEPGRAM_API_KEY=new\nDEEPGRAM_API_KEY=legacy\nPULSESCRIBE_MODE=deepgram\n"
    )


def test_save_env_setting_preserves_following_duplicate_assignments(
    tmp_path, monkeypatch
):
    _isolate_prefs(tmp_path, monkeypatch)
    prefs.ENV_FILE.write_text(
        "# comment\nPULSESCRIBE_MODE=deepgram\nPULSESCRIBE_MODE=legacy\nUNCHANGED_KEY=keep\n",
        encoding="utf-8",
    )

    prefs.save_env_setting("PULSESCRIBE_MODE", "local")

    assert prefs.ENV_FILE.read_text(encoding="utf-8") == (
        "# comment\nPULSESCRIBE_MODE=local\nPULSESCRIBE_MODE=legacy\nUNCHANGED_KEY=keep\n"
    )


def test_remove_env_setting_is_noop_when_key_does_not_exist(tmp_path, monkeypatch):
    _isolate_prefs(tmp_path, monkeypatch)
    prefs.ENV_FILE.write_text("PULSESCRIBE_MODE=deepgram\n", encoding="utf-8")

    original_write_text = Path.write_text
    write_calls = 0

    def _patched_write_text(self: Path, *args, **kwargs):
        nonlocal write_calls
        if self == prefs.ENV_FILE:
            write_calls += 1
        return original_write_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", _patched_write_text)

    prefs.remove_env_setting("NOT_EXISTING_KEY")
    assert write_calls == 0


def test_save_env_setting_updates_spaced_assignment_without_creating_duplicate(
    tmp_path, monkeypatch
):
    _isolate_prefs(tmp_path, monkeypatch)
    prefs.ENV_FILE.write_text("PULSESCRIBE_MODE = deepgram\n", encoding="utf-8")

    prefs.save_env_setting("PULSESCRIBE_MODE", "local")

    assert prefs.ENV_FILE.read_text(encoding="utf-8") == "PULSESCRIBE_MODE=local\n"
    assert prefs.read_env_file()["PULSESCRIBE_MODE"] == "local"


def test_save_env_setting_updates_export_assignment_without_creating_duplicate(
    tmp_path, monkeypatch
):
    _isolate_prefs(tmp_path, monkeypatch)
    prefs.ENV_FILE.write_text(
        'export PULSESCRIBE_MODE = "deepgram"  # keep readable\nUNCHANGED_KEY=keep\n',
        encoding="utf-8",
    )

    prefs.save_env_setting("PULSESCRIBE_MODE", "local")

    assert prefs.ENV_FILE.read_text(encoding="utf-8") == (
        "PULSESCRIBE_MODE=local\nUNCHANGED_KEY=keep\n"
    )
    assert prefs.read_env_file()["PULSESCRIBE_MODE"] == "local"


def test_save_env_setting_same_export_assignment_is_noop_and_preserves_format(
    tmp_path, monkeypatch
):
    _isolate_prefs(tmp_path, monkeypatch)
    original_content = (
        'export PULSESCRIBE_MODE = "local"  # keep readable\nUNCHANGED_KEY=keep\n'
    )
    prefs.ENV_FILE.write_text(original_content, encoding="utf-8")

    monkeypatch.setattr(
        prefs,
        "_write_text_atomic",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("should not rewrite unchanged env")
        ),
    )

    prefs.save_env_setting("PULSESCRIBE_MODE", "local")

    assert prefs.ENV_FILE.read_text(encoding="utf-8") == original_content


def test_remove_env_setting_removes_spaced_assignment(tmp_path, monkeypatch):
    _isolate_prefs(tmp_path, monkeypatch)
    prefs.ENV_FILE.write_text(
        "PULSESCRIBE_MODE = deepgram\nPULSESCRIBE_LANGUAGE=en\n",
        encoding="utf-8",
    )

    prefs.remove_env_setting("PULSESCRIBE_MODE")

    assert prefs.ENV_FILE.read_text(encoding="utf-8") == "PULSESCRIBE_LANGUAGE=en\n"
    assert "PULSESCRIBE_MODE" not in prefs.read_env_file()


def test_remove_env_setting_removes_all_duplicate_assignments_for_key(
    tmp_path, monkeypatch
):
    _isolate_prefs(tmp_path, monkeypatch)
    prefs.ENV_FILE.write_text(
        "# comment\nPULSESCRIBE_MODE=deepgram\nPULSESCRIBE_MODE=legacy\nUNCHANGED_KEY=keep\n",
        encoding="utf-8",
    )

    prefs.remove_env_setting("PULSESCRIBE_MODE")

    assert prefs.ENV_FILE.read_text(encoding="utf-8") == "# comment\nUNCHANGED_KEY=keep\n"
    assert prefs.read_env_file() == {"UNCHANGED_KEY": "keep"}


def test_save_env_setting_tolerates_invalid_utf8_in_existing_env(
    tmp_path, monkeypatch
):
    _isolate_prefs(tmp_path, monkeypatch)
    prefs.ENV_FILE.write_bytes(
        b"PULSESCRIBE_MODE=deepgram\nBROKEN=\xff\nUNCHANGED_KEY=keep\n"
    )

    prefs.save_env_setting("PULSESCRIBE_MODE", "local")

    assert prefs.ENV_FILE.read_text(encoding="utf-8") == (
        "PULSESCRIBE_MODE=local\nBROKEN=�\nUNCHANGED_KEY=keep\n"
    )
    assert prefs.read_env_file()["PULSESCRIBE_MODE"] == "local"


def test_update_env_settings_batches_updates_in_single_atomic_write(tmp_path, monkeypatch):
    _isolate_prefs(tmp_path, monkeypatch)
    prefs.ENV_FILE.write_text(
        "PULSESCRIBE_MODE=deepgram\nPULSESCRIBE_LANGUAGE=en\n",
        encoding="utf-8",
    )

    write_calls: list[dict[str, str | None]] = []
    original_write = prefs._write_text_atomic

    def _patched_write(path, content, *, encoding="utf-8"):
        write_calls.append({"path": str(path), "content": content, "encoding": encoding})
        return original_write(path, content, encoding=encoding)

    monkeypatch.setattr(prefs, "_write_text_atomic", _patched_write)

    prefs.update_env_settings(
        {
            "PULSESCRIBE_MODE": "local",
            "PULSESCRIBE_LANGUAGE": None,
            "PULSESCRIBE_DEVICE": "cpu",
        }
    )

    assert len(write_calls) == 1
    assert prefs.ENV_FILE.read_text(encoding="utf-8") == (
        "PULSESCRIBE_MODE=local\nPULSESCRIBE_DEVICE=cpu\n"
    )


def test_update_env_settings_skips_write_when_target_values_are_already_current(
    tmp_path, monkeypatch
):
    _isolate_prefs(tmp_path, monkeypatch)
    prefs.ENV_FILE.write_text(
        "PULSESCRIBE_MODE=local\nPULSESCRIBE_DEVICE=cpu\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        prefs,
        "_write_text_atomic",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("should not rewrite unchanged env")
        ),
    )

    prefs.update_env_settings(
        {
            "PULSESCRIBE_MODE": "local",
            "PULSESCRIBE_DEVICE": "cpu",
        }
    )


def test_update_env_settings_preserves_comments_and_collapses_updated_duplicates(
    tmp_path, monkeypatch
):
    _isolate_prefs(tmp_path, monkeypatch)
    prefs.ENV_FILE.write_text(
        "# top\nPULSESCRIBE_MODE=deepgram\nPULSESCRIBE_MODE=groq\nUNCHANGED_KEY=keep\n# keep\nPULSESCRIBE_LANGUAGE=en\n",
        encoding="utf-8",
    )

    prefs.update_env_settings(
        {
            "PULSESCRIBE_MODE": "local",
            "PULSESCRIBE_LANGUAGE": None,
        }
    )

    assert prefs.ENV_FILE.read_text(encoding="utf-8") == (
        "# top\nPULSESCRIBE_MODE=local\nUNCHANGED_KEY=keep\n# keep\n"
    )


def test_update_env_settings_leaves_unrelated_duplicate_keys_intact(
    tmp_path, monkeypatch
):
    _isolate_prefs(tmp_path, monkeypatch)
    prefs.ENV_FILE.write_text(
        "PULSESCRIBE_MODE=deepgram\n"
        "UNCHANGED_KEY=first\n"
        "UNCHANGED_KEY=second\n"
        "PULSESCRIBE_LANGUAGE=en\n",
        encoding="utf-8",
    )

    prefs.update_env_settings(
        {
            "PULSESCRIBE_MODE": "local",
            "PULSESCRIBE_LANGUAGE": None,
        }
    )

    assert prefs.ENV_FILE.read_text(encoding="utf-8") == (
        "PULSESCRIBE_MODE=local\n"
        "UNCHANGED_KEY=first\n"
        "UNCHANGED_KEY=second\n"
    )


def test_update_env_settings_handles_export_assignments_for_updated_keys(
    tmp_path, monkeypatch
):
    _isolate_prefs(tmp_path, monkeypatch)
    prefs.ENV_FILE.write_text(
        'export PULSESCRIBE_MODE = "deepgram"\nUNCHANGED_KEY=keep\nexport PULSESCRIBE_DEVICE="auto"\n',
        encoding="utf-8",
    )

    prefs.update_env_settings(
        {
            "PULSESCRIBE_MODE": None,
            "PULSESCRIBE_DEVICE": "cpu",
        }
    )

    assert prefs.ENV_FILE.read_text(encoding="utf-8") == (
        "UNCHANGED_KEY=keep\nPULSESCRIBE_DEVICE=cpu\n"
    )


def test_update_env_settings_appends_new_keys_in_given_update_order(
    tmp_path, monkeypatch
):
    _isolate_prefs(tmp_path, monkeypatch)
    prefs.ENV_FILE.write_text("UNCHANGED_KEY=keep\n", encoding="utf-8")

    prefs.update_env_settings(
        {
            "PULSESCRIBE_MODE": "local",
            "PULSESCRIBE_DEVICE": "cpu",
            "PULSESCRIBE_LANGUAGE": "de",
        }
    )

    assert prefs.ENV_FILE.read_text(encoding="utf-8") == (
        "UNCHANGED_KEY=keep\n"
        "PULSESCRIBE_MODE=local\n"
        "PULSESCRIBE_DEVICE=cpu\n"
        "PULSESCRIBE_LANGUAGE=de\n"
    )


def test_save_api_key_reraises_write_errors(tmp_path, monkeypatch):
    _isolate_prefs(tmp_path, monkeypatch)
    prefs.ENV_FILE.write_text("DEEPGRAM_API_KEY=old\n", encoding="utf-8")

    monkeypatch.setattr(
        prefs,
        "_write_env_lines",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk full")),
    )

    with pytest.raises(OSError, match="disk full"):
        prefs.save_api_key("DEEPGRAM_API_KEY", "new")


def test_update_env_settings_reraises_write_errors(tmp_path, monkeypatch):
    _isolate_prefs(tmp_path, monkeypatch)
    prefs.ENV_FILE.write_text("PULSESCRIBE_MODE=deepgram\n", encoding="utf-8")

    monkeypatch.setattr(
        prefs,
        "_write_env_lines",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk full")),
    )

    with pytest.raises(OSError, match="disk full"):
        prefs.update_env_settings({"PULSESCRIBE_MODE": "local"})


def test_remove_env_setting_suppresses_write_errors(tmp_path, monkeypatch):
    _isolate_prefs(tmp_path, monkeypatch)
    prefs.ENV_FILE.write_text("PULSESCRIBE_MODE=deepgram\n", encoding="utf-8")

    monkeypatch.setattr(
        prefs,
        "_write_env_lines",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk full")),
    )

    prefs.remove_env_setting("PULSESCRIBE_MODE")

    assert prefs.ENV_FILE.read_text(encoding="utf-8") == "PULSESCRIBE_MODE=deepgram\n"


def test_read_env_file_parses_quoted_values_and_inline_comments(tmp_path, monkeypatch):
    _isolate_prefs(tmp_path, monkeypatch)
    prefs.ENV_FILE.write_text(
        '\n'.join(
            [
                'PULSESCRIBE_MODE="local"',
                "PULSESCRIBE_LANGUAGE='de'",
                'PULSESCRIBE_HOLD_HOTKEY="ctrl+win"  # keep quoted hotkey readable',
                'PULSESCRIBE_EMPTY=""',
                'PULSESCRIBE_MODE="deepgram"',
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    values = prefs.read_env_file()

    assert values["PULSESCRIBE_MODE"] == "local"
    assert values["PULSESCRIBE_LANGUAGE"] == "de"
    assert values["PULSESCRIBE_HOLD_HOTKEY"] == "ctrl+win"
    assert values["PULSESCRIBE_EMPTY"] == ""


def test_read_env_file_supports_export_prefix(tmp_path, monkeypatch):
    _isolate_prefs(tmp_path, monkeypatch)
    prefs.ENV_FILE.write_text(
        'export PULSESCRIBE_MODE="local"\nPULSESCRIBE_MODE=deepgram\n',
        encoding="utf-8",
    )

    assert prefs.read_env_file() == {"PULSESCRIBE_MODE": "local"}


def test_read_env_file_returns_empty_after_cached_env_file_is_deleted(
    tmp_path, monkeypatch
):
    _isolate_prefs(tmp_path, monkeypatch)
    prefs.ENV_FILE.write_text("PULSESCRIBE_MODE=deepgram\n", encoding="utf-8")

    assert prefs.read_env_file() == {"PULSESCRIBE_MODE": "deepgram"}

    prefs.ENV_FILE.unlink()

    assert prefs.read_env_file() == {}


def test_apply_hotkey_setting_updates_selected_key_and_removes_legacy_keys(
    tmp_path, monkeypatch
):
    _isolate_prefs(tmp_path, monkeypatch)
    prefs.ENV_FILE.write_text(
        "PULSESCRIBE_HOTKEY=f18\n"
        "PULSESCRIBE_HOTKEY_MODE=toggle\n"
        "PULSESCRIBE_TOGGLE_HOTKEY=f19\n",
        encoding="utf-8",
    )

    prefs.apply_hotkey_setting("hold", "Fn")

    values = prefs.read_env_file()
    assert values == {
        "PULSESCRIBE_TOGGLE_HOTKEY": "f19",
        "PULSESCRIBE_HOLD_HOTKEY": "fn",
    }


def test_apply_hotkey_setting_updates_toggle_and_preserves_hold_key(
    tmp_path, monkeypatch
):
    _isolate_prefs(tmp_path, monkeypatch)
    prefs.ENV_FILE.write_text(
        "PULSESCRIBE_HOTKEY=f18\n"
        "PULSESCRIBE_HOTKEY_MODE=toggle\n"
        "PULSESCRIBE_HOLD_HOTKEY=fn\n",
        encoding="utf-8",
    )

    prefs.apply_hotkey_setting("toggle", " F19 ")

    values = prefs.read_env_file()
    assert values == {
        "PULSESCRIBE_HOLD_HOTKEY": "fn",
        "PULSESCRIBE_TOGGLE_HOTKEY": "f19",
    }


def test_apply_hotkey_setting_unknown_kind_falls_back_to_toggle(
    tmp_path, monkeypatch
):
    _isolate_prefs(tmp_path, monkeypatch)

    prefs.apply_hotkey_setting("unexpected", " F20 ")

    assert prefs.read_env_file() == {"PULSESCRIBE_TOGGLE_HOTKEY": "f20"}


def test_load_preferences_returns_empty_dict_for_non_object_json(
    tmp_path, monkeypatch
):
    _isolate_prefs(tmp_path, monkeypatch)
    prefs.PREFS_FILE.write_text('["unexpected", "list"]', encoding="utf-8")

    assert prefs.load_preferences() == {}
    assert prefs.get_show_welcome_on_startup() is True


def test_bool_preferences_coerce_legacy_string_and_numeric_values(
    tmp_path, monkeypatch
):
    _isolate_prefs(tmp_path, monkeypatch)
    prefs.PREFS_FILE.write_text(
        '{"show_welcome_on_startup": "false", "has_seen_onboarding": "0"}',
        encoding="utf-8",
    )

    assert prefs.get_show_welcome_on_startup() is False
    assert prefs.has_seen_onboarding() is False
    assert prefs.get_onboarding_step() == OnboardingStep.CHOOSE_GOAL

    prefs.PREFS_FILE.write_text(
        '{"show_welcome_on_startup": 1, "has_seen_onboarding": "true"}',
        encoding="utf-8",
    )
    prefs._prefs_cache = None

    assert prefs.get_show_welcome_on_startup() is True
    assert prefs.has_seen_onboarding() is True
    assert prefs.get_onboarding_step() == OnboardingStep.DONE


def test_load_preferences_uses_cache_until_signature_changes(tmp_path, monkeypatch):
    _isolate_prefs(tmp_path, monkeypatch)
    prefs.PREFS_FILE.write_text(
        '{"show_welcome_on_startup": false}',
        encoding="utf-8",
    )

    original_read_text = Path.read_text
    read_calls = 0

    def _patched_read_text(self: Path, *args, **kwargs):
        nonlocal read_calls
        if self == prefs.PREFS_FILE:
            read_calls += 1
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", _patched_read_text)

    assert prefs.load_preferences()["show_welcome_on_startup"] is False
    assert prefs.load_preferences()["show_welcome_on_startup"] is False
    assert read_calls == 1


def test_load_preferences_refreshes_when_size_changes_even_if_timestamps_are_stable(
    tmp_path, monkeypatch
):
    _isolate_prefs(tmp_path, monkeypatch)
    prefs.PREFS_FILE.write_text(
        '{"show_welcome_on_startup": false}',
        encoding="utf-8",
    )

    original_stat = Path.stat

    def _patched_stat(self: Path, *args, **kwargs):
        stat_result = original_stat(self, *args, **kwargs)
        if self == prefs.PREFS_FILE:
            return SimpleNamespace(
                st_mtime=123.0,
                st_mtime_ns=123_000_000_000,
                st_size=stat_result.st_size,
                st_ctime=456.0,
                st_ctime_ns=456_000_000_000,
            )
        return stat_result

    monkeypatch.setattr(Path, "stat", _patched_stat)

    assert prefs.load_preferences()["show_welcome_on_startup"] is False

    prefs.PREFS_FILE.write_text('{"display_name": "Ada"}', encoding="utf-8")

    assert prefs.load_preferences() == {"display_name": "Ada"}


def test_load_preferences_returns_empty_after_cached_file_is_deleted(
    tmp_path, monkeypatch
):
    _isolate_prefs(tmp_path, monkeypatch)
    prefs.PREFS_FILE.write_text(
        '{"show_welcome_on_startup": false}',
        encoding="utf-8",
    )

    assert prefs.load_preferences()["show_welcome_on_startup"] is False

    prefs.PREFS_FILE.unlink()

    assert prefs.load_preferences() == {}
    assert prefs.get_show_welcome_on_startup() is True


def test_save_preferences_writes_utf8_atomically(tmp_path, monkeypatch):
    _isolate_prefs(tmp_path, monkeypatch)

    original_write_text = Path.write_text
    original_replace = Path.replace
    write_calls: list[tuple[Path, str | None]] = []
    replace_calls: list[tuple[Path, Path]] = []

    def _patched_write_text(self: Path, *args, **kwargs):
        write_calls.append((self, kwargs.get("encoding")))
        return original_write_text(self, *args, **kwargs)

    def _patched_replace(self: Path, target: Path):
        replace_calls.append((self, target))
        return original_replace(self, target)

    monkeypatch.setattr(Path, "write_text", _patched_write_text)
    monkeypatch.setattr(Path, "replace", _patched_replace)

    prefs.save_preferences({"display_name": "Müller"})

    assert prefs.load_preferences()["display_name"] == "Müller"
    assert all(path != prefs.PREFS_FILE for path, _encoding in write_calls)
    assert any(encoding == "utf-8" for _path, encoding in write_calls)
    assert replace_calls and replace_calls[-1][1] == prefs.PREFS_FILE
