from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import utils.preferences as prefs


def _isolate_prefs(tmp_path, monkeypatch):
    monkeypatch.setattr(prefs, "PREFS_FILE", tmp_path / "preferences.json")
    monkeypatch.setattr(prefs, "ENV_FILE", tmp_path / ".env")
    prefs._env_cache = None


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


def test_remove_env_setting_removes_spaced_assignment(tmp_path, monkeypatch):
    _isolate_prefs(tmp_path, monkeypatch)
    prefs.ENV_FILE.write_text(
        "PULSESCRIBE_MODE = deepgram\nPULSESCRIBE_LANGUAGE=en\n",
        encoding="utf-8",
    )

    prefs.remove_env_setting("PULSESCRIBE_MODE")

    assert prefs.ENV_FILE.read_text(encoding="utf-8") == "PULSESCRIBE_LANGUAGE=en\n"
    assert "PULSESCRIBE_MODE" not in prefs.read_env_file()


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


def test_load_preferences_returns_empty_dict_for_non_object_json(
    tmp_path, monkeypatch
):
    _isolate_prefs(tmp_path, monkeypatch)
    prefs.PREFS_FILE.write_text('["unexpected", "list"]', encoding="utf-8")

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
