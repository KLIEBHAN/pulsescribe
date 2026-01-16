"""Tests für utils/hotkey.py – Hotkey-Parsing und Auto-Paste."""

import sys

import pytest
from unittest.mock import patch
import subprocess

import utils.hotkey


# =============================================================================
# Tests: parse_hotkey (QuickMacHotKey Format)
# =============================================================================


class TestParseHotkey:
    """Tests für parse_hotkey() – konvertiert Strings in (virtualKey, modifierMask)."""

    def test_function_key_f19(self):
        """F19 wird korrekt geparst."""
        virtual_key, modifier_mask = utils.hotkey.parse_hotkey("f19")
        assert virtual_key == 80  # kVK_F19
        assert modifier_mask == 0

    def test_function_key_f1(self):
        """F1 wird korrekt geparst."""
        virtual_key, modifier_mask = utils.hotkey.parse_hotkey("f1")
        assert virtual_key == 122  # kVK_F1
        assert modifier_mask == 0

    def test_function_key_case_insensitive(self):
        """Groß-/Kleinschreibung wird ignoriert."""
        result_lower = utils.hotkey.parse_hotkey("f19")
        result_upper = utils.hotkey.parse_hotkey("F19")
        assert result_lower == result_upper

    def test_special_key_space(self):
        """Space wird korrekt geparst."""
        virtual_key, modifier_mask = utils.hotkey.parse_hotkey("space")
        assert virtual_key == 49  # kVK_Space
        assert modifier_mask == 0

    def test_special_key_enter(self):
        """Enter wird korrekt geparst."""
        virtual_key, modifier_mask = utils.hotkey.parse_hotkey("enter")
        assert virtual_key == 36  # kVK_Return
        assert modifier_mask == 0

    def test_special_key_esc(self):
        """Esc wird korrekt geparst."""
        virtual_key, modifier_mask = utils.hotkey.parse_hotkey("esc")
        assert virtual_key == 53  # kVK_Escape
        assert modifier_mask == 0

    def test_fn_key(self):
        """Fn/Globe wird korrekt geparst."""
        virtual_key, modifier_mask = utils.hotkey.parse_hotkey("fn")
        assert virtual_key == 63  # kVK_Function
        assert modifier_mask == 0

    def test_capslock_key(self):
        """CapsLock wird korrekt geparst."""
        virtual_key, modifier_mask = utils.hotkey.parse_hotkey("capslock")
        assert virtual_key == 57  # kVK_CapsLock
        assert modifier_mask == 0

    def test_special_key_escape_alias(self):
        """Escape (Alias für esc) wird korrekt geparst."""
        result_esc = utils.hotkey.parse_hotkey("esc")
        result_escape = utils.hotkey.parse_hotkey("escape")
        assert result_esc == result_escape

    def test_single_letter(self):
        """Einzelner Buchstabe wird korrekt geparst."""
        virtual_key, modifier_mask = utils.hotkey.parse_hotkey("a")
        assert virtual_key == 0  # kVK_ANSI_A
        assert modifier_mask == 0

    def test_single_number(self):
        """Einzelne Zahl wird korrekt geparst."""
        virtual_key, modifier_mask = utils.hotkey.parse_hotkey("5")
        assert virtual_key == 23  # kVK_ANSI_5
        assert modifier_mask == 0

    def test_unknown_key_raises(self):
        """Unbekannte Taste wirft ValueError."""
        with pytest.raises(ValueError, match="Unbekannte Taste"):
            utils.hotkey.parse_hotkey("unknownkey")

    def test_whitespace_stripped(self):
        """Whitespace wird entfernt."""
        result = utils.hotkey.parse_hotkey("  f19  ")
        assert result == (80, 0)


# =============================================================================
# Tests: parse_hotkey mit Modifiern
# =============================================================================


