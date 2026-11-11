import json
import os
import subprocess
import sys
import textwrap

import pytest

from tests.test_release_workflow import WORKFLOW, run_block


pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="DMG validation uses bash"
)


def validate_dmg(tmp_path, detach_statuses, *, fail_at="", newer_mlx="true"):
    """Run the workflow block with fake tools; never mount a volume or sleep."""
    binary = tmp_path / "bin"
    binary.mkdir()
    log = tmp_path / "calls.jsonl"
    mount = tmp_path / "mounted dmg"
    command = binary / "fake-command"
    command.write_text(
        f"#!{sys.executable}\n"
        + textwrap.dedent(
            """\
            import json
            import os
            import shutil
            import sys
            from pathlib import Path

            name = Path(sys.argv[0]).name
            args = sys.argv[1:]
            log = Path(os.environ["CALL_LOG"])
            calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
            with log.open("a") as stream:
                stream.write(json.dumps([name, *args]) + "\\n")
            fail_at = os.environ["FAIL_AT"]
            if name == fail_at:
                sys.exit(23)
            mount = Path(os.environ["MOUNT_POINT"])
            if name == "mktemp":
                mount.mkdir()
                print(mount)
            elif name == "hdiutil":
                if args[0] == "attach":
                    assert args == ["attach", "-readonly", "-nobrowse", "-mountpoint", str(mount), "dist/PulseScribe-1.7.1.dmg"]
                    executable = mount / "PulseScribe.app/Contents/MacOS/pulsescribe"
                    executable.parent.mkdir(parents=True)
                    executable.symlink_to(Path(__file__).resolve())
                    if fail_at != "newer-mlx":
                        library = mount / "PulseScribe.app/Contents/Frameworks/mlx_macos26/lib/libmlx.dylib"
                        library.parent.mkdir(parents=True)
                        library.write_text("MLX for newer macOS")
                elif args[0] == "detach":
                    assert args == ["detach", str(mount)]
                    attempts = sum(call[:2] == ["hdiutil", "detach"] for call in calls)
                    statuses = json.loads(os.environ["DETACH_STATUSES"])
                    status = statuses[min(attempts, len(statuses) - 1)]
                    if status == 0:
                        shutil.rmtree(mount / "PulseScribe.app")
                    sys.exit(status)
            elif name == "codesign" and args[:2] == ["-d", "-r-"]:
                certificate = os.environ["MACOS_SIGNING_CERT_SHA1"].lower()
                print(f'designated => identifier "com.kliebhan.pulsescribe" and certificate leaf = H"{certificate}"')
            elif name == "pulsescribe":
                assert args == ["--check-mlx-backend"]
                print("MLX backend OK: 80 and 128 mel filters on Device(gpu, 0)")
                if fail_at == "mlx-build":
                    print("MLX build: mlx_macos26 (macOS 26.2+)")
                else:
                    print("MLX build: default (mlx_macos26 needs macOS 26.2, running 15.8.2)")
            elif name == "plutil":
                print("0.0.0" if fail_at == "version" else os.environ["VERSION"])
            elif name == "file":
                print("arm64")
            elif name == "shasum":
                assert not mount.exists(), "Checksum must follow successful detach and cleanup"
                print("test checksum")
            """
        )
    )
    command.chmod(0o700)
    for name in ("mktemp", "hdiutil", "codesign", "plutil", "file", "sleep", "shasum"):
        (binary / name).symlink_to(command.name)
    (tmp_path / "dist").mkdir()
    (tmp_path / "dist/PulseScribe-1.7.1.dmg").write_text("test DMG")
    result = subprocess.run(
        ["bash", "-c", run_block("Validate DMG")],
        cwd=tmp_path,
        env={
            **os.environ,
            "PATH": f"{binary}{os.pathsep}{os.environ['PATH']}",
            "CALL_LOG": str(log),
            "MOUNT_POINT": str(mount),
            "DETACH_STATUSES": json.dumps(detach_statuses),
            "FAIL_AT": fail_at,
            "VERSION": "1.7.1",
            "PUBLISH": "true",
            "NEWER_MLX": newer_mlx,
            "MACOS_SIGNING_CERT_SHA1": "7241EB994E2F876853BEEE24D56EF802F9AE2AB0",
        },
        capture_output=True,
        text=True,
        timeout=10,
    )
    return result, [json.loads(line) for line in log.read_text().splitlines()]


