"""Add MLX's build for newer macOS to the packaged app (macOS build step).

pip installs the MLX wheels that match the build machine's macOS, and the app
is built on an older macOS so that it starts there. On macOS 26.2 and newer
that build runs Whisper about 2.3 times slower than the one MLX publishes for
those systems. This step downloads the newer wheels of the same MLX version
and places their native files in the bundle, where ``utils.mlx_variant``
selects them at start. ``build_app.spec`` calls it after the bundle is
assembled and before the app is signed.

    python build_mlx_variant.py dist/PulseScribe.app
"""

from __future__ import annotations

import importlib.metadata
import os
import re
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

from utils.mlx_variant import VARIANT_DIR, variant_min_macos

# The wheels made for the newest macOS generation; pip picks them only on that macOS.
NEWER_PLATFORM = "macosx_26_0_arm64"
PACKAGES = ("mlx", "mlx-metal")
# Native files differ between the builds; the Python files must not.
_CODE_SUFFIXES = (".so", ".dylib")
_DATA_SUFFIXES = (".metallib",)


def installed_platform(package: str) -> str:
    """Platform tag of the installed wheel of ``package``, e.g. ``macosx_15_0_arm64``."""
    wheel = importlib.metadata.distribution(package).read_text("WHEEL") or ""
    tags = [line.split(":", 1)[1].strip() for line in wheel.splitlines() if line.startswith("Tag:")]
    if not tags:
        raise RuntimeError(f"{package}: installed wheel names no platform tag")
    return tags[0].split("-")[-1]


def macos_generation(platform_tag: str) -> int:
    """The macOS major version a wheel platform tag such as ``macosx_15_0_arm64`` is made for."""
    match = re.fullmatch(r"macosx_(\d+)_\d+_\w+", platform_tag)
    if match is None:
        raise RuntimeError(f"Not a macOS wheel platform: {platform_tag}")
    return int(match.group(1))


def download_newer_wheels(version: str, destination: Path) -> list[Path]:
    """Download the newer-macOS wheels of MLX ``version`` for this Python into ``destination``."""
    python = f"{sys.version_info.major}.{sys.version_info.minor}"
    subprocess.run(
        [
            sys.executable, "-m", "pip", "download", "--quiet", "--disable-pip-version-check", "--no-deps",
            "--only-binary=:all:", "--platform", NEWER_PLATFORM,
            "--python-version", python, "--implementation", "cp",
            "--abi", f"cp{python.replace('.', '')}", "--dest", str(destination),
            *(f"{package}=={version}" for package in PACKAGES),
        ],
        check=True,
    )
    return sorted(destination.glob("*.whl"))


def place_native_files(wheels: list[Path], app: Path, installed_mlx: Path) -> list[Path]:
    """Copy the wheels' native MLX files into ``app``; return the files written.

    Code goes to ``Contents/Frameworks/mlx_macos26`` and the Metal kernel
    library to ``Contents/Resources/mlx_macos26`` with a link beside the code,
    the layout PyInstaller uses so that the bundle can be signed. Fails if a
    Python file of the wheels differs from the installed MLX: the bundle has
    one copy of them for both builds.
    """
    code_root = app / "Contents" / "Frameworks" / VARIANT_DIR
    data_root = app / "Contents" / "Resources" / VARIANT_DIR
    written: list[Path] = []
    for wheel in wheels:
        with zipfile.ZipFile(wheel) as archive:
            for member in archive.namelist():
                if not member.startswith("mlx/"):
                    continue
                relative = Path(member).relative_to("mlx")
                if member.endswith(".py"):
                    if archive.read(member) != (installed_mlx / relative).read_bytes():
                        raise RuntimeError(
                            f"{wheel.name}: {member} differs from the installed MLX; "
                            "the two builds no longer share their Python code"
                        )
                elif member.endswith(_CODE_SUFFIXES):
                    target = code_root / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(archive.read(member))
                    target.chmod(0o755)
                    written.append(target)
                elif member.endswith(_DATA_SUFFIXES):
                    target = data_root / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(archive.read(member))
                    link = code_root / relative
                    link.parent.mkdir(parents=True, exist_ok=True)
                    link.unlink(missing_ok=True)
                    link.symlink_to(os.path.relpath(target, link.parent))
                    written.append(target)
    return written


def add_newer_macos_mlx(app: Path) -> None:
    """Add MLX's newer-macOS build to ``app`` unless the installed MLX already is that build."""
    version = importlib.metadata.version("mlx")
    if importlib.metadata.version("mlx-metal") != version:
        raise RuntimeError("mlx and mlx-metal are installed in different versions")
    base = installed_platform("mlx-metal")
    if macos_generation(base) >= macos_generation(NEWER_PLATFORM):
        print(f"MLX {version} is installed as the {base} build; the app runs only on that macOS.")
        return
    installed_mlx = Path(str(importlib.metadata.distribution("mlx").locate_file("mlx")))
    with tempfile.TemporaryDirectory() as downloads:
        wheels = download_newer_wheels(version, Path(downloads))
        if len(wheels) != len(PACKAGES):
            raise RuntimeError(f"Expected {len(PACKAGES)} MLX wheels for {NEWER_PLATFORM}, got {wheels}")
        written = place_native_files(wheels, app, installed_mlx)
    minimum = variant_min_macos(app / "Contents" / "Frameworks" / VARIANT_DIR)
    if minimum is None:
        raise RuntimeError(f"Incomplete MLX build for {NEWER_PLATFORM}: {written}")
    megabytes = sum(path.stat().st_size for path in written) / 1e6
    print(
        f"Added MLX {version} for macOS {minimum[0]}.{minimum[1]} and newer: "
        f"{len(written)} files, {megabytes:.0f} MB (base build: {base})"
    )


if __name__ == "__main__":
    add_newer_macos_mlx(Path(sys.argv[1]))
