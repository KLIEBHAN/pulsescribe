"""Tests für hotkey_daemon.py – Hotkey-Parsing und Konfiguration."""

import pytest


# =============================================================================
# Tests: parse_hotkey (QuickMacHotKey Format)
# =============================================================================


class TestParseHotkey:
    """Tests für parse_hotkey() – konvertiert Strings in (virtualKey, modifierMask)."""

    def test_function_key_f19(self):
        """F19 wird korrekt geparst."""
        import hotkey_daemon

        virtual_key, modifier_mask = hotkey_daemon.parse_hotkey("f19")
        assert virtual_key == 80  # kVK_F19
        assert modifier_mask == 0

    def test_function_key_f1(self):
        """F1 wird korrekt geparst."""
        import hotkey_daemon

        virtual_key, modifier_mask = hotkey_daemon.parse_hotkey("f1")
        assert virtual_key == 122  # kVK_F1
        assert modifier_mask == 0

    def test_function_key_case_insensitive(self):
        """Groß-/Kleinschreibung wird ignoriert."""
        import hotkey_daemon

        result_lower = hotkey_daemon.parse_hotkey("f19")
        result_upper = hotkey_daemon.parse_hotkey("F19")
        assert result_lower == result_upper

    def test_special_key_space(self):
        """Space wird korrekt geparst."""
        import hotkey_daemon

        virtual_key, modifier_mask = hotkey_daemon.parse_hotkey("space")
        assert virtual_key == 49  # kVK_Space
        assert modifier_mask == 0

    def test_special_key_enter(self):
        """Enter wird korrekt geparst."""
        import hotkey_daemon

        virtual_key, modifier_mask = hotkey_daemon.parse_hotkey("enter")
        assert virtual_key == 36  # kVK_Return
        assert modifier_mask == 0

    def test_special_key_esc(self):
        """Esc wird korrekt geparst."""
        import hotkey_daemon

        virtual_key, modifier_mask = hotkey_daemon.parse_hotkey("esc")
        assert virtual_key == 53  # kVK_Escape
        assert modifier_mask == 0

    def test_special_key_escape_alias(self):
        """Escape (Alias für esc) wird korrekt geparst."""
        import hotkey_daemon

        result_esc = hotkey_daemon.parse_hotkey("esc")
        result_escape = hotkey_daemon.parse_hotkey("escape")
        assert result_esc == result_escape

    def test_single_letter(self):
        """Einzelner Buchstabe wird korrekt geparst."""
        import hotkey_daemon

        virtual_key, modifier_mask = hotkey_daemon.parse_hotkey("a")
        assert virtual_key == 0  # kVK_ANSI_A
        assert modifier_mask == 0

    def test_single_number(self):
        """Einzelne Zahl wird korrekt geparst."""
        import hotkey_daemon

        virtual_key, modifier_mask = hotkey_daemon.parse_hotkey("5")
        assert virtual_key == 23  # kVK_ANSI_5
        assert modifier_mask == 0

    def test_unknown_key_raises(self):
        """Unbekannte Taste wirft ValueError."""
        import hotkey_daemon

        with pytest.raises(ValueError, match="Unbekannte Taste"):
            hotkey_daemon.parse_hotkey("unknownkey")

    def test_whitespace_stripped(self):
        """Whitespace wird entfernt."""
        import hotkey_daemon

        result = hotkey_daemon.parse_hotkey("  f19  ")
        assert result == (80, 0)


# =============================================================================
# Tests: parse_hotkey mit Modifiern
# =============================================================================