@pytest.mark.parametrize(
    "statuses,expected,attempts",
    [
        ((0,), 0, 1),
        ((16, 0), 0, 2),
        ((16, 16, 0), 0, 3),
        ((7, 0), 7, 1),
        ((16, 7, 0), 7, 2),
        ((16,), 16, 3),
        ((16, 16, 16, 0), 16, 3),  # EXIT cleanup must not restart exhausted retries.
    ],
)
def test_detach_retries_only_busy_and_gates_checksums(
    tmp_path, statuses, expected, attempts
):
    result, calls = validate_dmg(tmp_path, statuses)

    assert result.returncode == expected, result.stderr
    detaches = [call for call in calls if call[:2] == ["hdiutil", "detach"]]
    assert detaches == [["hdiutil", "detach", str(tmp_path / "mounted dmg")]] * attempts
    assert [call for call in calls if call[0] == "sleep"] == [["sleep", "2"]] * (
        attempts - 1
    )
    assert len([call for call in calls if call[0] == "pulsescribe"]) == 1
    checksum = tmp_path / "dist/PulseScribe-1.7.1.dmg.sha256"
    if expected == 0:
        assert checksum.read_text() == "test checksum\n"
        assert not (tmp_path / "mounted dmg").exists()
        last_detach = max(i for i, call in enumerate(calls) if call[0] == "hdiutil")
        assert calls[last_detach + 1] == [
            "shasum",
            "-a",
            "256",
            "PulseScribe-1.7.1.dmg",
        ]
    else:
        assert not checksum.exists()
        assert not any(call[0] == "shasum" for call in calls)
        assert (tmp_path / "mounted dmg/PulseScribe.app").is_dir()


@pytest.mark.parametrize(
    "fail_at,expected",
    [
        ("codesign", 23),
        ("version", 1),
        ("pulsescribe", 23),
        ("newer-mlx", 1),  # the bundle lacks MLX's build for newer macOS
        ("mlx-build", 1),  # the app does not use the default build on this runner
    ],
)
@pytest.mark.parametrize(
    "statuses,attempts",
    [((0,), 1), ((16, 0), 2), ((7, 0), 1), ((16, 16, 16, 0), 3)],
)
def test_cleanup_retains_primary_validation_failure(
    tmp_path, fail_at, expected, statuses, attempts
):
    result, calls = validate_dmg(tmp_path, statuses, fail_at=fail_at)

    assert result.returncode == expected, result.stderr
    assert (
        len([call for call in calls if call[:2] == ["hdiutil", "detach"]]) == attempts
    )
    assert [call for call in calls if call[0] == "sleep"] == [["sleep", "2"]] * (
        attempts - 1
    )
    assert not any(call[0] == "shasum" for call in calls)
    assert not (tmp_path / "dist/PulseScribe-1.7.1.dmg.sha256").exists()
    assert (tmp_path / "mounted dmg").exists() == (statuses[attempts - 1] != 0)


def test_rebuilt_tag_from_before_the_second_mlx_build_still_validates(tmp_path):
    # Its source adds no second build, so the app has none.
    result, _calls = validate_dmg(tmp_path, (0,), fail_at="newer-mlx", newer_mlx="false")

    assert result.returncode == 0, result.stderr
    assert (tmp_path / "dist/PulseScribe-1.7.1.dmg.sha256").exists()


def test_second_mlx_build_is_checked_only_for_sources_that_add_it():
    workflow = WORKFLOW.read_text()
    macos = workflow.split("\n  build-macos:", 1)[1].split("\n  check-macos-26:", 1)[0]
    assert "newer_mlx: ${{ steps.newer-mlx.outputs.expected }}" in macos
    assert "NEWER_MLX: ${{ steps.newer-mlx.outputs.expected }}" in macos
    check = workflow.split("\n  check-macos-26:", 1)[1].split("\n  build-windows:", 1)[0]
    assert "    if: needs.build-macos.outputs.newer_mlx == 'true'\n" in check
    publish = workflow.split("\n  publish-release:", 1)[1]
    assert "check-macos-26" in publish.split("needs:", 1)[1].split("\n", 1)[0]


def test_macos_artifact_upload_requires_validation_success():
    macos = (
        WORKFLOW.read_text()
        .split("\n  build-macos:", 1)[1]
        .split("\n  build-windows:", 1)[0]
    )
    assert macos.index("- name: Validate DMG") < macos.index(
        "- name: Upload macOS build artifact"
    )
    upload = macos.split("- name: Upload macOS build artifact", 1)[1]
    assert "\n        if:" not in upload  # Keep the default success() gate.
    assert "dist/PulseScribe-${{ needs.metadata.outputs.version }}.dmg.sha256" in upload
