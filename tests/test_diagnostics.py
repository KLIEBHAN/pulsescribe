import json
import zipfile

import pytest

import utils.diagnostics as diagnostics
from utils.diagnostics import _read_env_file, _redact_log_line, _redact_log_text


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        (
            "12:00:00 [INFO] Auto-Paste: 'hello world'\n",
            "12:00:00 [INFO] Auto-Paste: <redacted>\n",
        ),
        (
            "12:00:00 [INFO] ✓ Text eingefügt: 'secret'\n",
            "12:00:00 [INFO] ✓ Text eingefügt: <redacted>\n",
        ),
        (
            "12:00:00 [INFO] Transkript: highly sensitive text...\n",
            "12:00:00 [INFO] Transkript: <redacted>\n",
        ),
        (
            "12:00:00 [DEBUG] Ergebnis: raw transcript preview\n",
            "12:00:00 [DEBUG] Ergebnis: <redacted>\n",
        ),
        (
            "12:00:00 [INFO] [abc12345] Final: final transcript preview\n",
            "12:00:00 [INFO] [abc12345] Final: <redacted>\n",
        ),
        (
            "12:00:00 [DEBUG] [abc12345] Interim: interim preview\n",
            "12:00:00 [DEBUG] [abc12345] Interim: <redacted>\n",
        ),
        (
            "12:00:00 [DEBUG] [abc12345] Output: refined preview\n",
            "12:00:00 [DEBUG] [abc12345] Output: <redacted>\n",
        ),
        (
            "12:00:00 [DEBUG] Transcript saved to history: hello...\n",
            "12:00:00 [DEBUG] Transcript saved to history: <redacted>\n",
        ),
        (
            "12:00:00 [DEBUG] State: AppState.DONE text='I'm still here...'\n",
            "12:00:00 [DEBUG] State: AppState.DONE text='<redacted>'\n",
        ),
    ],
)
def test_redact_log_line_removes_transcript_previews(line: str, expected: str) -> None:
    assert _redact_log_line(line) == expected


def test_redact_log_line_text_field_non_greedy() -> None:
    """Non-greedy regex redacts each text='...' field independently."""
    line = "12:00:00 [DEBUG] text='secret1' status='ok' text='secret2'\n"
    result = _redact_log_line(line)
    assert result == "12:00:00 [DEBUG] text='<redacted>' status='ok' text='<redacted>'\n"


def test_redact_log_line_leaves_non_sensitive_lines_unchanged() -> None:
    line = "12:00:00 [INFO] Starting PulseScribe\n"
    assert _redact_log_line(line) == line


def test_redact_log_text_redacts_each_matching_line() -> None:
    raw = (
        "12:00:00 [INFO] Starting PulseScribe\n"
        "12:00:01 [INFO] Transkript: secret\n"
        "12:00:02 [DEBUG] [abc12345] Interim: partial\n"
    )

    redacted = _redact_log_text(raw)

    assert "Starting PulseScribe" in redacted
    assert "Transkript: <redacted>" in redacted
    assert "[abc12345] Interim: <redacted>" in redacted
    assert "secret" not in redacted
    assert "partial" not in redacted


