import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest


WORKFLOW = (
    Path(__file__).resolve().parents[1] / ".github/workflows/build-installers.yml"
)


def run_block(name):
    lines = WORKFLOW.read_text().splitlines()
    index = next(i for i, line in enumerate(lines) if line.strip() == f"- name: {name}")
    start = (
        next(i for i in range(index, len(lines)) if lines[i].strip() == "run: |") + 1
    )
    result = []
    for line in lines[start:]:
        if line and not line.startswith("          "):
            break
        result.append(line)
    return textwrap.dedent("\n".join(result))


@pytest.mark.skipif(sys.platform == "win32", reason="release job uses bash")
@pytest.mark.parametrize(
    "latest,expected",
    [
        ("v1.3.0", "true"),
        ("v1.5.0", "false"),
        ("v1.10.0", "false"),
        ("HTTP404", "true"),
        ("HTTP401", None),
        ("HTTP500", None),
    ],
)
def test_partial_draft_is_reuploaded_and_latest_never_moves_backwards(
    tmp_path, latest, expected
):
    binary = tmp_path / "bin"
    binary.mkdir()
    log = tmp_path / "calls.jsonl"
    gh = binary / "gh"
    gh.write_text(
        f"#!{sys.executable}\n"
        + """import json, os, sys
with open(os.environ["CALL_LOG"], "a") as file:
    file.write(json.dumps(sys.argv[1:]) + "\\n")
if sys.argv[1:3] == ["release", "view"]:
    if "isDraft" in sys.argv: print("true")
    elif "assets" in sys.argv: print("PulseScribe-1.4.0.dmg")
elif sys.argv[1] == "api":
    latest = os.environ["LATEST_TAG"]
    if latest.startswith("HTTP"):
        print(f"gh: API failed (HTTP {latest[4:]})", file=sys.stderr)
        sys.exit(1)
    print(latest)
"""
    )
    gh.chmod(0o700)
    source = tmp_path / "release-source/utils"
    source.mkdir(parents=True)
    (source / "updates.py").write_text(
        (WORKFLOW.parents[2] / "utils/updates.py").read_text()
    )
    environment = {
        **os.environ,
        "PATH": f"{binary}{os.pathsep}{os.environ['PATH']}",
        "CALL_LOG": str(log),
        "LATEST_TAG": latest,
        "CLOBBER": "false",
        "GITHUB_EVENT_NAME": "push",
        "RELEASE_TAG": "v1.4.0",
        "VERSION": "1.4.0",
        "GITHUB_REPOSITORY": "KLIEBHAN/pulsescribe",
        "LOCAL_INSTALLER": "false",
    }
    result = subprocess.run(
        ["bash", "-c", run_block("Upload assets and publish the complete release")],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        timeout=10,
    )
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    upload = next(call for call in calls if call[:2] == ["release", "upload"])
    assert "--clobber" in upload
    assert "release-assets/PulseScribe-1.4.0.dmg" in upload
    assert "release-assets/PulseScribe-Setup-1.4.0.exe" in upload
    assert "release-assets/SHA256SUMS.txt" in upload
    if expected is None:
        assert result.returncode != 0
        assert not any(call[:2] == ["release", "edit"] for call in calls)
        assert f"HTTP {latest[4:]}".encode() in result.stderr
        return
    assert result.returncode == 0, result.stderr
    publication = next(call for call in calls if call[:2] == ["release", "edit"])
    assert f"--latest={expected}" in publication
    assert calls.index(upload) < calls.index(publication)


