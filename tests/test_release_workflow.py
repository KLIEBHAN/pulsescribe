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
    "latest,expected", [("v1.3.0", "true"), ("v1.5.0", "false"), ("v1.10.0", "false")]
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
elif sys.argv[1] == "api": print(os.environ["LATEST_TAG"])
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
    subprocess.run(
        ["bash", "-c", run_block("Upload assets and publish the complete release")],
        cwd=tmp_path,
        env=environment,
        check=True,
        capture_output=True,
        timeout=10,
    )
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    upload = next(call for call in calls if call[:2] == ["release", "upload"])
    assert "--clobber" in upload
    assert "release-assets/PulseScribe-1.4.0.dmg" in upload
    assert "release-assets/PulseScribe-Setup-1.4.0.exe" in upload
    assert "release-assets/SHA256SUMS.txt" in upload
    publication = next(call for call in calls if call[:2] == ["release", "edit"])
    assert f"--latest={expected}" in publication
    assert calls.index(upload) < calls.index(publication)


@pytest.mark.skipif(sys.platform == "win32", reason="release metadata uses bash")
@pytest.mark.parametrize("annotated", [False, True])
def test_tag_publication_requires_annotated_update_notes(tmp_path, annotated):
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
        "GITHUB_EVENT_NAME": "push",
        "REQUESTED_REF": "v1.4.0",
        "UPLOAD_TO_RELEASE": "false",
        "REPLACE_EXISTING_ASSETS": "false",
        "BUILD_WINDOWS_LOCAL": "false",
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
    if annotated:
        assert result.returncode == 0, result.stderr
        assert "publish=true" in output.read_text()
    else:
        assert result.returncode != 0
        assert b"Release tags must be annotated" in result.stderr
        assert not output.exists()