class TestParseHotkeyWithModifiers:
    """Tests für parse_hotkey() mit Tastenkombinationen."""

    def test_cmd_r(self):
        """cmd+r wird korrekt konvertiert."""
        virtual_key, modifier_mask = utils.hotkey.parse_hotkey("cmd+r")
        assert virtual_key == 15  # kVK_ANSI_R
        assert modifier_mask == 256  # cmdKey

    def test_ctrl_shift_space(self):
        """ctrl+shift+space wird korrekt konvertiert."""
        virtual_key, modifier_mask = utils.hotkey.parse_hotkey("ctrl+shift+space")
        assert virtual_key == 49  # kVK_Space
        assert modifier_mask == 4096 + 512  # controlKey + shiftKey

    def test_alt_f19(self):
        """alt+f19 wird korrekt konvertiert."""
        virtual_key, modifier_mask = utils.hotkey.parse_hotkey("alt+f19")
        assert virtual_key == 80  # kVK_F19
        assert modifier_mask == 2048  # optionKey

    def test_cmd_shift_r(self):
        """cmd+shift+r wird korrekt konvertiert."""
        virtual_key, modifier_mask = utils.hotkey.parse_hotkey("cmd+shift+r")
        assert virtual_key == 15  # kVK_ANSI_R
        assert modifier_mask == 256 + 512  # cmdKey + shiftKey

    def test_alt_period(self):
        """alt+. wird korrekt konvertiert."""
        virtual_key, modifier_mask = utils.hotkey.parse_hotkey("alt+.")
        assert virtual_key == 47  # kVK_ANSI_Period
        assert modifier_mask == 2048  # optionKey

    def test_modifier_aliases(self):
        """Modifier-Aliase werden korrekt konvertiert."""
        # control = ctrl
        _, ctrl_mask = utils.hotkey.parse_hotkey("ctrl+a")
        _, control_mask = utils.hotkey.parse_hotkey("control+a")
        assert ctrl_mask == control_mask == 4096

        # option = alt (macOS)
        _, alt_mask = utils.hotkey.parse_hotkey("alt+a")
        _, option_mask = utils.hotkey.parse_hotkey("option+a")
        assert alt_mask == option_mask == 2048

        # command = cmd
        _, cmd_mask = utils.hotkey.parse_hotkey("cmd+a")
        _, command_mask = utils.hotkey.parse_hotkey("command+a")
        assert cmd_mask == command_mask == 256

    def test_case_insensitive(self):
        """Groß-/Kleinschreibung wird ignoriert."""
        result_lower = utils.hotkey.parse_hotkey("ctrl+shift+a")
        result_upper = utils.hotkey.parse_hotkey("CTRL+SHIFT+A")
        assert result_lower == result_upper

    def test_unknown_modifier_raises(self):
        """Unbekannter Modifier wirft ValueError."""
        with pytest.raises(ValueError, match="Unbekannter Modifier"):
            utils.hotkey.parse_hotkey("foobar+a")

    def test_whitespace_stripped(self):
        """Whitespace um Teile wird entfernt."""
        result_compact = utils.hotkey.parse_hotkey("ctrl+shift+a")
        result_spaced = utils.hotkey.parse_hotkey("ctrl + shift + a")
        assert result_compact == result_spaced


# =============================================================================
# Tests: PulseScribeDaemon._parse_pynput_hotkey (Hold Mode)
# =============================================================================


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS-only: requires pulsescribe_daemon")
class TestParsePynputHotkey:
    """Tests für Hold-Mode Hotkey-Parsing via pynput."""

    def test_ctrl_shift_space(self):
        """ctrl+shift+space wird korrekt geparst."""
        from pynput import keyboard  # type: ignore[import-not-found]
        from pulsescribe_daemon import PulseScribeDaemon

        keys = PulseScribeDaemon._parse_pynput_hotkey("ctrl+shift+space")
        assert keyboard.Key.ctrl in keys
        assert keyboard.Key.shift in keys
        assert keyboard.Key.space in keys

    def test_option_l_uses_virtual_keycode(self):
        """Option+Letter sollte per VK erkannt werden (Option modifiziert sonst das Zeichen)."""
        from pynput import keyboard  # type: ignore[import-not-found]
        from pulsescribe_daemon import PulseScribeDaemon
        from utils.hotkey import KEY_CODE_MAP

        keys = PulseScribeDaemon._parse_pynput_hotkey("option+l")
        assert keyboard.Key.alt in keys
        assert keyboard.KeyCode.from_vk(KEY_CODE_MAP["l"]) in keys

    def test_unknown_function_key_raises(self):
        """Unbekannte Funktionstaste wirft ValueError."""
        from pulsescribe_daemon import PulseScribeDaemon

        with pytest.raises(ValueError):
            PulseScribeDaemon._parse_pynput_hotkey("f99")


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS-only: tests Quartz hotkey listener")
class TestMacOSHotkeyListenerSelection:
    def test_toggle_uses_quartz_on_macos(self, monkeypatch):
        from pulsescribe_daemon import PulseScribeDaemon

        daemon = PulseScribeDaemon()
        calls = {"quartz": 0, "pynput": 0}

        def fake_quartz(hk: str, *, mode: str) -> bool:
            assert hk == "cmd+l"
            assert mode == "toggle"
            calls["quartz"] += 1
            return True

        def fake_parse(_hotkey: str):
            calls["pynput"] += 1
            raise AssertionError("pynput parser must not be used on macOS")

        monkeypatch.setattr(daemon, "_start_quartz_hotkey_listener", fake_quartz)
        monkeypatch.setattr(
            PulseScribeDaemon, "_parse_pynput_hotkey", staticmethod(fake_parse)
        )

        assert daemon._start_toggle_hotkey_listener("cmd+l") is True
        assert calls["quartz"] == 1
        assert calls["pynput"] == 0

    def test_hold_uses_quartz_on_macos(self, monkeypatch):
        from pulsescribe_daemon import PulseScribeDaemon

        daemon = PulseScribeDaemon()
        calls = {"quartz": 0, "pynput": 0}

        def fake_quartz(hk: str, *, mode: str) -> bool:
            assert hk == "cmd+l"
            assert mode == "hold"
            calls["quartz"] += 1
            return True

        def fake_parse(_hotkey: str):
            calls["pynput"] += 1
            raise AssertionError("pynput parser must not be used on macOS")

        monkeypatch.setattr(daemon, "_start_quartz_hotkey_listener", fake_quartz)
        monkeypatch.setattr(
            PulseScribeDaemon, "_parse_pynput_hotkey", staticmethod(fake_parse)
        )

        assert daemon._start_hold_hotkey_listener("cmd+l") is True
        assert calls["quartz"] == 1
        assert calls["pynput"] == 0