def _release_metadata(tmp_path, *, event, annotated=True, build_local="false"):
    """Run the metadata step against a temporary repository with tag v1.4.0."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "1.4.0"\n')
    subprocess.run(["git", "add", "pyproject.toml"], cwd=tmp_path, check=True)
    git = [
        "git",
        "-c",
        "user.name=Release test",
        "-c",
        "user.email=release-test@example.invalid",
    ]
    subprocess.run([*git, "commit", "-qm", "Test release"], cwd=tmp_path, check=True)
    tag = [*git, "tag", "v1.4.0"]
    if annotated:
        tag += ["-a", "-m", "User-facing update notes"]
    subprocess.run(tag, cwd=tmp_path, check=True)
    output = tmp_path / "output"
    environment = {
        **os.environ,
        "GITHUB_EVENT_NAME": event,
        "REQUESTED_REF": "v1.4.0",
        "UPLOAD_TO_RELEASE": "false",
        "REPLACE_EXISTING_ASSETS": "false",
        "BUILD_WINDOWS_LOCAL": build_local,
        "GITHUB_OUTPUT": str(output),
        "GITHUB_STEP_SUMMARY": str(tmp_path / "summary"),
    }
    result = subprocess.run(
        ["bash", "-c", run_block("Validate ref, version, and commit")],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        timeout=10,
    )
    return result, output


@pytest.mark.skipif(sys.platform == "win32", reason="release metadata uses bash")
@pytest.mark.parametrize("annotated", [False, True])
def test_tag_publication_requires_annotated_update_notes(tmp_path, annotated):
    result, output = _release_metadata(tmp_path, event="push", annotated=annotated)
    if annotated:
        assert result.returncode == 0, result.stderr
        assert "publish=true" in output.read_text()
    else:
        assert result.returncode != 0
        assert b"Release tags must be annotated" in result.stderr
        assert not output.exists()


@pytest.mark.skipif(sys.platform == "win32", reason="release metadata uses bash")
@pytest.mark.parametrize(
    ("event", "build_local", "expected"),
    [
        ("push", "false", "true"),  # Local installs only accept the Local package
        ("release", "false", "true"),
        ("workflow_dispatch", "false", "false"),
        ("workflow_dispatch", "true", "true"),
    ],
)
def test_published_releases_always_include_local_package(
    tmp_path, event, build_local, expected
):
    result, output = _release_metadata(tmp_path, event=event, build_local=build_local)

    assert result.returncode == 0, result.stderr
    assert f"build_windows_local={expected}" in output.read_text().splitlines()


@pytest.mark.skipif(sys.platform != "win32", reason="build preparation uses PowerShell")
@pytest.mark.parametrize("legacy_tag", [False, True])
def test_windows_cache_inputs_match_installed_requirements_and_differ_by_variant(
    tmp_path, legacy_tag
):
    root = WORKFLOW.parents[2]
    runtime = (root / "requirements.txt").read_text()
    (tmp_path / "requirements.txt").write_text(runtime, encoding="utf-8")
    if legacy_tag:
        tools = "pyinstaller>=6,<7\nPySide6>=6,<7\n"
    else:
        tools = (root / "requirements-build-windows.txt").read_text()
        (tmp_path / "requirements-build-windows.txt").write_text(tools, encoding="utf-8")

    def prepare(variant):
        subprocess.run(
            [
                "pwsh",
                "-NoProfile",
                "-Command",
                run_block(f"Prepare {variant} build requirements"),
            ],
            cwd=tmp_path,
            check=True,
            capture_output=True,
            timeout=15,
        )
        return (tmp_path / "requirements-windows-build.txt").read_text(encoding="utf-8-sig")

    local = prepare("Local Whisper")
    cloud = prepare("API-only")
    assert local.splitlines() == (runtime + tools).splitlines()
    excluded = ("openai-whisper", "faster-whisper", "lightning-whisper-mlx")
    assert cloud.splitlines() == [
        line for line in local.splitlines() if not line.startswith(excluded)
    ]
    assert cloud != local
    assert prepare("API-only") == cloud  # Reruns overwrite, rather than append.


@pytest.mark.parametrize(
    "job,variant",
    [("build-windows", "API-only"), ("build-windows-local", "Local Whisper")],
)
def test_windows_cache_hashes_the_prepared_install_input(job, variant):
    workflow = WORKFLOW.read_text()
    section = workflow.split(f"\n  {job}:\n", 1)[1]
    section = section.split("\n  build-windows-local:", 1)[0]
    section = section.split("\n  publish-release:", 1)[0]
    assert section.index(f"Prepare {variant} build requirements") < section.index(
        "name: Set up Python"
    )
    assert "cache-dependency-path: requirements-windows-build.txt" in section
    assert "python -m pip install -r requirements-windows-build.txt" in section
    assert "      - requirements-build-windows.txt\n" in workflow
