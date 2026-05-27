from ui.hotkey_format import format_hotkey_for_display, normalize_hotkey_text


def test_normalize_hotkey_text_strips_outer_whitespace() -> None:
    assert normalize_hotkey_text("  ctrl+alt+r  ") == "ctrl+alt+r"
    assert normalize_hotkey_text(None) == ""


def test_format_hotkey_for_display_uses_platform_labels() -> None:
    labels = {"ctrl": "Ctrl", "alt": "Alt", "space": "Space"}

    assert format_hotkey_for_display("ctrl+alt+space", labels) == "Ctrl+Alt+Space"
    assert format_hotkey_for_display("f19", labels) == "F19"
    assert format_hotkey_for_display("x", labels) == "X"


def test_format_hotkey_for_display_can_clean_and_title_unknown_parts() -> None:
    labels = {"capslock": "Caps Lock"}

    assert (
        format_hotkey_for_display(
            " capslock + custom_key ",
            labels,
            strip_parts=True,
            omit_empty_parts=True,
            title_unknown_parts=True,
        )
        == "Caps Lock+Custom Key"
    )
