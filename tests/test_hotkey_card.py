from types import SimpleNamespace

import ui.hotkey_card as hotkey_card_mod
from ui.hotkey_card import HotkeyCard, HotkeyCardWidgets


class _FakeField:
    def __init__(self) -> None:
        self.value = ""
        self.calls = 0

    def setStringValue_(self, value: str) -> None:
        self.value = value
        self.calls += 1


class _FakeStatusLabel:
    def __init__(self) -> None:
        self.value = ""
        self.color = None
        self.value_calls = 0
        self.color_calls = 0

    def setStringValue_(self, value: str) -> None:
        self.value = value
        self.value_calls += 1

    def setTextColor_(self, color) -> None:
        self.color = color
        self.color_calls += 1


def _make_card() -> HotkeyCard:
    widgets = HotkeyCardWidgets(
        toggle_field=_FakeField(),
        toggle_record_btn=None,
        hold_field=_FakeField(),
        hold_record_btn=None,
        status_label=_FakeStatusLabel(),
    )
    return HotkeyCard(
        widgets=widgets,
        hotkey_recorder=SimpleNamespace(recording=False),
        on_hotkey_change=lambda *_args: True,
    )


def test_sync_from_env_skips_duplicate_field_updates(monkeypatch) -> None:
    card = _make_card()

    monkeypatch.setattr(
        hotkey_card_mod,
        "get_env_setting",
        lambda key: {
            "PULSESCRIBE_TOGGLE_HOTKEY": "f19",
            "PULSESCRIBE_HOLD_HOTKEY": "fn",
        }.get(key),
        raising=False,
    )

    def _fake_get_env_setting(key: str):
        return {
            "PULSESCRIBE_TOGGLE_HOTKEY": "f19",
            "PULSESCRIBE_HOLD_HOTKEY": "fn",
        }.get(key)

    import utils.preferences as prefs_mod

    monkeypatch.setattr(prefs_mod, "get_env_setting", _fake_get_env_setting)

    card.sync_from_env()
    card.sync_from_env()

    assert card._widgets.toggle_field.value == "F19"
    assert card._widgets.hold_field.value == "FN"
    assert card._widgets.toggle_field.calls == 1
    assert card._widgets.hold_field.calls == 1


def test_set_status_skips_duplicate_label_updates(monkeypatch) -> None:
    card = _make_card()

    monkeypatch.setattr(hotkey_card_mod, "_get_color", lambda *args, **kwargs: args)

    card.set_status("warning", "Check hotkey")
    card.set_status("warning", "Check hotkey")

    status_label = card._widgets.status_label
    assert status_label.value == "Check hotkey"
    assert status_label.value_calls == 1
    assert status_label.color_calls == 1


def test_sync_from_env_prefers_cached_hotkeys_provider(monkeypatch) -> None:
    card = HotkeyCard(
        widgets=HotkeyCardWidgets(
            toggle_field=_FakeField(),
            toggle_record_btn=None,
            hold_field=_FakeField(),
            hold_record_btn=None,
            status_label=_FakeStatusLabel(),
        ),
        hotkey_recorder=SimpleNamespace(recording=False),
        on_hotkey_change=lambda *_args: True,
        get_current_hotkeys=lambda: ("option+space", "fn"),
    )

    import utils.preferences as prefs_mod

    monkeypatch.setattr(
        prefs_mod,
        "get_env_setting",
        lambda _key: (_ for _ in ()).throw(AssertionError("env read should be skipped")),
    )

    card.sync_from_env()

    assert card._widgets.toggle_field.value == "OPTION+SPACE"
    assert card._widgets.hold_field.value == "FN"