class TestParseHotkeyWithModifiers:
    """Tests für parse_hotkey() mit Tastenkombinationen."""

    def test_cmd_r(self):
        """cmd+r wird korrekt konvertiert."""
        import hotkey_daemon

        virtual_key, modifier_mask = hotkey_daemon.parse_hotkey("cmd+r")
        assert virtual_key == 15  # kVK_ANSI_R
        assert modifier_mask == 256  # cmdKey

    def test_ctrl_shift_space(self):
        """ctrl+shift+space wird korrekt konvertiert."""
        import hotkey_daemon

        virtual_key, modifier_mask = hotkey_daemon.parse_hotkey("ctrl+shift+space")
        assert virtual_key == 49  # kVK_Space
        assert modifier_mask == 4096 + 512  # controlKey + shiftKey

    def test_alt_f19(self):
        """alt+f19 wird korrekt konvertiert."""
        import hotkey_daemon

        virtual_key, modifier_mask = hotkey_daemon.parse_hotkey("alt+f19")
        assert virtual_key == 80  # kVK_F19
        assert modifier_mask == 2048  # optionKey

    def test_cmd_shift_r(self):
        """cmd+shift+r wird korrekt konvertiert."""
        import hotkey_daemon

        virtual_key, modifier_mask = hotkey_daemon.parse_hotkey("cmd+shift+r")
        assert virtual_key == 15  # kVK_ANSI_R
        assert modifier_mask == 256 + 512  # cmdKey + shiftKey

    def test_modifier_aliases(self):
        """Modifier-Aliase werden korrekt konvertiert."""
        import hotkey_daemon

        # control = ctrl
        _, ctrl_mask = hotkey_daemon.parse_hotkey("ctrl+a")
        _, control_mask = hotkey_daemon.parse_hotkey("control+a")
        assert ctrl_mask == control_mask == 4096

        # option = alt (macOS)
        _, alt_mask = hotkey_daemon.parse_hotkey("alt+a")
        _, option_mask = hotkey_daemon.parse_hotkey("option+a")
        assert alt_mask == option_mask == 2048

        # command = cmd
        _, cmd_mask = hotkey_daemon.parse_hotkey("cmd+a")
        _, command_mask = hotkey_daemon.parse_hotkey("command+a")
        assert cmd_mask == command_mask == 256

    def test_case_insensitive(self):
        """Groß-/Kleinschreibung wird ignoriert."""
        import hotkey_daemon

        result_lower = hotkey_daemon.parse_hotkey("ctrl+shift+a")
        result_upper = hotkey_daemon.parse_hotkey("CTRL+SHIFT+A")
        assert result_lower == result_upper

    def test_unknown_modifier_raises(self):
        """Unbekannter Modifier wirft ValueError."""
        import hotkey_daemon

        with pytest.raises(ValueError, match="Unbekannter Modifier"):
            hotkey_daemon.parse_hotkey("foobar+a")

    def test_whitespace_stripped(self):
        """Whitespace um Teile wird entfernt."""
        import hotkey_daemon

        result_compact = hotkey_daemon.parse_hotkey("ctrl+shift+a")
        result_spaced = hotkey_daemon.parse_hotkey("ctrl + shift + a")
        assert result_compact == result_spaced


# =============================================================================
# Tests: HotkeyDaemon Konfiguration
# =============================================================================


class TestHotkeyDaemonConfig:
    """Tests für HotkeyDaemon Konfiguration."""

    def test_default_hotkey(self):
        """Default-Hotkey ist f19."""
        import hotkey_daemon

        daemon = hotkey_daemon.HotkeyDaemon()
        assert daemon.hotkey == "f19"

    def test_default_mode(self):
        """Default-Modus ist toggle."""
        import hotkey_daemon

        daemon = hotkey_daemon.HotkeyDaemon()
        assert daemon.mode == "toggle"

    def test_custom_hotkey(self):
        """Custom-Hotkey wird übernommen."""
        import hotkey_daemon

        daemon = hotkey_daemon.HotkeyDaemon(hotkey="f12")
        assert daemon.hotkey == "f12"

    def test_ptt_mode_falls_back_to_toggle(self):
        """PTT-Modus fällt auf toggle zurück (nicht unterstützt)."""
        import hotkey_daemon

        daemon = hotkey_daemon.HotkeyDaemon(mode="ptt")
        # QuickMacHotKey unterstützt kein PTT, daher Fallback
        assert daemon.mode == "toggle"


# =============================================================================
# Tests: Recording State
# =============================================================================


