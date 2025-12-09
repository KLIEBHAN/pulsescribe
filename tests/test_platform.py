"""Tests für whisper_platform Module."""

import sys
from unittest.mock import Mock, patch

import pytest


class TestGetPlatform:
    """Tests für platform detection."""

    def test_macos_detection(self):
        """macOS wird korrekt erkannt."""
        with patch.object(sys, "platform", "darwin"):
            from whisper_platform import get_platform
            assert get_platform() == "macos"

    def test_windows_detection(self):
        """Windows wird korrekt erkannt."""
        with patch.object(sys, "platform", "win32"):
            # Müssen das Modul neu laden
            import importlib
            import whisper_platform
            importlib.reload(whisper_platform)
            assert whisper_platform.get_platform() == "windows"

    def test_linux_detection(self):
        """Linux wird korrekt erkannt."""
        with patch.object(sys, "platform", "linux"):
            import importlib
            import whisper_platform
            importlib.reload(whisper_platform)
            assert whisper_platform.get_platform() == "linux"


class TestSoundPlayer:
    """Tests für Sound-Playback."""

    @pytest.fixture(autouse=True)
    def reset_platform_module(self):
        """Stellt sicher dass platform Modul sauber ist."""
        import importlib
        import whisper_platform
        importlib.reload(whisper_platform)

    def test_macos_soundplayer_exists(self):
        """MacOSSoundPlayer existiert."""
        from whisper_platform.sound import MacOSSoundPlayer
        player = MacOSSoundPlayer()
        assert hasattr(player, "play")

    def test_soundplayer_play_method(self):
        """play() Methode akzeptiert Sound-Namen."""
        from whisper_platform.sound import MacOSSoundPlayer
        player = MacOSSoundPlayer()
        # Sollte nicht werfen (silent failure für unbekannte Sounds)
        player.play("unknown_sound")


class TestClipboard:
    """Tests für Clipboard-Operationen."""

    def test_macos_clipboard_exists(self):
        """MacOSClipboard existiert."""
        from whisper_platform.clipboard import MacOSClipboard
        clipboard = MacOSClipboard()
        assert hasattr(clipboard, "copy")
        assert hasattr(clipboard, "paste")


class TestAppDetector:
    """Tests für App-Detection."""

    def test_macos_appdetector_exists(self):
        """MacOSAppDetector existiert."""
        from whisper_platform.app_detection import MacOSAppDetector
        detector = MacOSAppDetector()
        assert hasattr(detector, "get_frontmost_app")


class TestDaemonController:
    """Tests für Daemon-Kontrolle."""

    def test_macos_daemon_exists(self):
        """MacOSDaemonController existiert."""
        from whisper_platform.daemon import MacOSDaemonController
        controller = MacOSDaemonController()
        assert hasattr(controller, "start")
        assert hasattr(controller, "stop")
        assert hasattr(controller, "is_running")


class TestProtocols:
    """Tests für Protocol-Definitionen."""

    def test_soundplayer_protocol(self):
        """SoundPlayer Protocol ist definiert."""
        from whisper_platform.base import SoundPlayer
        # Protocol sollte runtime_checkable sein
        assert hasattr(SoundPlayer, "__protocol_attrs__") or hasattr(SoundPlayer, "_is_protocol")

    def test_clipboard_protocol(self):
        """ClipboardHandler Protocol ist definiert."""
        from whisper_platform.base import ClipboardHandler
        assert ClipboardHandler is not None

    def test_appdetector_protocol(self):
        """AppDetector Protocol ist definiert."""
        from whisper_platform.base import AppDetector
        assert AppDetector is not None

    def test_daemoncontroller_protocol(self):
        """DaemonController Protocol ist definiert."""
        from whisper_platform.base import DaemonController
        assert DaemonController is not None

    def test_hotkeylistener_protocol(self):
        """HotkeyListener Protocol ist definiert."""
        from whisper_platform.base import HotkeyListener
        assert HotkeyListener is not None
