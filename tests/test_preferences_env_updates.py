"""Contract shared by single-setting saves and batch `.env` updates."""

import pytest

import utils.preferences as prefs


@pytest.mark.parametrize("batch", [False, True], ids=["single", "batch"])
@pytest.mark.parametrize(
    "original,value,single_result,batch_result",
    [
        pytest.param(
            'export MODE = "local" # keep\nMODE=legacy\n',
            "local",
            'export MODE = "local" # keep\nMODE=legacy\n',
            "MODE=local\n",
            id="unchanged-value-with-formatting-and-duplicate",
        ),
        pytest.param(
            "MODE=local\nMODE=legacy\n",
            "local",
            "MODE=local\nMODE=legacy\n",
            "MODE=local\n",
            id="unchanged-first-value-with-duplicate",
        ),
        pytest.param(
            'export MODE = "old"\n# keep\nMODE=legacy\n',
            "local",
            "MODE=local\n# keep\nMODE=legacy\n",
            "MODE=local\n# keep\n",
            id="changed-first-value-with-duplicate",
        ),
        pytest.param(
            "MODE=old\nMODE=legacy\n",
            "",
            "MODE=\nMODE=legacy\n",
            "MODE=\n",
            id="empty-value-is-not-deletion",
        ),
        pytest.param(
            'export MODE = "" # keep\n',
            "",
            'export MODE = "" # keep\n',
            "MODE=\n",
            id="unchanged-empty-value",
        ),
        pytest.param(
            "# keep\nMODE=old\nMODE=legacy\nOTHER=first\nOTHER=second\n",
            None,
            "# keep\nOTHER=first\nOTHER=second\n",
            "# keep\nOTHER=first\nOTHER=second\n",
            id="deletion-removes-all-matching-duplicates",
        ),
        pytest.param(
            "# keep\nOTHER=first\nOTHER=second\n",
            None,
            "# keep\nOTHER=first\nOTHER=second\n",
            "# keep\nOTHER=first\nOTHER=second\n",
            id="absent-deletion-is-noop",
        ),
        pytest.param(
            "# keep\nOTHER=value\n",
            "",
            "# keep\nOTHER=value\nMODE=\n",
            "# keep\nOTHER=value\nMODE=\n",
            id="append-empty-value",
        ),
    ],
)
def test_env_update_contract(
    tmp_path, monkeypatch, batch, original, value, single_result, batch_result
):
    env_path = tmp_path / ".env"
    env_path.write_text(original, encoding="utf-8")
    monkeypatch.setattr(prefs, "ENV_FILE", env_path)
    monkeypatch.setattr(prefs, "_env_cache", None)
    writes = []
    original_write = prefs._write_text_atomic

    def track_write(path, content, *, encoding="utf-8"):
        writes.append(content)
        original_write(path, content, encoding=encoding)

    monkeypatch.setattr(prefs, "_write_text_atomic", track_write)

    if batch:
        prefs.update_env_settings({"MODE": value})
    elif value is None:
        prefs.remove_env_setting("MODE")
    else:
        prefs.save_env_setting("MODE", value)

    expected = batch_result if batch else single_result
    assert env_path.read_text(encoding="utf-8") == expected
    assert writes == ([] if expected == original else [expected])


def test_batch_update_writes_when_appending_key_recreates_removed_line(
    tmp_path, monkeypatch
):
    env_path = tmp_path / ".env"
    env_path.write_text("export MODE=local", encoding="utf-8")
    monkeypatch.setattr(prefs, "ENV_FILE", env_path)
    monkeypatch.setattr(prefs, "_env_cache", None)

    prefs.update_env_settings({"MODE": None, "export MODE": "local"})

    assert env_path.read_text(encoding="utf-8") == "export MODE=local\n"