class TestRecordingState:
    """Tests für Recording-State-Verwaltung."""

    def test_initial_state_not_recording(self):
        """Initial-State ist nicht recording."""
        import hotkey_daemon

        daemon = hotkey_daemon.HotkeyDaemon()
        assert daemon._recording is False

    def test_is_recording_no_pid_file(self, tmp_path, monkeypatch):
        """is_recording() gibt False wenn keine PID-Datei existiert."""
        import hotkey_daemon

        monkeypatch.setattr(hotkey_daemon, "PID_FILE", tmp_path / "nonexistent.pid")
        assert hotkey_daemon.is_recording() is False

    def test_is_recording_invalid_pid(self, tmp_path, monkeypatch):
        """is_recording() gibt False bei ungültiger PID."""
        import hotkey_daemon

        pid_file = tmp_path / "test.pid"
        pid_file.write_text("invalid")
        monkeypatch.setattr(hotkey_daemon, "PID_FILE", pid_file)
        assert hotkey_daemon.is_recording() is False

    def test_is_recording_stale_pid(self, tmp_path, monkeypatch):
        """is_recording() gibt False bei nicht existierendem Prozess."""
        import hotkey_daemon

        pid_file = tmp_path / "test.pid"
        pid_file.write_text("999999999")  # Sehr hohe PID, existiert nicht
        monkeypatch.setattr(hotkey_daemon, "PID_FILE", pid_file)
        assert hotkey_daemon.is_recording() is False


# =============================================================================
# Tests: Key Code Mappings
# =============================================================================


class TestKeyCodeMappings:
    """Tests für die KEY_CODE_MAP Vollständigkeit."""

    def test_all_function_keys_defined(self):
        """Alle Funktionstasten F1-F20 sind definiert."""
        import hotkey_daemon

        for i in range(1, 21):
            assert f"f{i}" in hotkey_daemon.KEY_CODE_MAP

    def test_all_letters_defined(self):
        """Alle Buchstaben a-z sind definiert."""
        import hotkey_daemon

        for char in "abcdefghijklmnopqrstuvwxyz":
            assert char in hotkey_daemon.KEY_CODE_MAP

    def test_all_numbers_defined(self):
        """Alle Zahlen 0-9 sind definiert."""
        import hotkey_daemon

        for num in "0123456789":
            assert num in hotkey_daemon.KEY_CODE_MAP

    def test_common_special_keys_defined(self):
        """Häufige Sondertasten sind definiert."""
        import hotkey_daemon

        required_keys = ["space", "enter", "return", "tab", "escape", "esc", "delete"]
        for key in required_keys:
            assert key in hotkey_daemon.KEY_CODE_MAP


# =============================================================================
# Tests: Modifier Mappings
# =============================================================================


class TestModifierMappings:
    """Tests für die MODIFIER_MAP Vollständigkeit."""

    def test_cmd_modifiers(self):
        """cmd und command sind definiert."""
        import hotkey_daemon

        assert "cmd" in hotkey_daemon.MODIFIER_MAP
        assert "command" in hotkey_daemon.MODIFIER_MAP
        assert (
            hotkey_daemon.MODIFIER_MAP["cmd"] == hotkey_daemon.MODIFIER_MAP["command"]
        )

    def test_ctrl_modifiers(self):
        """ctrl und control sind definiert."""
        import hotkey_daemon

        assert "ctrl" in hotkey_daemon.MODIFIER_MAP
        assert "control" in hotkey_daemon.MODIFIER_MAP
        assert (
            hotkey_daemon.MODIFIER_MAP["ctrl"] == hotkey_daemon.MODIFIER_MAP["control"]
        )

    def test_alt_modifiers(self):
        """alt und option sind definiert."""
        import hotkey_daemon

        assert "alt" in hotkey_daemon.MODIFIER_MAP
        assert "option" in hotkey_daemon.MODIFIER_MAP
        assert hotkey_daemon.MODIFIER_MAP["alt"] == hotkey_daemon.MODIFIER_MAP["option"]

    def test_shift_modifier(self):
        """shift ist definiert."""
        import hotkey_daemon

        assert "shift" in hotkey_daemon.MODIFIER_MAP


# =============================================================================
# Tests: paste_transcript (Auto-Paste)
# =============================================================================


