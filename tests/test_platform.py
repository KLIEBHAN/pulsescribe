"""High-level tests for `whisper_platform`.

Goal: keep these tests stable and behavior-focused (no "class exists" checks).
"""

import sys
from unittest.mock import patch

import pytest

import whisper_platform
from whisper_platform.base import (
    AppDetector,
    ClipboardHandler,
    DaemonController,
    HotkeyListener,
    SoundPlayer,
)


@pytest.mark.parametrize(
    "platform,expected",
    [
        ("darwin", "macos"),
        ("win32", "windows"),
        ("linux", "linux"),
        ("linux2", "linux"),
    ],
    ids=["macos", "windows", "linux", "linux2"],
)
def test_get_platform_mapping(platform: str, expected: str):
    with patch.object(sys, "platform", platform):
        assert whisper_platform.get_platform() == expected


def test_get_platform_unsupported_raises():
    with patch.object(sys, "platform", "solaris"):
        with pytest.raises(RuntimeError, match="Nicht unterstützte Plattform"):
            whisper_platform.get_platform()


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS-only factories")
def test_factories_return_protocols_macos():
    assert isinstance(whisper_platform.get_sound_player(), SoundPlayer)
    assert isinstance(whisper_platform.get_clipboard(), ClipboardHandler)
    assert isinstance(whisper_platform.get_app_detector(), AppDetector)
    assert isinstance(whisper_platform.get_daemon_controller(), DaemonController)
    assert isinstance(
        whisper_platform.get_hotkey_listener("f19", lambda: None), HotkeyListener
    )