def test_read_env_file_parses_quoted_values_and_inline_comments(tmp_path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        '\n'.join(
            [
                'PULSESCRIBE_MODE="local"',
                'PULSESCRIBE_HOLD_HOTKEY="ctrl+win"  # readable comment',
                "PULSESCRIBE_LANGUAGE='de'",
                'DEEPGRAM_API_KEY="dg-test"',
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    values = _read_env_file(env_file)

    assert values["PULSESCRIBE_MODE"] == "local"
    assert values["PULSESCRIBE_HOLD_HOTKEY"] == "ctrl+win"
    assert values["PULSESCRIBE_LANGUAGE"] == "de"
    assert values["DEEPGRAM_API_KEY"] == "dg-test"


def test_read_env_file_supports_export_prefix(tmp_path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                'export PULSESCRIBE_MODE="local"',
                "export PULSESCRIBE_LANGUAGE=de  # comment",
                "export DEEPGRAM_API_KEY=dg-test",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    values = _read_env_file(env_file)

    assert values["PULSESCRIBE_MODE"] == "local"
    assert values["PULSESCRIBE_LANGUAGE"] == "de"
    assert values["DEEPGRAM_API_KEY"] == "dg-test"



def test_read_redacted_log_tail_uses_tail_reader_instead_of_full_file_read(
    tmp_path, monkeypatch
) -> None:
    log_file = tmp_path / "pulsescribe.log"
    log_file.write_text("ignored\n", encoding="utf-8")

    tail_calls: list[tuple[object, int, int]] = []

    def fake_tail_reader(
        path, *, max_lines, encoding="utf-8", errors="replace", max_scan_bytes=0
    ) -> str:
        tail_calls.append((path, max_lines, max_scan_bytes))
        return "12:00:01 [INFO] Transkript: secret tail\n"

    monkeypatch.setattr(diagnostics, "read_file_tail_lines", fake_tail_reader)
    monkeypatch.setattr(
        diagnostics,
        "_read_text_safe",
        lambda _path: (_ for _ in ()).throw(
            AssertionError("full file read should not happen")
        ),
    )

    result = diagnostics._read_redacted_log_tail(log_file, max_lines=800)

    assert tail_calls == [
        (log_file, 800, diagnostics._log_tail_scan_bytes(800))
    ]
    assert result == "12:00:01 [INFO] Transkript: <redacted>\n"



def test_reveal_exported_file_uses_windows_explorer(tmp_path, monkeypatch) -> None:
    zip_path = tmp_path / "pulsescribe_diagnostics.zip"
    zip_path.write_text("zip", encoding="utf-8")

    calls: list[list[str]] = []
    monkeypatch.setattr(diagnostics.sys, "platform", "win32")
    monkeypatch.setattr(
        diagnostics.subprocess,
        "Popen",
        lambda cmd: calls.append(cmd),
    )

    diagnostics._reveal_exported_file(zip_path)

    assert calls == [["explorer", "/select,", str(zip_path)]]


def test_export_diagnostics_report_cleans_up_broken_zip_on_error(
    tmp_path, monkeypatch
) -> None:
    """When zip creation fails (e.g. disk full), the broken file must be removed."""
    cfg = tmp_path / ".pulsescribe"
    cfg.mkdir(parents=True)

    monkeypatch.setattr(diagnostics, "_user_config_dir", lambda: cfg)
    monkeypatch.setattr(diagnostics.platform, "platform", lambda: "macOS-14.0-arm64")
    monkeypatch.setattr(
        diagnostics.platform,
        "mac_ver",
        lambda: ("14.0", ("", "", ""), "arm64"),
    )
    monkeypatch.setattr(diagnostics.platform, "machine", lambda: "arm64")

    # Simulate zipfile.ZipFile raising OSError (e.g. disk full)
    def _failing_zipfile(*args, **kwargs):
        raise OSError("No space left on device")

    monkeypatch.setattr(zipfile, "ZipFile", _failing_zipfile)

    with pytest.raises(OSError, match="No space left"):
        diagnostics.export_diagnostics_report()

    # Verify no broken zip files remain
    diag_dir = cfg / "diagnostics"
    zip_files = list(diag_dir.glob("*.zip"))
    assert zip_files == [], f"Broken zip should be cleaned up, found: {zip_files}"


def test_export_diagnostics_report_redacts_startup_log_tail(
    tmp_path, monkeypatch
) -> None:
    cfg = tmp_path / ".pulsescribe"
    logs_dir = cfg / "logs"
    logs_dir.mkdir(parents=True)
    startup_log = cfg / "startup.log"
    startup_log.write_text(
        "\n".join(
            [
                "12:00:00 [INFO] Starting PulseScribe",
                "12:00:01 [INFO] Transkript: secret startup text",
                "12:00:02 [DEBUG] State: AppState.DONE text='still secret'",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(diagnostics, "_user_config_dir", lambda: cfg)
    monkeypatch.setattr(diagnostics.platform, "platform", lambda: "macOS-14.0-arm64")
    monkeypatch.setattr(
        diagnostics.platform,
        "mac_ver",
        lambda: ("14.0", ("", "", ""), "arm64"),
    )
    monkeypatch.setattr(diagnostics.platform, "machine", lambda: "arm64")
    monkeypatch.setattr(diagnostics.subprocess, "Popen", lambda *_args, **_kwargs: None)

    zip_path = diagnostics.export_diagnostics_report()

    with zipfile.ZipFile(zip_path) as zf:
        startup_tail = zf.read("logs/startup.log.tail.txt").decode("utf-8")

    assert "Starting PulseScribe" in startup_tail
    assert "Transkript: <redacted>" in startup_tail
    assert "text='<redacted>'" in startup_tail
    assert "secret startup text" not in startup_tail
    assert "still secret" not in startup_tail


def test_export_diagnostics_report_includes_sanitized_env_preferences_and_main_log(
    tmp_path, monkeypatch
) -> None:
    cfg = tmp_path / ".pulsescribe"
    logs_dir = cfg / "logs"
    logs_dir.mkdir(parents=True)
    (cfg / ".env").write_text(
        "\n".join(
            [
                "PULSESCRIBE_MODE=local",
                "DEEPGRAM_API_KEY=dg-secret-1234",
                "AUTH_TOKEN=abc123",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (cfg / "preferences.json").write_text(
        '{"show_welcome_on_startup": false}',
        encoding="utf-8",
    )
    (logs_dir / "pulsescribe.log").write_text(
        "\n".join(
            [
                "12:00:00 [INFO] Starting PulseScribe",
                "12:00:01 [INFO] Transkript: secret main text",
                "12:00:02 [DEBUG] State: AppState.DONE text='still secret'",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(diagnostics, "_user_config_dir", lambda: cfg)
    monkeypatch.setattr(diagnostics.platform, "platform", lambda: "macOS-14.0-arm64")
    monkeypatch.setattr(
        diagnostics.platform,
        "mac_ver",
        lambda: ("14.0", ("", "", ""), "arm64"),
    )
    monkeypatch.setattr(diagnostics.platform, "machine", lambda: "arm64")
    monkeypatch.setattr(diagnostics, "_get_app_version", lambda: "1.2.3")
    monkeypatch.setattr(
        diagnostics.time,
        "strftime",
        lambda fmt: {
            "%Y%m%d_%H%M%S": "20260409_150000",
            "%Y-%m-%d %H:%M:%S": "2026-04-09 15:00:00",
        }[fmt],
    )
    monkeypatch.setattr(diagnostics.subprocess, "Popen", lambda *_args, **_kwargs: None)

    zip_path = diagnostics.export_diagnostics_report()

    assert zip_path.name == "pulsescribe_diagnostics_20260409_150000.zip"

    with zipfile.ZipFile(zip_path) as zf:
        names = set(zf.namelist())
        report = json.loads(zf.read("report.json"))
        env_values = json.loads(zf.read("env_sanitized.json"))
        prefs = json.loads(zf.read("preferences.json"))
        log_tail = zf.read("logs/pulsescribe.log.tail.txt").decode("utf-8")

    assert names == {
        "report.json",
        "env_sanitized.json",
        "preferences.json",
        "logs/pulsescribe.log.tail.txt",
    }
    assert env_values == {
        "PULSESCRIBE_MODE": "local",
        "DEEPGRAM_API_KEY": "dg…1234",
        "AUTH_TOKEN": "********",
    }
    assert prefs == {"show_welcome_on_startup": False}
    assert report["app"] == {
        "name": "PulseScribe",
        "version": "1.2.3",
        "frozen": False,
    }
    assert report["settings"] == {
        "env_sanitized": env_values,
        "preferences": prefs,
    }
    assert report["paths"]["config_dir"] == str(cfg)
    assert report["paths"]["log_file"] == str(logs_dir / "pulsescribe.log")
    assert "Starting PulseScribe" in log_tail
    assert "Transkript: <redacted>" in log_tail
    assert "text='<redacted>'" in log_tail
    assert "secret main text" not in log_tail
    assert "still secret" not in log_tail


def test_export_diagnostics_report_ignores_invalid_preferences_json(
    tmp_path, monkeypatch
) -> None:
    cfg = tmp_path / ".pulsescribe"
    cfg.mkdir(parents=True)
    (cfg / "preferences.json").write_text("{invalid json", encoding="utf-8")

    monkeypatch.setattr(diagnostics, "_user_config_dir", lambda: cfg)
    monkeypatch.setattr(diagnostics.platform, "platform", lambda: "macOS-14.0-arm64")
    monkeypatch.setattr(
        diagnostics.platform,
        "mac_ver",
        lambda: ("14.0", ("", "", ""), "arm64"),
    )
    monkeypatch.setattr(diagnostics.platform, "machine", lambda: "arm64")
    monkeypatch.setattr(
        diagnostics.time,
        "strftime",
        lambda fmt: {
            "%Y%m%d_%H%M%S": "20260409_150100",
            "%Y-%m-%d %H:%M:%S": "2026-04-09 15:01:00",
        }[fmt],
    )
    monkeypatch.setattr(diagnostics.subprocess, "Popen", lambda *_args, **_kwargs: None)

    zip_path = diagnostics.export_diagnostics_report()

    with zipfile.ZipFile(zip_path) as zf:
        report = json.loads(zf.read("report.json"))
        assert "preferences.json" not in zf.namelist()

    assert report["settings"]["preferences"] == {}