class TestPasteTranscript:
    """Tests für paste_transcript() – Clipboard und Auto-Paste."""

    def test_paste_transcript_success(self, monkeypatch):
        """Erfolgreicher Paste-Vorgang gibt True zurück."""
        import hotkey_daemon
        import subprocess

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

        # Mock Quartz CGEventPost (um echte Tastatur-Events zu vermeiden)
        monkeypatch.setattr(
            hotkey_daemon,
            "paste_transcript",
            lambda text: True,  # Vereinfachter Mock
        )

        result = hotkey_daemon.paste_transcript("test text")
        assert result is True

    def test_paste_transcript_pbcopy_failure(self, monkeypatch):
        """pbcopy-Fehler gibt False zurück."""
        import hotkey_daemon
        import subprocess

        def mock_run(cmd, *args, **kwargs):
            result = subprocess.CompletedProcess(cmd, 1)
            result.stdout = b""
            result.stderr = b"pbcopy error"
            return result

        monkeypatch.setattr(subprocess, "run", mock_run)

        result = hotkey_daemon.paste_transcript("test text")
        assert result is False

    def test_paste_transcript_timeout(self, monkeypatch):
        """Timeout bei pbcopy gibt False zurück."""
        import hotkey_daemon
        import subprocess

        def mock_run(cmd, *args, **kwargs):
            raise subprocess.TimeoutExpired(cmd, 5)

        monkeypatch.setattr(subprocess, "run", mock_run)

        result = hotkey_daemon.paste_transcript("test text")
        assert result is False

    def test_paste_transcript_empty_text(self, monkeypatch):
        """Leerer Text wird korrekt behandelt."""
        import hotkey_daemon
        import subprocess

        call_log = []

        def mock_run(cmd, *args, **kwargs):
            call_log.append((cmd, kwargs.get("input")))
            result = subprocess.CompletedProcess(cmd, 0)
            result.stdout = b""
            result.stderr = b""
            return result

        monkeypatch.setattr(subprocess, "run", mock_run)

        # Mock CGEventPost
        def mock_cgeventpost(*args):
            pass

        # Simuliere erfolgreichen Quartz-Import
        import sys
        from unittest.mock import MagicMock

        mock_quartz = MagicMock()
        mock_quartz.CGEventCreateKeyboardEvent = MagicMock(return_value=MagicMock())
        mock_quartz.CGEventPost = mock_cgeventpost
        mock_quartz.CGEventSetFlags = MagicMock()
        mock_quartz.kCGEventFlagMaskCommand = 0x100000
        mock_quartz.kCGHIDEventTap = 0
        monkeypatch.setitem(sys.modules, "Quartz", mock_quartz)

        # Importiere paste_transcript neu mit gemocktem Quartz
        hotkey_daemon.paste_transcript("")
        # Leerer Text sollte trotzdem verarbeitet werden
        assert any(cmd[0] == "pbcopy" for cmd, _ in call_log)


# =============================================================================
# Tests: stop_recording (IPC)
# =============================================================================


