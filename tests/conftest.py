"""
Gemeinsame Test-Fixtures für pulsescribe.

Diese Fixtures isolieren Tests von externen Abhängigkeiten:
- Dateisystem (IPC-Dateien, Vocabulary)
- Umgebungsvariablen (API-Keys)
- Module-Level Caches

Shared Mock-Fixtures für häufig genutzte Objekte:
- deepgram_response: Standard Deepgram API Response
- mock_args: Factory für CLI-Argument Mocks
"""

import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

# Projekt-Root zum Python-Path hinzufügen
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# =============================================================================
# Shared Mock-Fixtures
# =============================================================================


@pytest.fixture
def deepgram_response():
    """
    Standard Deepgram API Response Mock.

    Struktur: result.channel.alternatives[0].transcript
    Wenn sich die Deepgram-API ändert, nur hier anpassen.
    """

    def _create(transcript: str = "Test transcript"):
        result = Mock()
        result.channel = Mock()
        result.channel.alternatives = [Mock(transcript=transcript)]
        return result

    return _create


@pytest.fixture
def mock_args():
    """
    Factory für CLI-Argument Mocks.

    Usage:
        args = mock_args(mode="deepgram", refine=True)
    """

    def _create(**kwargs):
        defaults = {
            "mode": "openai",
            "refine": False,
            "no_refine": False,
            "refine_model": None,
            "refine_provider": None,
            "context": None,
            "copy": False,
            "language": None,
        }
        defaults.update(kwargs)
        return Mock(**defaults)

    return _create


# =============================================================================
# Environment & Isolation Fixtures
# =============================================================================


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

    Verhindert Konflikte mit laufenden pulsescribe Instanzen
    und ermöglicht parallele Test-Ausführung.
    """
    import transcribe

    monkeypatch.setattr(transcribe, "VOCABULARY_FILE", tmp_path / "vocab.json")

    return tmp_path


@pytest.fixture(autouse=True)
def reset_caches(monkeypatch):
    """
    Setzt Module-Level Caches vor jedem Test zurück.

    Wichtig für: _custom_app_contexts_cache (wird bei erstem Aufruf befüllt)
    """
    import refine.context

    monkeypatch.setattr(refine.context, "_custom_app_contexts_cache", None)


@pytest.fixture
def clean_env(monkeypatch):
    """Entfernt alle PULSESCRIBE_* Umgebungsvariablen für saubere Tests.

    Mockt auch load_environment() um zu verhindern, dass .env-Dateien
    während der Tests geladen werden (was sonst ENV-Pollution verursacht).
    """
    import os

    for key in list(os.environ.keys()):
        if key.startswith("PULSESCRIBE_"):
            monkeypatch.delenv(key, raising=False)

    # Verhindere dass load_environment() die .env lädt und ENV polluted
    # Module müssen importiert werden bevor setattr funktioniert
    import transcribe
    import pulsescribe_daemon

    monkeypatch.setattr(transcribe, "load_environment", lambda: None)
    monkeypatch.setattr(pulsescribe_daemon, "load_environment", lambda: None)
