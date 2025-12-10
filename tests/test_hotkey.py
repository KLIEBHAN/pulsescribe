"""Tests für utils/hotkey.py – Hotkey-Parsing und Auto-Paste."""

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
# Tests: paste_transcript (Auto-Paste)
# =============================================================================


class TestPasteTranscript:
    """Tests für paste_transcript() – Clipboard und Auto-Paste."""

    def test_paste_transcript_success(self, monkeypatch):
        """Erfolgreicher Paste-Vorgang gibt True zurück."""
        # Mock subprocess.run für pbcopy und pbpaste
        call_log = []

        def mock_run(cmd, *args, **kwargs):
            call_log.append(cmd)
            result = subprocess.CompletedProcess(cmd, 0)
            if cmd[0] == "pbpaste":
                result.stdout = b"test text"
            else:
                result.stdout = b""
            result.stderr = b""
            return result

        monkeypatch.setattr(subprocess, "run", mock_run)

        # Mock Quartz CGEventPost
        with patch("utils.hotkey._paste_via_pynput", return_value=False), \
             patch("utils.hotkey._paste_via_quartz", return_value=True):  # Quartz success
            
            result = utils.hotkey.paste_transcript("test text")
            assert result is True

    def test_paste_transcript_pbcopy_failure(self, monkeypatch):
        """pbcopy-Fehler gibt False zurück."""
        def mock_run(cmd, *args, **kwargs):
            result = subprocess.CompletedProcess(cmd, 1)
            result.stdout = b""
            result.stderr = b"pbcopy error"
            return result

        monkeypatch.setattr(subprocess, "run", mock_run)

        result = utils.hotkey.paste_transcript("test text")
        assert result is False

    def test_paste_transcript_timeout(self, monkeypatch):
        """Timeout bei pbcopy gibt False zurück."""
        def mock_run(cmd, *args, **kwargs):
            raise subprocess.TimeoutExpired(cmd, 5)

        monkeypatch.setattr(subprocess, "run", mock_run)

        result = utils.hotkey.paste_transcript("test text")
        assert result is False

    def test_paste_transcript_empty_text(self, monkeypatch):
        """Leerer Text wird korrekt behandelt."""
        call_log = []

        def mock_run(cmd, *args, **kwargs):
            call_log.append((cmd, kwargs.get("input")))
            result = subprocess.CompletedProcess(cmd, 0)
            result.stdout = b""
            result.stderr = b""
            return result

        monkeypatch.setattr(subprocess, "run", mock_run)

        # Mock Quartz etc success
        with patch("utils.hotkey._paste_via_pynput", return_value=True):
             utils.hotkey.paste_transcript("")
        
        # Leerer Text sollte trotzdem verarbeitet werden
        assert any(cmd[0] == "pbcopy" for cmd, _ in call_log)


# =============================================================================
# Tests: _paste_via_osascript (Fallback)
# =============================================================================


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