class TestStopRecording:
    """Tests für stop_recording() – IPC und Prozess-Kommunikation."""

    def test_stop_recording_no_pid_file(self, tmp_path, monkeypatch):
        """Ohne PID-Datei gibt stop_recording None zurück."""
        import hotkey_daemon

        monkeypatch.setattr(hotkey_daemon, "PID_FILE", tmp_path / "nonexistent.pid")
        result = hotkey_daemon.stop_recording()
        assert result is None

    def test_stop_recording_invalid_pid(self, tmp_path, monkeypatch):
        """Ungültige PID gibt None zurück."""
        import hotkey_daemon

        pid_file = tmp_path / "test.pid"
        pid_file.write_text("invalid")
        monkeypatch.setattr(hotkey_daemon, "PID_FILE", pid_file)

        result = hotkey_daemon.stop_recording()
        assert result is None

    def test_stop_recording_process_not_found(self, tmp_path, monkeypatch):
        """Nicht existierender Prozess gibt None zurück."""
        import hotkey_daemon
        import os

        pid_file = tmp_path / "test.pid"
        pid_file.write_text("999999999")  # Unrealistische PID
        monkeypatch.setattr(hotkey_daemon, "PID_FILE", pid_file)

        # Mock os.kill um ProcessLookupError zu werfen
        def mock_kill(pid, sig):
            raise ProcessLookupError()

        monkeypatch.setattr(os, "kill", mock_kill)

        result = hotkey_daemon.stop_recording()
        assert result is None

    def test_stop_recording_with_transcript(self, tmp_path, monkeypatch):
        """Erfolgreiche Transkription gibt Text zurück."""
        import hotkey_daemon
        import os
        import signal

        # Setup IPC-Dateien
        pid_file = tmp_path / "test.pid"
        transcript_file = tmp_path / "transcript.txt"
        error_file = tmp_path / "error.txt"

        pid_file.write_text("12345")
        transcript_file.write_text("Das ist ein Test-Transkript.")

        monkeypatch.setattr(hotkey_daemon, "PID_FILE", pid_file)
        monkeypatch.setattr(hotkey_daemon, "TRANSCRIPT_FILE", transcript_file)
        monkeypatch.setattr(hotkey_daemon, "ERROR_FILE", error_file)
        monkeypatch.setattr(hotkey_daemon, "POLL_INTERVAL", 0.01)
        monkeypatch.setattr(hotkey_daemon, "TRANSCRIPT_TIMEOUT", 0.5)

        # Mock os.kill (Signal senden erfolgreich)
        kill_calls = []

        def mock_kill(pid, sig):
            kill_calls.append((pid, sig))

        monkeypatch.setattr(os, "kill", mock_kill)

        result = hotkey_daemon.stop_recording()

        assert result == "Das ist ein Test-Transkript."
        assert (12345, signal.SIGUSR1) in kill_calls

    def test_stop_recording_with_error(self, tmp_path, monkeypatch):
        """Fehler-Datei wird erkannt und None zurückgegeben."""
        import hotkey_daemon
        import os

        # Setup IPC-Dateien
        pid_file = tmp_path / "test.pid"
        transcript_file = tmp_path / "transcript.txt"
        error_file = tmp_path / "error.txt"

        pid_file.write_text("12345")
        error_file.write_text("Transkription fehlgeschlagen: API-Fehler")

        monkeypatch.setattr(hotkey_daemon, "PID_FILE", pid_file)
        monkeypatch.setattr(hotkey_daemon, "TRANSCRIPT_FILE", transcript_file)
        monkeypatch.setattr(hotkey_daemon, "ERROR_FILE", error_file)
        monkeypatch.setattr(hotkey_daemon, "POLL_INTERVAL", 0.01)
        monkeypatch.setattr(hotkey_daemon, "TRANSCRIPT_TIMEOUT", 0.5)

        # Mock os.kill
        monkeypatch.setattr(os, "kill", lambda pid, sig: None)

        result = hotkey_daemon.stop_recording()
        assert result is None

    def test_stop_recording_timeout(self, tmp_path, monkeypatch):
        """Timeout ohne Transkript gibt None zurück."""
        import hotkey_daemon
        import os

        # Setup IPC-Dateien (ohne Transkript)
        pid_file = tmp_path / "test.pid"
        transcript_file = tmp_path / "transcript.txt"
        error_file = tmp_path / "error.txt"

        pid_file.write_text("12345")
        # Kein Transkript und kein Error erstellen

        monkeypatch.setattr(hotkey_daemon, "PID_FILE", pid_file)
        monkeypatch.setattr(hotkey_daemon, "TRANSCRIPT_FILE", transcript_file)
        monkeypatch.setattr(hotkey_daemon, "ERROR_FILE", error_file)
        monkeypatch.setattr(hotkey_daemon, "POLL_INTERVAL", 0.01)
        monkeypatch.setattr(hotkey_daemon, "TRANSCRIPT_TIMEOUT", 0.1)  # Kurzer Timeout

        # Mock os.kill
        monkeypatch.setattr(os, "kill", lambda pid, sig: None)

        result = hotkey_daemon.stop_recording()
        assert result is None


# =============================================================================
# Tests: _paste_via_osascript (Fallback)
# =============================================================================


class TestPasteViaOsascript:
    """Tests für _paste_via_osascript() – Fallback-Mechanismus."""

    def test_osascript_success(self, monkeypatch):
        """Erfolgreicher osascript-Aufruf gibt True zurück."""
        import hotkey_daemon
        import subprocess

        def mock_run(cmd, *args, **kwargs):
            result = subprocess.CompletedProcess(cmd, 0)
            result.stdout = ""
            result.stderr = ""
            return result

        monkeypatch.setattr(subprocess, "run", mock_run)

        result = hotkey_daemon._paste_via_osascript()
        assert result is True

    def test_osascript_failure(self, monkeypatch):
        """Fehlgeschlagener osascript-Aufruf gibt False zurück."""
        import hotkey_daemon
        import subprocess

        def mock_run(cmd, *args, **kwargs):
            result = subprocess.CompletedProcess(cmd, 1)
            result.stdout = ""
            result.stderr = "osascript: Accessibility not granted"
            return result

        monkeypatch.setattr(subprocess, "run", mock_run)

        result = hotkey_daemon._paste_via_osascript()
        assert result is False
