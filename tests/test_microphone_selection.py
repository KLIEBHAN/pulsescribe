"""Microphone discovery, persistence and native settings selection."""

import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import config
from ui.microphone import get_microphone_options
from utils.preferences import _apply_env_updates_to_lines, read_env_file


@pytest.fixture
def devices(monkeypatch):
    devices = [
        {"name": "Speakers", "max_input_channels": 0, "default_samplerate": 44100},
        {"name": "Built-in Mic", "max_input_channels": 1, "default_samplerate": 44100},
        {"name": "USB Mic", "max_input_channels": 2, "default_samplerate": 48000},
    ]
    sd = SimpleNamespace(
        default=SimpleNamespace(device=[1, None]),
        query_devices=Mock(side_effect=lambda idx=None: devices if idx is None else devices[idx]),
        InputStream=Mock(side_effect=AssertionError("discovery must not open a stream")),
    )
    monkeypatch.setitem(sys.modules, "sounddevice", sd)
    monkeypatch.setattr(config, "_cached_input_device", None)
    monkeypatch.setattr(config, "_cached_input_device_preference", "")
    monkeypatch.delenv("PULSESCRIBE_INPUT_DEVICE", raising=False)
    return devices, sd


def test_options_only_list_inputs_and_deduplicate_names(devices):
    listed, sd = devices
    listed.append(dict(listed[2]))
    assert get_microphone_options() == [
        ("System default", ""), ("Built-in Mic", "Built-in Mic"), ("USB Mic", "USB Mic")
    ]
    sd.InputStream.assert_not_called()


def test_options_retain_missing_selection_and_survive_discovery_error(devices):
    _, sd = devices
    sd.query_devices.side_effect = RuntimeError("no audio service")
    assert get_microphone_options("USB Mic") == [
        ("System default", ""), ("USB Mic (unavailable)", "USB Mic")
    ]


def test_selected_microphone_overrides_default_and_caches_native_rate(devices, monkeypatch):
    _, sd = devices
    monkeypatch.setenv("PULSESCRIBE_INPUT_DEVICE", "USB Mic")
    assert config.get_input_device() == (2, 48000)
    assert config.get_input_device() == (2, 48000)
    assert sd.query_devices.call_count == 1
    monkeypatch.setenv("PULSESCRIBE_INPUT_DEVICE", "Built-in Mic")
    assert config.get_input_device() == (1, 44100)
    monkeypatch.delenv("PULSESCRIBE_INPUT_DEVICE")
    assert config.get_input_device() == (None, 44100)


def test_missing_microphone_falls_back_and_recovers_after_reconnect(devices, monkeypatch):
    listed, _ = devices
    monkeypatch.setenv("PULSESCRIBE_INPUT_DEVICE", "Headset")
    assert config.get_input_device() == (None, 44100)
    assert config._cached_input_device is None
    listed.insert(0, {"name": "Headset", "max_input_channels": 1, "default_samplerate": 16000})
    assert config.get_input_device() == (0, 16000)
    config.reset_input_device_cache()
    listed.reverse()
    assert config.get_input_device() == (3, 16000)


@pytest.mark.parametrize("name", ["USB Mic", "Fabi's AirPods", 'USB "Mic" #2', r"USB\Mic's"])
def test_device_names_roundtrip_through_both_env_readers(tmp_path, name):
    from dotenv import dotenv_values

    key = "PULSESCRIBE_INPUT_DEVICE"
    for original in ([], [f"{key}=old"]):
        lines, _ = _apply_env_updates_to_lines(original, {key: name})
        path = tmp_path / ".env"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        assert read_env_file(path)[key] == name
        assert dotenv_values(path)[key] == name


@pytest.mark.parametrize("selected", ["", "USB Mic", "Disconnected Mic"])
def test_mac_popup_refresh_and_save_preserve_selection(devices, selected):
    appkit = pytest.importorskip("AppKit")
    from ui.welcome import WelcomeController
    from utils.settings_env_updates import SettingsEnvUpdateBuilder

    appkit.NSApplication.sharedApplication()
    ctrl = WelcomeController.__new__(WelcomeController)
    ctrl._env_settings_cache = {"PULSESCRIBE_INPUT_DEVICE": selected}
    ctrl._mode_popup = ctrl._local_backend_popup = ctrl._local_model_popup = ctrl._lang_popup = None
    ctrl._microphone_popup = ctrl._create_settings_popup(0, 0, 250, ())
    ctrl._refresh_microphone_options()
    assert ctrl._microphone_popup.selectedItem().representedObject() == selected
    ctrl._refresh_microphone_options()
    builder = SettingsEnvUpdateBuilder()
    ctrl._add_general_env_updates(builder)
    assert builder.build()["PULSESCRIBE_INPUT_DEVICE"] == (selected or None)
    before = ctrl._get_current_settings_signature()
    ctrl._microphone_popup.selectItemAtIndex_(1 if not selected else 0)
    assert ctrl._get_current_settings_signature() != before


def test_mac_microphone_row_leaves_room_for_api_card(devices):
    appkit = pytest.importorskip("AppKit")
    from ui.welcome import (
        WelcomeController, CARD_SPACING, FOOTER_HEIGHT, WELCOME_HEIGHT,
        WELCOME_PADDING, WELCOME_WIDTH, _get_api_card_height,
    )

    appkit.NSApplication.sharedApplication()
    tab_y = WELCOME_PADDING + FOOTER_HEIGHT
    tab_height = WELCOME_HEIGHT - WELCOME_PADDING - 72 - tab_y - CARD_SPACING
    tab = appkit.NSTabView.alloc().initWithFrame_(
        appkit.NSMakeRect(0, tab_y, WELCOME_WIDTH, tab_height)
    )
    height = tab.contentRect().size.height
    ctrl = WelcomeController.__new__(WelcomeController)
    ctrl.config = ctrl._env_settings_cache = {}
    ctrl._content_view = appkit.NSView.alloc().initWithFrame_(
        appkit.NSMakeRect(0, 0, WELCOME_WIDTH, height)
    )
    ctrl._update_all_visibility = lambda: None
    bottom = ctrl._build_settings_card(height - CARD_SPACING)
    assert bottom >= _get_api_card_height()


@pytest.mark.parametrize("selected", ["", "USB Mic", "Disconnected Mic"])
def test_windows_combo_refresh_save_and_dirty_state(devices, selected):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication, QComboBox
    from ui.settings_windows import SettingsWindow
    from utils.settings_env_updates import SettingsEnvUpdateBuilder

    app = QApplication.instance() or QApplication([])
    window = SettingsWindow.__new__(SettingsWindow)
    window._microphone_combo = QComboBox()
    window._mode_combo = window._local_backend_combo = window._local_model_combo = None
    window._streaming_checkbox = window._lang_combo = None
    window._refresh_microphone_options(selected)
    before = window._get_current_settings_signature()
    changed = Mock()
    window._microphone_combo.currentIndexChanged.connect(changed)
    window._refresh_microphone_options()
    changed.assert_not_called()
    assert window._microphone_combo.currentData() == selected
    builder = SettingsEnvUpdateBuilder()
    window._add_core_env_updates(builder)
    assert builder.build()["PULSESCRIBE_INPUT_DEVICE"] == (selected or None)
    window._microphone_combo.setCurrentIndex(1 if not selected else 0)
    assert window._get_current_settings_signature() != before
    assert app is not None
