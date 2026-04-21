from ui.vocabulary_feedback import (
    build_vocabulary_editor_feedback,
    build_vocabulary_load_feedback,
    build_vocabulary_save_feedback,
)


def test_build_vocabulary_editor_feedback_handles_empty_state() -> None:
    text, color = build_vocabulary_editor_feedback("", saved_keywords=[])

    assert "No custom vocabulary yet" in text
    assert "commas also work" in text
    assert color == "text_secondary"


def test_build_vocabulary_editor_feedback_explains_duplicates_and_limits() -> None:
    text, color = build_vocabulary_editor_feedback(
        "API\napi\n" + "\n".join(f"kw{i}" for i in range(60)),
        saved_keywords=["API"],
    )

    assert "ready to save" in text
    assert "Duplicate entries will be merged automatically" in text
    assert "Local Whisper uses the first 50 keywords" in text
    assert color == "warning"


def test_build_vocabulary_load_feedback_reports_empty_and_cleaned_states() -> None:
    empty_text, empty_color = build_vocabulary_load_feedback(keywords=[], issues=[])
    fixed_text, fixed_color = build_vocabulary_load_feedback(
        keywords=["Alpha", "Beta"],
        issues=["2 doppelte Keywords gefunden."],
    )

    assert "No custom vocabulary yet" in empty_text
    assert empty_color == "text_secondary"
    assert "Loaded 2 keywords." in fixed_text
    assert "merged automatically" in fixed_text
    assert fixed_color == "warning"


def test_build_vocabulary_save_feedback_distinguishes_saved_and_unchanged() -> None:
    saved_text, saved_color = build_vocabulary_save_feedback(
        "API\napi",
        unchanged=False,
    )
    unchanged_text, unchanged_color = build_vocabulary_save_feedback(
        "API",
        unchanged=True,
    )

    assert "Saved 1 keyword." in saved_text
    assert "merged automatically" in saved_text
    assert saved_color == "warning"

    assert "No vocabulary changes to save" in unchanged_text
    assert unchanged_color == "text_secondary"
