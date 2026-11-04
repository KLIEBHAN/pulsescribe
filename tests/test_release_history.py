"""Text shown by both settings implementations."""

from ui import release_history


def test_release_history_reflows_the_bundled_notes(monkeypatch):
    calls = []

    def load():
        calls.append("load")
        return "**Release Date:** 2026-11-04\n\nPulseScribe 1.16.1\n\nA hard-wrapped\nparagraph."

    monkeypatch.setattr(release_history, "load_recent_release_notes", load)
    assert release_history.build_release_history_text() == (
        "**Release Date:** 2026-11-04\n\nPulseScribe 1.16.1\n\nA hard-wrapped paragraph."
    )
    assert calls == ["load"]


def test_history_omits_the_legacy_install_prompt_but_keeps_changes(monkeypatch):
    monkeypatch.setattr(
        release_history,
        "load_recent_release_notes",
        lambda: (
            "PulseScribe 1.16.1\n\nChanges.\n\n"
            "Use **Check for Updates…** in the macOS menu bar or Windows tray\n"
            "to install this version.\n\nPulseScribe 1.16.0\n\nMore changes."
        ),
    )
    assert release_history.build_release_history_text() == (
        "PulseScribe 1.16.1\n\nChanges.\n\nPulseScribe 1.16.0\n\nMore changes."
    )


def test_missing_history_explains_where_to_find_the_notes(monkeypatch):
    monkeypatch.setattr(release_history, "load_recent_release_notes", lambda: "")
    text = release_history.build_release_history_text()
    assert "unavailable" in text
    assert "https://github.com/KLIEBHAN/pulsescribe/releases" in text
