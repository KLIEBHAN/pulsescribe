"""Tests für load_vocabulary() - Custom Vocabulary aus JSON laden."""

import json
from pathlib import Path
from types import SimpleNamespace

from transcribe import load_vocabulary
from utils.vocabulary import (
    load_vocabulary_state,
    save_vocabulary,
    save_vocabulary_state,
    validate_vocabulary,
)


class TestLoadVocabulary:
    """Tests für load_vocabulary() - JSON-Parsing mit Fallbacks."""

    def test_file_not_exists(self, temp_files):
        """Fehlende Datei gibt leere keywords zurück."""
        result = load_vocabulary()
        assert result == {"keywords": []}

    def test_valid_json(self, temp_files):
        """Gültiges JSON wird korrekt geparst."""
        vocab_file = temp_files / "vocab.json"
        vocab_file.write_text(json.dumps({"keywords": ["Claude", "Anthropic"]}))

        result = load_vocabulary()

        assert result == {"keywords": ["Claude", "Anthropic"]}

    def test_invalid_json(self, temp_files):
        """Ungültiges JSON gibt leere keywords zurück."""
        vocab_file = temp_files / "vocab.json"
        vocab_file.write_text("not valid json {")

        result = load_vocabulary()

        assert result == {"keywords": []}

    def test_invalid_utf8_returns_empty_keywords(self, temp_files):
        """Ungültiges UTF-8 wird robust abgefangen."""
        vocab_file = temp_files / "vocab.json"
        vocab_file.write_bytes(b'{"keywords":["\xff"]}')

        result = load_vocabulary()

        assert result == {"keywords": []}

    def test_keywords_wrong_type(self, temp_files):
        """keywords als String statt Liste wird zu leerer Liste korrigiert."""
        vocab_file = temp_files / "vocab.json"
        vocab_file.write_text(json.dumps({"keywords": "should-be-list"}))

        result = load_vocabulary()

        assert result == {"keywords": []}

    def test_non_object_json_returns_empty_keywords(self, temp_files):
        """Valides JSON mit falschem Root-Typ darf keinen Crash auslösen."""
        import utils.vocabulary as vocab

        vocab_file = temp_files / "vocab.json"
        vocab_file.write_text(json.dumps(["Claude", "Anthropic"]), encoding="utf-8")
        vocab._cache.clear()

        result = vocab.load_vocabulary(path=vocab_file)

        assert result == {"keywords": []}

    def test_missing_keywords_key(self, temp_files):
        """Fehlender keywords-Key wird zu leerer Liste."""
        vocab_file = temp_files / "vocab.json"
        vocab_file.write_text(json.dumps({"other": "data"}))

        result = load_vocabulary()

        # Validierung prüft nur ob keywords Liste ist, nicht ob Key existiert
        # → data.get("keywords") = None → isinstance(None, list) = False → []
        assert result["keywords"] == []

    def test_empty_keywords_list(self, temp_files):
        """Leere keywords-Liste bleibt erhalten."""
        vocab_file = temp_files / "vocab.json"
        vocab_file.write_text(json.dumps({"keywords": []}))

        result = load_vocabulary()

        assert result == {"keywords": []}

    def test_keywords_with_extra_fields(self, temp_files):
        """Zusätzliche Felder im JSON bleiben erhalten."""
        vocab_file = temp_files / "vocab.json"
        vocab_file.write_text(json.dumps({"keywords": ["test"], "extra": "preserved"}))

        result = load_vocabulary()

        assert result["keywords"] == ["test"]
        assert result.get("extra") == "preserved"

    def test_normalizes_keywords(self, temp_files):
        """Nicht-Strings, Leerzeichen und Duplikate werden normalisiert."""
        vocab_file = temp_files / "vocab.json"
        vocab_file.write_text(
            json.dumps({"keywords": ["  Foo ", "Bar", "Foo", 123, "", None]})
        )

        result = load_vocabulary()

        assert result["keywords"] == ["Foo", "Bar"]

    def test_normalizes_keywords_case_insensitively(self, temp_files):
        """Groß-/Kleinschreibung darf keine zusätzlichen Keyword-Slots verbrauchen."""
        vocab_file = temp_files / "vocab.json"
        vocab_file.write_text(
            json.dumps({"keywords": ["API", "api", "GraphQL", "graphql"]})
        )

        result = load_vocabulary()

        assert result["keywords"] == ["API", "GraphQL"]

    def test_load_uses_utf8_encoding(self, temp_files, monkeypatch):
        import utils.vocabulary as vocab

        vocab_file = temp_files / "vocab.json"
        vocab_file.write_text(
            json.dumps({"keywords": ["Müller", "東京"]}),
            encoding="utf-8",
        )
        vocab._cache.clear()

        original_read_text = Path.read_text
        encodings: list[str | None] = []

        def _patched_read_text(self: Path, *args, **kwargs):
            if self == vocab_file:
                encodings.append(kwargs.get("encoding"))
            return original_read_text(self, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", _patched_read_text)

        result = vocab.load_vocabulary(path=vocab_file)

        assert result["keywords"] == ["Müller", "東京"]
        assert encodings == ["utf-8"]

    def test_load_returns_defensive_copy_from_cache(self, temp_files):
        import utils.vocabulary as vocab

        vocab_file = temp_files / "vocab.json"
        vocab_file.write_text(
            json.dumps({"keywords": ["Alpha"], "extra": "keep"}),
            encoding="utf-8",
        )
        vocab._cache.clear()

        first = vocab.load_vocabulary(path=vocab_file)
        first["keywords"].append("Mutated")
        first["extra"] = "changed"

        second = vocab.load_vocabulary(path=vocab_file)

        assert second["keywords"] == ["Alpha"]
        assert second["extra"] == "keep"

    def test_load_vocabulary_state_returns_data_issues_and_signature(self, temp_files):
        vocab_file = temp_files / "vocab.json"
        vocab_file.write_text(
            json.dumps({"keywords": ["API", "api", "GraphQL"]}),
            encoding="utf-8",
        )

        data, issues, signature = load_vocabulary_state(path=vocab_file)

        assert data["keywords"] == ["API", "GraphQL"]
        assert any("doppelte" in issue for issue in issues)
        assert signature is not None


class TestSaveVocabulary:
    """Tests für save_vocabulary() - Custom Vocabulary persistieren."""

    def test_save_creates_file(self, temp_files):
        vocab_file = temp_files / "vocab.json"
        save_vocabulary(["alpha", "beta"], path=vocab_file)

        data = json.loads(vocab_file.read_text())
        assert data["keywords"] == ["alpha", "beta"]

        # Wrapper-Load sollte die neuen Keywords sehen
        assert load_vocabulary()["keywords"] == ["alpha", "beta"]

    def test_save_preserves_extra_fields(self, temp_files):
        vocab_file = temp_files / "vocab.json"
        vocab_file.write_text(json.dumps({"keywords": ["old"], "extra": "keep"}))

        save_vocabulary(["new"], path=vocab_file)

        data = json.loads(vocab_file.read_text())
        assert data["keywords"] == ["new"]
        assert data["extra"] == "keep"

    def test_save_normalizes_keywords_before_persisting(self, temp_files):
        import utils.vocabulary as vocab

        vocab_file = temp_files / "vocab.json"
        vocab_file.write_text(
            json.dumps({"keywords": ["old"], "extra": "keep"}),
            encoding="utf-8",
        )
        vocab._cache.clear()

        save_vocabulary(["  API ", "api", "GraphQL", "graphql"], path=vocab_file)

        data = json.loads(vocab_file.read_text(encoding="utf-8"))
        assert data == {"keywords": ["API", "GraphQL"], "extra": "keep"}
        assert vocab.load_vocabulary(path=vocab_file) == data

    def test_save_uses_atomic_write_helper_with_utf8_encoding(self, temp_files, monkeypatch):
        import utils.vocabulary as vocab

        vocab_file = temp_files / "vocab.json"
        atomic_calls: list[tuple[Path, str]] = []

        def _patched_atomic_write(path: Path, content: str, *, encoding: str) -> None:
            atomic_calls.append((path, encoding))
            path.write_text(content, encoding=encoding)

        monkeypatch.setattr(vocab, "write_text_atomic", _patched_atomic_write)

        save_vocabulary(["Müller", "東京"], path=vocab_file)

        assert atomic_calls == [(vocab_file, "utf-8")]

    def test_save_skips_noop_rewrite_when_keywords_are_unchanged(self, temp_files, monkeypatch):
        import utils.vocabulary as vocab

        vocab_file = temp_files / "vocab.json"
        vocab_file.write_text(
            json.dumps({"keywords": ["Alpha", "Beta"], "extra": "keep"}),
            encoding="utf-8",
        )

        atomic_calls: list[Path] = []

        def _patched_atomic_write(path: Path, content: str, *, encoding: str) -> None:
            atomic_calls.append(path)
            path.write_text(content, encoding=encoding)

        monkeypatch.setattr(vocab, "write_text_atomic", _patched_atomic_write)

        save_vocabulary(["Alpha", "Beta"], path=vocab_file)

        assert atomic_calls == []

    def test_save_noop_refreshes_stale_cache_without_rewriting(
        self, temp_files, monkeypatch
    ):
        import utils.vocabulary as vocab

        vocab_file = temp_files / "vocab.json"
        vocab_file.write_text(
            json.dumps({"keywords": ["Alpha"], "extra": "keep"}),
            encoding="utf-8",
        )
        signature = vocab._file_signature(vocab_file)
        vocab._cache[vocab_file] = (
            signature,
            {"keywords": ["Stale"], "extra": "wrong"},
            ["stale"],
        )

        monkeypatch.setattr(
            vocab,
            "write_text_atomic",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError("unchanged save should not rewrite the file")
            ),
        )

        save_vocabulary(["Alpha"], path=vocab_file)

        assert vocab.load_vocabulary(path=vocab_file) == {
            "keywords": ["Alpha"],
            "extra": "keep",
        }

    def test_save_vocabulary_state_reuses_current_cached_json_without_rereading_file(
        self, temp_files, monkeypatch
    ):
        import utils.vocabulary as vocab

        vocab_file = temp_files / "vocab.json"
        vocab_file.write_text(
            json.dumps({"keywords": ["Alpha"], "extra": "keep"}),
            encoding="utf-8",
        )
        signature = vocab._file_signature(vocab_file)
        assert vocab.load_vocabulary(path=vocab_file) == {
            "keywords": ["Alpha"],
            "extra": "keep",
        }
        assert vocab._trusted_cache_signatures[vocab_file] == signature

        original_read_text = Path.read_text

        def _patched_read_text(self: Path, *args, **kwargs):
            if self == vocab_file:
                raise AssertionError("cached save should not reread the JSON file")
            return original_read_text(self, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", _patched_read_text)
        monkeypatch.setattr(
            vocab,
            "write_text_atomic",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError("unchanged save should not rewrite the file")
            ),
        )

        data, issues, returned_signature = save_vocabulary_state(
            ["Alpha"],
            path=vocab_file,
        )

        assert data == {"keywords": ["Alpha"], "extra": "keep"}
        assert issues == []
        assert returned_signature == signature


