"""Select PR installer jobs; unknown inputs keep both platforms enabled."""

import os
import subprocess
from pathlib import Path


MACOS_BUILD_FILES = {"build_app.sh", "build_app.spec", "build_dmg.sh"}
WINDOWS_BUILD_FILES = {
    "build_windows.ps1",
    "build_windows.py",
    "build_windows.spec",
    "build_windows_light.spec",
    "installer_windows.iss",
    "requirements-build-windows.txt",
}
DOCUMENTATION_FILES = {
    "README.md",
    "README.de.md",
    "CONTRIBUTING.md",
    "CHANGELOG.md",
    "LICENSE",
}


def select_platforms(paths):
    """Skip a platform only for known build-only or non-runtime changes."""
    macos = windows = False
    for path in paths:
        if path in MACOS_BUILD_FILES or path.startswith("macos/"):
            macos = True
        elif path in WINDOWS_BUILD_FILES:
            windows = True
        elif path in DOCUMENTATION_FILES or path.startswith(("docs/", "tests/")):
            continue
        else:
            return True, True
    return macos, windows


def detect_platforms(base, head):
    """Compare the whole PR, including both paths of renames; fail open."""
    if not base or not head:
        return True, True
    try:
        result = subprocess.run(
            [
                "git",
                "diff",
                "--name-only",
                "--no-renames",
                "-z",
                f"{base}...{head}",
                "--",
            ],
            check=True,
            capture_output=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        print("::warning::Cannot resolve PR diff; building both installers")
        return True, True
    paths = os.fsdecode(result.stdout).split("\0")[:-1]
    # An unexpectedly empty diff must not silently remove all packaging checks.
    return select_platforms(paths) if paths else (True, True)


def main():
    macos, windows = detect_platforms(
        os.environ.get("PR_BASE_SHA"), os.environ.get("PR_HEAD_SHA")
    )
    with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as output:
        output.write(f"build_macos={str(macos).lower()}\n")
        output.write(f"build_windows={str(windows).lower()}\n")
    with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a", encoding="utf-8") as summary:
        summary.write(f"\n- PR installers: macOS={macos}, Windows={windows}\n")


if __name__ == "__main__":
    main()