# =============================================================================
# Tests: paste_transcript (Auto-Paste)
# =============================================================================


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS-only: tests macOS paste logic")
class TestPasteTranscript:
    """Tests für paste_transcript() – Clipboard und Auto-Paste."""

    def test_paste_transcript_success(self, monkeypatch):
        """Erfolgreicher Paste-Vorgang via NSPasteboard gibt True zurück."""
        native_calls = []

        def mock_copy_native(text):
            native_calls.append(text)
            return True

        with (
            patch(
                "utils.hotkey._copy_to_clipboard_native", side_effect=mock_copy_native
            ),
            patch("utils.hotkey._paste_via_pynput", return_value=False),
            patch("utils.hotkey._paste_via_quartz", return_value=True),
        ):
            result = utils.hotkey.paste_transcript("test text")
            assert result is True

        # NSPasteboard wurde mit richtigem Text aufgerufen
        assert native_calls == ["test text"]

    def test_paste_transcript_pbcopy_failure(self, monkeypatch):
        """pbcopy-Fehler (bei NSPasteboard-Fallback) gibt False zurück."""

        def mock_run(cmd, *args, **kwargs):
            result = subprocess.CompletedProcess(cmd, 1)
            result.stdout = b""
            result.stderr = b"pbcopy error"
            return result

        monkeypatch.setattr(subprocess, "run", mock_run)

        # NSPasteboard fehlschlagen lassen, damit Fallback zu pbcopy geht
        with patch("utils.hotkey._copy_to_clipboard_native", return_value=False):
            result = utils.hotkey.paste_transcript("test text")
        assert result is False

    def test_paste_transcript_timeout(self, monkeypatch):
        """Timeout bei pbcopy (Fallback) gibt False zurück."""

        def mock_run(cmd, *args, **kwargs):
            raise subprocess.TimeoutExpired(cmd, 5)

        monkeypatch.setattr(subprocess, "run", mock_run)

        # NSPasteboard fehlschlagen lassen, damit Fallback zu pbcopy geht
        with patch("utils.hotkey._copy_to_clipboard_native", return_value=False):
            result = utils.hotkey.paste_transcript("test text")
        assert result is False

    def test_paste_transcript_empty_text(self, monkeypatch):
        """Leerer Text wird korrekt behandelt."""
        native_calls = []

        def mock_copy_native(text):
            native_calls.append(text)
            return True

        with (
            patch(
                "utils.hotkey._copy_to_clipboard_native", side_effect=mock_copy_native
            ),
            patch("utils.hotkey._paste_via_pynput", return_value=True),
        ):
            utils.hotkey.paste_transcript("")

        # Leerer Text sollte trotzdem verarbeitet werden
        assert native_calls == [""]

    def test_paste_transcript_clipboard_restore_disabled_by_default(self, monkeypatch):
        """Clipboard-Restore ist standardmäßig deaktiviert."""
        get_text_calls = []
        copy_calls = []

        def mock_get_text():
            get_text_calls.append(True)
            return "previous text"

        def mock_copy(text):
            copy_calls.append(text)
            return True

        monkeypatch.delenv("PULSESCRIBE_CLIPBOARD_RESTORE", raising=False)

        with (
            patch("utils.hotkey._get_clipboard_text", side_effect=mock_get_text),
            patch("utils.hotkey._copy_to_clipboard_native", side_effect=mock_copy),
            patch("utils.hotkey._paste_via_pynput", return_value=True),
        ):
            utils.hotkey.paste_transcript("test")

        # _get_clipboard_text sollte NICHT aufgerufen werden (da ENV nicht gesetzt)
        assert get_text_calls == []
        # Nur der Transkriptions-Text sollte kopiert werden
        assert copy_calls == ["test"]

    def test_paste_transcript_clipboard_restore_enabled(self, monkeypatch):
        """Clipboard-Restore kopiert vorherigen Text erneut ins Clipboard."""
        get_text_calls = []
        copy_calls = []

        def mock_get_text():
            get_text_calls.append(True)
            return "previous text"

        def mock_copy(text):
            copy_calls.append(text)
            return True

        monkeypatch.setenv("PULSESCRIBE_CLIPBOARD_RESTORE", "true")

        with (
            patch("utils.hotkey._get_clipboard_text", side_effect=mock_get_text),
            patch("utils.hotkey._copy_to_clipboard_native", side_effect=mock_copy),
            patch("utils.hotkey._paste_via_pynput", return_value=True),
            patch("time.sleep"),  # Skip sleep für schnelleren Test
        ):
            utils.hotkey.paste_transcript("test")

        # _get_clipboard_text sollte aufgerufen werden
        assert get_text_calls == [True]
        # Erst Transkription, dann vorheriger Text (Re-Copy)
        assert copy_calls == ["test", "previous text"]

    def test_paste_transcript_clipboard_restore_with_empty_clipboard(self, monkeypatch):
        """Kein Re-Copy wenn Clipboard vor Transkription leer war."""
        copy_calls = []

        def mock_copy(text):
            copy_calls.append(text)
            return True

        monkeypatch.setenv("PULSESCRIBE_CLIPBOARD_RESTORE", "true")

        with (
            patch(
                "utils.hotkey._get_clipboard_text", return_value=None
            ),  # Leerer Clipboard
            patch("utils.hotkey._copy_to_clipboard_native", side_effect=mock_copy),
            patch("utils.hotkey._paste_via_pynput", return_value=True),
            patch("time.sleep"),
        ):
            utils.hotkey.paste_transcript("test")

        # Nur Transkription, kein Re-Copy (da vorheriger Clipboard leer)
        assert copy_calls == ["test"]


