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
