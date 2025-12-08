"""
Gemeinsame Test-Fixtures für whisper_go.

Diese Fixtures isolieren Tests von externen Abhängigkeiten:
- Dateisystem (IPC-Dateien, Vocabulary)
- Umgebungsvariablen (API-Keys)
- Module-Level Caches
"""

import sys
from pathlib import Path

import pytest

# Projekt-Root zum Python-Path hinzufügen
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture
def mock_env(monkeypatch):
    """Setzt Test-API-Keys für isolierte Tests."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-openai")
    monkeypatch.setenv("DEEPGRAM_API_KEY", "test-key-deepgram")
    monkeypatch.setenv("GROQ_API_KEY", "test-key-groq")


@pytest.fixture
def temp_files(tmp_path, monkeypatch):
    """
    Ersetzt alle IPC-Dateipfade durch temporäre Verzeichnisse.

    Verhindert Konflikte mit laufenden whisper_go Instanzen
    und ermöglicht parallele Test-Ausführung.
    """
    import transcribe

    monkeypatch.setattr(transcribe, "PID_FILE", tmp_path / "test.pid")
    monkeypatch.setattr(transcribe, "STATE_FILE", tmp_path / "test.state")
    monkeypatch.setattr(transcribe, "TRANSCRIPT_FILE", tmp_path / "test.transcript")
    monkeypatch.setattr(transcribe, "ERROR_FILE", tmp_path / "test.error")
    monkeypatch.setattr(transcribe, "VOCABULARY_FILE", tmp_path / "vocab.json")

    return tmp_path


@pytest.fixture(autouse=True)
def reset_caches(monkeypatch):
    """
    Setzt Module-Level Caches vor jedem Test zurück.

    Wichtig für: _custom_app_contexts_cache (wird bei erstem Aufruf befüllt)
    """
    import transcribe

    monkeypatch.setattr(transcribe, "_custom_app_contexts_cache", None)


@pytest.fixture
def clean_env(monkeypatch):
    """Entfernt alle WHISPER_GO_* Umgebungsvariablen für saubere Tests."""
    import os

    for key in list(os.environ.keys()):
        if key.startswith("WHISPER_GO_"):
            monkeypatch.delenv(key, raising=False)