# =============================================================================
# Tests: _paste_via_osascript (Fallback)
# =============================================================================


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS-only: tests osascript")
class TestPasteViaOsascript:
    """Tests für _paste_via_osascript() – Fallback-Mechanismus."""

    def test_osascript_success(self, monkeypatch):
        """Erfolgreicher osascript-Aufruf gibt True zurück."""

        def mock_run(cmd, *args, **kwargs):
            result = subprocess.CompletedProcess(cmd, 0)
            result.stdout = ""
            result.stderr = ""
            return result

        monkeypatch.setattr(subprocess, "run", mock_run)

        result = utils.hotkey._paste_via_osascript()
        assert result is True

    def test_osascript_failure(self, monkeypatch):
        """Fehlgeschlagener osascript-Aufruf gibt False zurück."""

        def mock_run(cmd, *args, **kwargs):
            result = subprocess.CompletedProcess(cmd, 1)
            result.stdout = ""
            result.stderr = "osascript: Accessibility not granted"
            return result

        monkeypatch.setattr(subprocess, "run", mock_run)

        result = utils.hotkey._paste_via_osascript()
        assert result is False


# =============================================================================
# Tests: Windows paste_transcript (Ctrl+V via pynput)
# =============================================================================


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only: tests Windows paste logic")
class TestPasteTranscriptWindows:
    """Tests für paste_transcript() auf Windows – pyperclip + pynput."""

    def test_paste_transcript_success(self, monkeypatch):
        """Erfolgreicher Paste-Vorgang auf Windows gibt True zurück."""
        copy_calls = []
        paste_calls = []

        def mock_copy(text):
            copy_calls.append(text)

        def mock_paste_windows():
            paste_calls.append(True)
            return True

        # Ensure clipboard restore is disabled
        monkeypatch.delenv("PULSESCRIBE_CLIPBOARD_RESTORE", raising=False)

        # Mock pyperclip
        import pyperclip
        monkeypatch.setattr(pyperclip, "copy", mock_copy)

        # Mock _paste_via_pynput_windows
        monkeypatch.setattr(utils.hotkey, "_paste_via_pynput_windows", mock_paste_windows)

        result = utils.hotkey.paste_transcript("test text")

        assert result is True
        assert copy_calls == ["test text"]
        assert paste_calls == [True]

    def test_paste_transcript_pyperclip_failure(self, monkeypatch):
        """pyperclip-Fehler gibt False zurück."""

        def mock_copy(text):
            raise Exception("Clipboard error")

        # Ensure clipboard restore is disabled
        monkeypatch.delenv("PULSESCRIBE_CLIPBOARD_RESTORE", raising=False)

        import pyperclip
        monkeypatch.setattr(pyperclip, "copy", mock_copy)

        result = utils.hotkey.paste_transcript("test text")

        assert result is False

    def test_paste_transcript_pynput_failure(self, monkeypatch):
        """pynput-Fehler gibt False zurück (Text bleibt im Clipboard)."""
        copy_calls = []

        def mock_copy(text):
            copy_calls.append(text)

        def mock_paste_windows():
            return False  # Simuliere pynput-Fehler

        # Ensure clipboard restore is disabled
        monkeypatch.delenv("PULSESCRIBE_CLIPBOARD_RESTORE", raising=False)

        import pyperclip
        monkeypatch.setattr(pyperclip, "copy", mock_copy)
        monkeypatch.setattr(utils.hotkey, "_paste_via_pynput_windows", mock_paste_windows)

        result = utils.hotkey.paste_transcript("test text")

        assert result is False
        # Text wurde trotzdem ins Clipboard kopiert
        assert copy_calls == ["test text"]

    def test_paste_transcript_empty_text(self, monkeypatch):
        """Leerer Text wird korrekt behandelt."""
        copy_calls = []

        def mock_copy(text):
            copy_calls.append(text)

        def mock_paste_windows():
            return True

        # Ensure clipboard restore is disabled
        monkeypatch.delenv("PULSESCRIBE_CLIPBOARD_RESTORE", raising=False)

        import pyperclip
        monkeypatch.setattr(pyperclip, "copy", mock_copy)
        monkeypatch.setattr(utils.hotkey, "_paste_via_pynput_windows", mock_paste_windows)

        result = utils.hotkey.paste_transcript("")

        assert result is True
        assert copy_calls == [""]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only: tests pynput Ctrl+V")
