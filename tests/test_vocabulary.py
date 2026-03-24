"""Tests für load_vocabulary() - Custom Vocabulary aus JSON laden."""

import json
from pathlib import Path
from types import SimpleNamespace

from transcribe import load_vocabulary
from utils.vocabulary import save_vocabulary, validate_vocabulary


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

    def test_keywords_wrong_type(self, temp_files):
        """keywords als String statt Liste wird zu leerer Liste korrigiert."""
        vocab_file = temp_files / "vocab.json"
        vocab_file.write_text(json.dumps({"keywords": "should-be-list"}))

        result = load_vocabulary()

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
