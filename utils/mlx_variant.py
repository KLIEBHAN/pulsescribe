"""The MLX build the packaged macOS app runs: the one made for the running macOS.

MLX publishes its native code per macOS generation. The app is built on an
older macOS so that it starts there, but that build runs Whisper about 2.3
times slower on macOS 26.2 and newer than the build made for those systems.
The bundle therefore also carries the newer build's native files in
``mlx_macos26`` (added by ``build_mlx_variant.py``). ``use_newer_macos_mlx()``
makes ``import mlx.core`` load them where the system is new enough; everything
else of MLX is the same Python code for both builds.
"""

from __future__ import annotations

import importlib.util
import platform
import struct
import sys
from importlib.abc import MetaPathFinder
from pathlib import Path

# Directory of the newer build's native files, beside the bundled ``mlx`` package.
VARIANT_DIR = "mlx_macos26"

_MH_MAGIC_64 = 0xFEEDFACF
_LC_BUILD_VERSION = 0x32
_PLATFORM_MACOS = 1

# What use_newer_macos_mlx() decided: the extension it redirects to, and a text for logs.
_newer_core: Path | None = None
_decision = "default"


def macho_min_macos(path: Path) -> tuple[int, int] | None:
    """Lowest macOS version (major, minor) a 64-bit Mach-O file declares to run on.

    None if the file is no thin little-endian 64-bit Mach-O or names no macOS version.
    """
    try:
        with open(path, "rb") as file:
            header = file.read(32)
            if len(header) < 32 or struct.unpack_from("<I", header)[0] != _MH_MAGIC_64:
                return None
            for _ in range(struct.unpack_from("<I", header, 16)[0]):
                command = file.read(8)
                if len(command) < 8:
                    return None
                kind, size = struct.unpack("<II", command)
                if size < 8:
                    return None
                body = file.read(size - 8)
                if len(body) < size - 8:
                    return None  # cut off inside a load command
                if kind == _LC_BUILD_VERSION and len(body) >= 8:
                    target, minimum = struct.unpack_from("<II", body)
                    if target == _PLATFORM_MACOS:
                        return minimum >> 16, (minimum >> 8) & 0xFF
    except OSError:
        return None
    return None


def variant_min_macos(variant: Path) -> tuple[int, int] | None:
    """Lowest macOS the build in ``variant`` runs on, or None if the build is incomplete.

    Complete means one ``mlx.core`` extension, its libraries and the Metal
    kernel library, each native file naming its minimum macOS.
    """
    cores = list(variant.glob("core.*.so"))
    libraries = list((variant / "lib").glob("*.dylib"))
    if len(cores) != 1 or not libraries or not (variant / "lib" / "mlx.metallib").is_file():
        return None
    minimums = [macho_min_macos(path) for path in (*cores, *libraries)]
    if any(minimum is None for minimum in minimums):
        return None
    return max(minimum for minimum in minimums if minimum is not None)


def running_macos() -> tuple[int, int] | None:
    """The running macOS version as (major, minor), or None if it cannot be read."""
    parts = platform.mac_ver()[0].split(".")
    try:
        return int(parts[0]), int(parts[1]) if len(parts) > 1 else 0
    except ValueError:
        return None


class _NewerCoreFinder(MetaPathFinder):
    """Answers ``import mlx.core`` with the newer build's extension module."""

    def __init__(self, core: Path) -> None:
        self._core = core

    def find_spec(self, fullname, path=None, target=None):
        if fullname != "mlx.core":
            return None
        return importlib.util.spec_from_file_location(fullname, self._core)


def use_newer_macos_mlx(bundle_dir: Path) -> None:
    """Make ``import mlx.core`` load the newer macOS build from ``bundle_dir`` where it runs.

    Call it once, before anything imports MLX. Without a complete newer build
    in the bundle, or on an older macOS, the bundled default stays in place.
    """
    global _newer_core, _decision
    variant = bundle_dir / VARIANT_DIR
    minimum = variant_min_macos(variant)
    if minimum is None:
        return
    needs = f"macOS {minimum[0]}.{minimum[1]}"
    macos = running_macos()
    if macos is None or macos < minimum:
        running = platform.mac_ver()[0] or "an unknown version"
        _decision = f"default ({VARIANT_DIR} needs {needs}, running {running})"
        return
    _newer_core = next(variant.glob("core.*.so"))
    sys.meta_path.insert(0, _NewerCoreFinder(_newer_core))
    _decision = f"{VARIANT_DIR} ({needs}+)"


def newer_macos_core() -> Path | None:
    """The extension ``import mlx.core`` is redirected to, or None with the default build."""
    return _newer_core


def mlx_build() -> str:
    """Which MLX build ``use_newer_macos_mlx()`` chose, as a short text for logs.

    ``default``, ``default (mlx_macos26 needs macOS 26.2, running 15.6)`` or
    ``mlx_macos26 (macOS 26.2+)``.
    """
    return _decision
