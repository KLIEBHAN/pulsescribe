"""Shared UX copy for vocabulary editor surfaces."""

from __future__ import annotations

from utils.vocabulary import VocabularyInputAnalysis, analyze_vocabulary_text


def _keyword_label(count: int) -> str:
    return "keyword" if count == 1 else "keywords"


def _normalize_issue_flags(issues: list[str]) -> dict[str, bool]:
    lowered = [issue.lower() for issue in issues]
    return {
        "duplicates": any("doppelte" in issue for issue in lowered),
        "invalid_entries": any("keine strings" in issue for issue in lowered),
        "local_limit": any("local" in issue and "50" in issue for issue in lowered),
        "deepgram_limit": any("deepgram" in issue and "100" in issue for issue in lowered),
        "invalid_file": any(
            keyword in issue
            for issue in lowered
            for keyword in (
                "json",
                "nicht lesbar",
                "json-objekt",
                "liste",
            )
        ),
    }


def _limit_message(analysis: VocabularyInputAnalysis) -> str | None:
    if analysis.exceeds_deepgram_limit:
        return "Deepgram uses up to 100 keywords; Local Whisper uses the first 50."
    if analysis.exceeds_local_limit:
        return "Local Whisper uses the first 50 keywords."
    return None


def build_vocabulary_editor_feedback(
    raw_text: str | None,
    *,
    saved_keywords: list[str] | None = None,
) -> tuple[str, str]:
    """Return live feedback for the editable vocabulary text area."""
    analysis = analyze_vocabulary_text(raw_text)
    saved = list(saved_keywords or [])
    changed = analysis.keywords != saved

    if analysis.keyword_count == 0:
        if saved:
            return (
                "The custom vocabulary is now empty. Save to clear the existing list.",
                "warning",
            )
        return (
            "No custom vocabulary yet. Add one keyword or phrase per line; commas also work.",
            "text_secondary",
        )

    parts: list[str] = []
    if changed:
        parts.append(
            f"{analysis.keyword_count} {_keyword_label(analysis.keyword_count)} ready to save."
        )
    else:
        parts.append(
            f"No changes to save ({analysis.keyword_count} {_keyword_label(analysis.keyword_count)})."
        )

    if analysis.duplicate_count > 0:
        parts.append("Duplicate entries will be merged automatically.")
    limit_message = _limit_message(analysis)
    if limit_message:
        parts.append(limit_message)

    if analysis.duplicate_count > 0 or limit_message:
        color = "warning"
    elif changed:
        color = "text"
    else:
        color = "text_secondary"
    return " ".join(parts), color


def build_vocabulary_load_feedback(
    *,
    keywords: list[str],
    issues: list[str],
) -> tuple[str, str]:
    """Return feedback after loading vocabulary from disk."""
    flags = _normalize_issue_flags(issues)
    count = len(keywords)

    if flags["invalid_file"]:
        if count == 0:
            return (
                "Could not read the saved vocabulary cleanly. Saving will create a cleaned file.",
                "warning",
            )
        parts = [f"Loaded {count} {_keyword_label(count)}."]
        parts.append("Some file issues were repaired while loading.")
        return " ".join(parts), "warning"

    if count == 0:
        return (
            "No custom vocabulary yet. Add names, product terms, or jargon you want PulseScribe to recognize.",
            "text_secondary",
        )

    parts = [f"Loaded {count} {_keyword_label(count)}."]
    if flags["duplicates"]:
        parts.append("Duplicate entries in the file were merged automatically.")
    if flags["invalid_entries"]:
        parts.append("Invalid entries were ignored.")
    if flags["deepgram_limit"]:
        parts.append("Deepgram uses up to 100 keywords; Local Whisper uses the first 50.")
    elif flags["local_limit"]:
        parts.append("Local Whisper uses the first 50 keywords.")

    color = "warning" if any(flags.values()) else "text_secondary"
    return " ".join(parts), color


def build_vocabulary_save_feedback(
    raw_text: str | None,
    *,
    unchanged: bool,
) -> tuple[str, str]:
    """Return feedback after saving or no-op saving vocabulary."""
    analysis = analyze_vocabulary_text(raw_text)
    count = analysis.keyword_count
    parts: list[str] = []

    if unchanged:
        if count == 0:
            parts.append("No vocabulary changes to save.")
        else:
            parts.append(f"No vocabulary changes to save ({count} {_keyword_label(count)}).")
    else:
        if count == 0:
            parts.append("Saved an empty custom vocabulary list.")
        else:
            parts.append(f"Saved {count} {_keyword_label(count)}.")

    if analysis.duplicate_count > 0:
        if unchanged:
            parts.append("Duplicate entries are ignored automatically.")
        else:
            parts.append("Duplicate entries were merged automatically.")

    limit_message = _limit_message(analysis)
    if limit_message:
        parts.append(limit_message)

    if analysis.duplicate_count > 0 or limit_message:
        color = "warning"
    elif unchanged:
        color = "text_secondary"
    else:
        color = "success"
    return " ".join(parts), color


__all__ = [
    "build_vocabulary_editor_feedback",
    "build_vocabulary_load_feedback",
    "build_vocabulary_save_feedback",
]