class TestValidateVocabulary:
    """Tests für validate_vocabulary()."""

    def test_validate_invalid_json(self, temp_files):
        vocab_file = temp_files / "vocab.json"
        vocab_file.write_text("not-json")
        issues = validate_vocabulary(path=vocab_file)
        assert issues and "JSON" in issues[0]

    def test_validate_too_many_keywords(self, temp_files):
        vocab_file = temp_files / "vocab.json"
        vocab_file.write_text(json.dumps({"keywords": [f"k{i}" for i in range(120)]}))
        issues = validate_vocabulary(path=vocab_file)
        assert any("Deepgram" in i for i in issues)

    def test_validate_reports_case_variant_duplicates(self, temp_files):
        vocab_file = temp_files / "vocab.json"
        vocab_file.write_text(
            json.dumps({"keywords": ["API", "api", "GraphQL", "graphql"]})
        )

        issues = validate_vocabulary(path=vocab_file)

        assert any("2 doppelte keywords" in issue.lower() for issue in issues)

    def test_validate_uses_utf8_encoding(self, temp_files, monkeypatch):
        vocab_file = temp_files / "vocab.json"
        vocab_file.write_text(
            json.dumps({"keywords": ["Müller", "東京"]}),
            encoding="utf-8",
        )

        original_read_text = Path.read_text
        encodings: list[str | None] = []

        def _patched_read_text(self: Path, *args, **kwargs):
            if self == vocab_file:
                encodings.append(kwargs.get("encoding"))
            return original_read_text(self, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", _patched_read_text)

        issues = validate_vocabulary(path=vocab_file)

        assert issues == []
        assert encodings == ["utf-8"]

    def test_validate_reuses_cached_load_without_rereading_file(self, temp_files, monkeypatch):
        import utils.vocabulary as vocab

        vocab_file = temp_files / "vocab.json"
        vocab_file.write_text(
            json.dumps({"keywords": ["API", "api", "GraphQL"]}),
            encoding="utf-8",
        )
        vocab._cache.clear()

        original_read_text = Path.read_text
        read_calls: list[Path] = []

        def _patched_read_text(self: Path, *args, **kwargs):
            if self == vocab_file:
                read_calls.append(self)
            return original_read_text(self, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", _patched_read_text)

        loaded = vocab.load_vocabulary(path=vocab_file)
        issues = vocab.validate_vocabulary(path=vocab_file)

        assert loaded["keywords"] == ["API", "GraphQL"]
        assert any("doppelte" in issue for issue in issues)
        assert read_calls == [vocab_file]


class TestVocabularyCaching:
    """Tests für robuste Cache-Invalidierung."""

    def test_load_vocabulary_refreshes_when_mtime_is_unchanged_but_size_changes(
        self, tmp_path, monkeypatch
    ):
        import utils.vocabulary as vocab

        vocab_file = tmp_path / "vocab.json"
        vocab_file.write_text(json.dumps({"keywords": ["alpha"]}), encoding="utf-8")
        vocab._cache.clear()

        original_stat = Path.stat

        def _patched_stat(self: Path):
            stat_result = original_stat(self)
            if self == vocab_file:
                return SimpleNamespace(
                    st_mtime=123.0,
                    st_mtime_ns=123_000_000_000,
                    st_size=stat_result.st_size,
                    st_ctime=stat_result.st_ctime,
                    st_ctime_ns=stat_result.st_ctime_ns,
                )
            return stat_result

        monkeypatch.setattr(Path, "stat", _patched_stat)

        first = vocab.load_vocabulary(path=vocab_file)
        assert first["keywords"] == ["alpha"]

        vocab_file.write_text(
            json.dumps({"keywords": ["alpha", "beta"]}),
            encoding="utf-8",
        )
        second = vocab.load_vocabulary(path=vocab_file)

        assert second["keywords"] == ["alpha", "beta"]
