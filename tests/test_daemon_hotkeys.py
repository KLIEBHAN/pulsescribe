from unittest.mock import Mock

import pulsescribe_daemon as daemon_mod


class _FakeCarbonHotKeyRegistration:
    def __init__(self, **_kwargs):
        self.register_calls = 0

    def register(self):
        self.register_calls += 1
        return True, None

    def unregister(self):
        return None


def _make_hotkey_test_daemon() -> daemon_mod.PulseScribeDaemon:
    daemon = daemon_mod.PulseScribeDaemon(mode="openai")
    daemon._toggle_hotkey_handlers = []
    daemon._pynput_listeners = []
    daemon._modifier_taps = []
    daemon._hold_state = Mock()
    daemon._start_fn_hotkey_monitor = Mock(return_value=True)
    daemon._start_capslock_hotkey_monitor = Mock(return_value=True)
    daemon._start_toggle_hotkey_listener = Mock(return_value=True)
    daemon._start_hold_hotkey_listener = Mock(return_value=True)
    return daemon


def test_resolve_hotkey_bindings_prefers_explicit_toggle_and_hold_over_legacy() -> None:
    daemon = daemon_mod.PulseScribeDaemon(
        hotkey="f17",
        hotkey_mode="hold",
        toggle_hotkey="f18",
        hold_hotkey="f19",
    )

    assert daemon._resolve_hotkey_bindings() == [
        ("toggle", "f18"),
        ("hold", "f19"),
    ]


def test_resolve_hotkey_bindings_uses_legacy_hotkey_with_toggle_fallback() -> None:
    daemon = daemon_mod.PulseScribeDaemon(mode="openai")
    daemon.toggle_hotkey = None
    daemon.hold_hotkey = None
    daemon.hotkey = "capslock"
    daemon.hotkey_mode = "definitely-invalid"

    assert daemon._resolve_hotkey_bindings() == [("toggle", "capslock")]


def test_hotkey_bindings_signature_normalizes_modes_and_keys() -> None:
    signature = daemon_mod.PulseScribeDaemon._hotkey_bindings_signature(
        [
            (" TOGGLE ", " F19 "),
            ("invalid-mode", " Fn "),
            ("hold", "   "),
            ("hold", "CapsLock"),
        ]
    )

    assert signature == (
        ("toggle", "f19"),
        ("toggle", "fn"),
        ("hold", "capslock"),
    )


def test_register_hotkeys_skips_exact_duplicate_bindings_and_keeps_first(
    monkeypatch,
) -> None:
    daemon = _make_hotkey_test_daemon()
    daemon.toggle_hotkey = " Space "
    daemon.hold_hotkey = "space"
    daemon.hotkey = "f19"
    daemon.hotkey_mode = "hold"

    monkeypatch.setattr(
        daemon_mod,
        "check_input_monitoring_permission",
        lambda show_alert=False: True,
    )
    monkeypatch.setattr(
        "utils.carbon_hotkey.CarbonHotKeyRegistration",
        _FakeCarbonHotKeyRegistration,
    )

    daemon._register_hotkeys_from_current_settings(show_alerts=False)

    assert len(daemon._toggle_hotkey_handlers) == 1
    daemon._start_hold_hotkey_listener.assert_not_called()
    daemon._start_toggle_hotkey_listener.assert_not_called()


def test_register_hotkeys_skips_overlapping_bindings_and_keeps_first(
    monkeypatch,
) -> None:
    daemon = _make_hotkey_test_daemon()
    daemon.toggle_hotkey = "space"
    daemon.hold_hotkey = "cmd+space"
    daemon.hotkey = None
    daemon.hotkey_mode = "toggle"

    monkeypatch.setattr(
        daemon_mod,
        "check_input_monitoring_permission",
        lambda show_alert=False: True,
    )
    monkeypatch.setattr(
        "utils.carbon_hotkey.CarbonHotKeyRegistration",
        _FakeCarbonHotKeyRegistration,
    )

    daemon._register_hotkeys_from_current_settings(show_alerts=False)

    assert len(daemon._toggle_hotkey_handlers) == 1
    daemon._start_hold_hotkey_listener.assert_not_called()
