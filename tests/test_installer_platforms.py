import os
import runpy
import subprocess
import sys
from pathlib import Path

import pytest

from tests.test_release_workflow import run_block


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".github/scripts/installer_platforms.py"
SELECT = runpy.run_path(str(SCRIPT))["select_platforms"]


@pytest.mark.parametrize(
    "paths,expected",
    [
        (
            [
                "build_app.sh",
                "build_app.spec",
                "build_dmg.sh",
                "macos/entitlements.plist",
            ],
            (True, False),
        ),
        (
            [
                "build_windows.ps1",
                "build_windows.spec",
                "requirements-build-windows.txt",
            ],
            (False, True),
        ),
        (
            ["installer_windows.iss", "build_windows.py", "build_windows_light.spec"],
            (False, True),
        ),
        (
            ["build_dmg.sh", "docs/BUILDING_MACOS.md", "tests/test_version.py"],
            (True, False),
        ),
        (["build_windows.spec", "CHANGELOG.md", "README.md"], (False, True)),
        (["build_app.spec", "build_windows.spec"], (True, True)),
        (["docs/UPDATES.md", "tests/test_installer_platforms.py"], (False, False)),
    ]
    + [
        ([path], (True, True))
        for path in (
            "requirements.txt",
            "pyproject.toml",
            "config.py",
            "audio/recording.py",
            "providers/local.py",
            "ui/window.py",
            "utils/updates.py",
            "cli/__init__.py",
            "refine/prompt.py",
            "whisper_platform/__init__.py",
            "assets/icon.ico",
            "pulsescribe_daemon.py",
            "pulsescribe_windows.py",
            "transcribe.py",
            ".github/workflows/build-installers.yml",
            ".github/scripts/installer_platforms.py",
            "new-runtime-module.py",
            "unexpected\nname.py",
        )
    ],
)
def test_selection(paths, expected):
    assert SELECT(paths) == expected
    assert SELECT(list(reversed(paths))) == expected


@pytest.fixture
def repository(tmp_path):
    def git(*args):
        return subprocess.check_output(
            [
                "git",
                "-c",
                "user.name=Build test",
                "-c",
                "user.email=build-test@example.invalid",
                *args,
            ],
            cwd=tmp_path,
            text=True,
        ).strip()

    git("init", "-q")
    (tmp_path / "build_app.spec").write_text("macOS build\n")
    (tmp_path / "build_windows.spec").write_text("Windows build\n")
    git("add", ".")
    git("commit", "-qm", "Base")
    return tmp_path, git, git("rev-parse", "HEAD")


def detect(repository, base=None, head="HEAD"):
    directory, _, original_base = repository
    output = directory / "output"
    summary = directory / "summary"
    output.write_text("existing=value\n")
    summary.write_text("Build metadata\n")
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=directory,
        env={
            **os.environ,
            "PR_BASE_SHA": original_base if base is None else base,
            "PR_HEAD_SHA": head,
            "GITHUB_OUTPUT": str(output),
            "GITHUB_STEP_SUMMARY": str(summary),
        },
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert summary.read_text().startswith("Build metadata\n")
    values = dict(line.split("=", 1) for line in output.read_text().splitlines())
    assert values.pop("existing") == "value"
    return values


def test_uses_whole_pr_diff_not_only_last_commit(repository):
    directory, git, _ = repository
    (directory / "build_app.spec").write_text("changed macOS\n")
    git("commit", "-qam", "macOS change")
    (directory / "build_windows.spec").write_text("changed Windows\n")
    git("commit", "-qam", "Windows change")
    assert detect(repository) == {"build_macos": "true", "build_windows": "true"}


def test_uses_merge_base_and_detects_deleted_files(repository):
    directory, git, base = repository
    git("checkout", "-qb", "target")
    (directory / "shared.py").write_text("unrelated target-branch change\n")
    git("add", "shared.py")
    git("commit", "-qm", "Target advanced")
    target = git("rev-parse", "HEAD")
    git("checkout", "-qb", "pull-request", base)
    (directory / "build_windows.spec").unlink()
    git("commit", "-qam", "Windows deletion")
    assert detect(repository, base=target) == {
        "build_macos": "false",
        "build_windows": "true",
    }


def test_cross_platform_rename_checks_both_paths(repository):
    _, git, _ = repository
    git("mv", "build_app.spec", "build_windows_light.spec")
    git("commit", "-qm", "Cross-platform rename")
    assert detect(repository) == {"build_macos": "true", "build_windows": "true"}


@pytest.mark.parametrize(
    "base,head", [(None, "HEAD"), ("missing", "HEAD"), ("", "HEAD"), (None, "")]
)
def test_empty_or_unavailable_diff_builds_both(repository, base, head):
    assert detect(repository, base, head) == {
        "build_macos": "true",
        "build_windows": "true",
    }


def test_workflow_only_filters_and_cancels_pull_requests():
    workflow = (ROOT / ".github/workflows/build-installers.yml").read_text()
    concurrency = workflow.split("\nconcurrency:\n", 1)[1].split("\npermissions:", 1)[0]
    assert (
        "cancel-in-progress: ${{ github.event_name == 'pull_request' }}" in concurrency
    )
    assert "github.event_name == 'pull_request' && 'pr' || 'release'" in concurrency
    metadata = workflow.split("  metadata:\n", 1)[1].split("\n  build-macos:", 1)[0]
    assert "fetch-depth: 0" in metadata
    assert "if: github.event_name == 'pull_request'\n        id: platforms" in metadata
    assert "python .github/scripts/installer_platforms.py" in metadata
    assert "PR_BASE_SHA: ${{ github.event.pull_request.base.sha }}" in metadata
    assert "PR_HEAD_SHA: ${{ github.event.pull_request.head.sha }}" in metadata
    for platform in ("macos", "windows"):
        assert (
            f"build_{platform}: ${{{{ steps.platforms.outputs.build_{platform} }}}}"
            in metadata
        )
        job = workflow.split(f"\n  build-{platform}:\n", 1)[1].split("\n    steps:", 1)[
            0
        ]
        assert "    needs: metadata\n" in job
        assert (
            f"if: github.event_name != 'pull_request' || needs.metadata.outputs.build_{platform} != 'false'"
            in job
        )
    assert "  pull_request: {}\n" in workflow  # Unknown paths reach the selector.


@pytest.mark.skipif(sys.platform == "win32", reason="metadata uses bash")
@pytest.mark.parametrize("has_helper", [False, True])
def test_selector_step_handles_older_pr_heads(repository, has_helper):
    directory, git, base = repository
    if has_helper:
        helper = directory / ".github/scripts/installer_platforms.py"
        helper.parent.mkdir(parents=True)
        helper.write_text(SCRIPT.read_text())
    (directory / "build_app.spec").write_text("macOS change\n")
    git("commit", "-qam", "macOS change")
    output = directory / "output"
    subprocess.run(
        [
            "bash",
            "-eu",
            "-o",
            "pipefail",
            "-c",
            run_block("Select PR installer platforms"),
        ],
        cwd=directory,
        env={
            **os.environ,
            "PR_BASE_SHA": base,
            "PR_HEAD_SHA": "HEAD",
            "GITHUB_OUTPUT": str(output),
            "GITHUB_STEP_SUMMARY": str(directory / "summary"),
        },
        check=True,
        capture_output=True,
        timeout=10,
    )
    assert output.read_text().splitlines() == [
        "build_macos=true",
        f"build_windows={str(not has_helper).lower()}",
    ]