class TestPasteViaPynputWindows:
    """Tests für _paste_via_pynput_windows() – Ctrl+V Simulation."""

    def test_pynput_success(self, monkeypatch):
        """Erfolgreiche Ctrl+V Simulation gibt True zurück."""
        key_events = []

        class MockKey:
            ctrl = "ctrl"

        class MockController:
            def press(self, key):
                key_events.append(("press", key))

            def release(self, key):
                key_events.append(("release", key))

        # Mock pynput.keyboard
        import pynput.keyboard
        monkeypatch.setattr(pynput.keyboard, "Controller", MockController)
        monkeypatch.setattr(pynput.keyboard, "Key", MockKey)

        result = utils.hotkey._paste_via_pynput_windows()

        assert result is True
        assert ("press", "ctrl") in key_events
        assert ("press", "v") in key_events
        assert ("release", "v") in key_events
        assert ("release", "ctrl") in key_events

    def test_pynput_exception(self, monkeypatch):
        """Exception bei pynput gibt False zurück."""

        class MockController:
            def press(self, key):
                raise RuntimeError("Keyboard simulation failed")

            def release(self, key):
                pass

        class MockKey:
            ctrl = "ctrl"

        import pynput.keyboard
        monkeypatch.setattr(pynput.keyboard, "Controller", MockController)
        monkeypatch.setattr(pynput.keyboard, "Key", MockKey)

        result = utils.hotkey._paste_via_pynput_windows()

        assert result is False
