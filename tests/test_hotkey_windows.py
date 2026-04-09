from types import SimpleNamespace

from utils.hotkey_windows import (
    hotkeys_conflict,
    normalize_windows_hotkey,
    parse_windows_hotkey_for_pynput,
)


class _FakeKeyCode:
    @staticmethod
    def from_char(ch: str) -> str:
        return f"char:{ch}"


def _fake_keyboard() -> SimpleNamespace:
    key = SimpleNamespace(
        ctrl="key:ctrl",
        alt="key:alt",
        shift="key:shift",
        cmd="key:cmd",
        space="key:space",
        tab="key:tab",
        enter="key:enter",
        esc="key:esc",
        backspace="key:backspace",
        delete="key:delete",
        home="key:home",
        end="key:end",
        page_up="key:page_up",
        page_down="key:page_down",
        up="key:up",
        down="key:down",
        left="key:left",
        right="key:right",
        caps_lock="key:caps_lock",
    )
    for index in range(1, 25):
        setattr(key, f"f{index}", f"key:f{index}")
    return SimpleNamespace(Key=key, KeyCode=_FakeKeyCode)


def test_normalize_windows_hotkey_canonicalizes_aliases_and_order() -> None:
    normalized, error = normalize_windows_hotkey(" ALT + CTRL + Return ")
    assert error is None
    assert normalized == "ctrl+alt+enter"


def test_normalize_windows_hotkey_deduplicates_repeated_tokens() -> None:
    normalized, error = normalize_windows_hotkey("CTRL+ctrl+R+r")

    assert error is None
    assert normalized == "ctrl+r"


def test_parse_windows_hotkey_supports_space_token() -> None:
    keyboard = _fake_keyboard()
    parsed = parse_windows_hotkey_for_pynput("ctrl+alt+space", keyboard)

    assert parsed == {"key:ctrl", "key:alt", "key:space"}


def test_parse_windows_hotkey_rejects_unknown_tokens() -> None:
    keyboard = _fake_keyboard()
    parsed = parse_windows_hotkey_for_pynput("ctrl+alt+space+invalid", keyboard)

    assert parsed == set()


def test_parse_windows_hotkey_supports_navigation_keys() -> None:
    keyboard = _fake_keyboard()
    parsed = parse_windows_hotkey_for_pynput("shift+pagedown", keyboard)

    assert parsed == {"key:shift", "key:page_down"}


def test_parse_windows_hotkey_returns_empty_when_keyboard_lacks_requested_key() -> None:
    keyboard = _fake_keyboard()
    delattr(keyboard.Key, "page_down")

    assert parse_windows_hotkey_for_pynput("shift+pagedown", keyboard) == set()


def test_normalize_windows_hotkey_rejects_multiple_non_modifier_keys() -> None:
    normalized, error = normalize_windows_hotkey("ctrl+a+b")

    assert normalized == ""
    assert error is not None
    assert "nicht-modifier-taste" in error.lower()


def test_parse_windows_hotkey_rejects_multiple_non_modifier_keys() -> None:
    keyboard = _fake_keyboard()
    parsed = parse_windows_hotkey_for_pynput("ctrl+a+b", keyboard)

    assert parsed == set()


def test_hotkeys_conflict_detects_subset_overlap() -> None:
    assert hotkeys_conflict("ctrl+win", "ctrl+win+r") is True


def test_hotkeys_conflict_ignores_distinct_hotkeys() -> None:
    assert hotkeys_conflict("ctrl+alt+r", "ctrl+shift+space") is False
