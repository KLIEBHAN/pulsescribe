"""Tests für load_vocabulary() - Custom Vocabulary aus JSON laden."""

import json

from transcribe import load_vocabulary


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
