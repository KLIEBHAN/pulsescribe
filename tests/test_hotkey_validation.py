import sys
from unittest.mock import Mock

import pytest

from utils.hotkey import hotkeys_conflict
from utils.hotkey_validation import validate_hotkey_change


def test_hotkeys_conflict_detects_modifier_subset_overlap() -> None:
    assert hotkeys_conflict("space", "cmd+space") is True
    assert hotkeys_conflict("option+space", "cmd+option+space") is True
    assert hotkeys_conflict("cmd+a", "cmd+b") is False


def test_validate_hotkey_change_rejects_overlapping_hotkeys(monkeypatch) -> None:
    values = {
        "PULSESCRIBE_TOGGLE_HOTKEY": "space",
        "PULSESCRIBE_HOLD_HOTKEY": "",
    }

    monkeypatch.setattr(
        "utils.preferences.get_env_setting",
        lambda key: values.get(key),
    )
    monkeypatch.setattr(
        "utils.permissions.check_input_monitoring_permission",
        lambda show_alert=False: True,
    )

    normalized, level, message = validate_hotkey_change("hold", "cmd+space")

    assert normalized == "cmd+space"
    assert level == "error"
    assert message is not None
    assert "überlappen" in message.lower()


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS-only daemon hotkeys")
def test_daemon_registration_skips_overlapping_hotkeys(monkeypatch) -> None:
    import pulsescribe_daemon as daemon_mod

    class _FakeCarbonHotKeyRegistration:
        def __init__(self, **_kwargs):
            self.register_calls = 0

        def register(self):
            self.register_calls += 1
            return True, None

        def unregister(self):
            return None

    daemon = daemon_mod.PulseScribeDaemon.__new__(daemon_mod.PulseScribeDaemon)
    daemon.toggle_hotkey = "space"
    daemon.hold_hotkey = "cmd+space"
    daemon.hotkey = None
    daemon.hotkey_mode = "toggle"
    daemon._toggle_hotkey_handlers = []
    daemon._pynput_listeners = []
    daemon._modifier_taps = []
    daemon._fn_active = False
    daemon._caps_active = False
    daemon._hold_state = Mock()
    daemon._start_fn_hotkey_monitor = Mock(return_value=True)
    daemon._start_capslock_hotkey_monitor = Mock(return_value=True)
    daemon._start_toggle_hotkey_listener = Mock(return_value=True)
    daemon._start_hold_hotkey_listener = Mock(return_value=True)

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
