from ui.overlay import OVERLAY_INTERIM_MAX_CHARS, _format_recording_interim_text


def test_format_recording_interim_text_compacts_whitespace():
    text = "  hello   world \n\n from   pulse  "
    assert _format_recording_interim_text(text) == "hello world from pulse"


def test_format_recording_interim_text_keeps_tail_for_long_text():
    text = "alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu"
    max_chars = 18

    formatted = _format_recording_interim_text(text, max_chars=max_chars)

    assert formatted == "..." + " ".join(text.split())[-(max_chars - 3) :]
    assert len(formatted) == max_chars


def test_format_recording_interim_text_uses_default_limit():
    text = "x" * (OVERLAY_INTERIM_MAX_CHARS + 20)
    formatted = _format_recording_interim_text(text)

    assert formatted.startswith("...")
    assert len(formatted) == OVERLAY_INTERIM_MAX_CHARS
