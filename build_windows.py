#!/usr/bin/env python3
"""
Build script for PulseScribe Windows.

Usage:
    python build_windows.py              # Build both versions
    python build_windows.py --full       # Build full version only (with local Whisper)
    python build_windows.py --light      # Build light version only (API-only, smaller)
    python build_windows.py --clean      # Clean build artifacts before building

Output:
    dist/PulseScribe/           Full version (~800MB, includes local Whisper)
    dist/PulseScribe-Light/     Light version (~150MB, API-only)
"""

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path


def get_venv_pyinstaller() -> Path | None:
    """Find PyInstaller in venv."""
    venv_path = Path(__file__).parent / "venv" / "Scripts" / "pyinstaller.exe"
    if venv_path.exists():
        return venv_path
    return None


def get_pyinstaller() -> str:
    """Get PyInstaller executable path."""
    # Prefer venv PyInstaller (has all dependencies)
    venv_pi = get_venv_pyinstaller()
    if venv_pi:
        return str(venv_pi)

    # Fallback to system PyInstaller
    return "pyinstaller"


def clean_build_artifacts() -> None:
    """Remove build and dist directories."""
    root = Path(__file__).parent

    for folder in ["build", "dist"]:
        path = root / folder
        if path.exists():
            print(f"Removing {folder}/...")
            shutil.rmtree(path)

    # Remove __pycache__ directories
    for pycache in root.rglob("__pycache__"):
        if pycache.is_dir():
            shutil.rmtree(pycache)

    print("Clean complete.")


def build_variant(spec_file: str, variant_name: str, *, build_local: bool) -> bool:
    """Build a specific variant."""
    root = Path(__file__).parent
    spec_path = root / spec_file

    if not spec_path.exists():
        print(f"ERROR: {spec_file} not found!")
        return False

    pyinstaller = get_pyinstaller()
    print(f"\n{'='*60}")
    print(f"Building {variant_name}...")
    print(f"Spec: {spec_file}")
    print(f"PyInstaller: {pyinstaller}")
    print(f"{'='*60}\n")

    start_time = time.time()
    env = os.environ.copy()
    env["PULSESCRIBE_BUILD_LOCAL"] = "1" if build_local else "0"

    try:
        subprocess.run(
            [pyinstaller, spec_file, "--clean", "--noconfirm"],
            cwd=str(root),
            env=env,
            check=True,
        )

        elapsed = time.time() - start_time
        print(f"\n{variant_name} build completed in {elapsed:.1f}s")
        return True

    except subprocess.CalledProcessError as e:
        print(f"\nERROR: {variant_name} build failed with exit code {e.returncode}")
        return False
    except FileNotFoundError:
        print("\nERROR: PyInstaller not found. Install with: pip install pyinstaller")
        return False


def get_folder_size(path: Path) -> str:
    """Get human-readable folder size."""
    if not path.exists():
        return "N/A"

    total = sum(f.stat().st_size for f in path.rglob("*") if f.is_file())

    if total >= 1024 * 1024 * 1024:
        return f"{total / (1024**3):.1f} GB"
    elif total >= 1024 * 1024:
        return f"{total / (1024**2):.0f} MB"
    elif total >= 1024:
        return f"{total / 1024:.0f} KB"
    return f"{total} bytes"


def print_summary() -> None:
    """Print build summary with sizes."""
    root = Path(__file__).parent / "dist"

    print(f"\n{'='*60}")
    print("BUILD SUMMARY")
    print(f"{'='*60}")

    variants = [
        ("PulseScribe", "Full (with local Whisper)"),
        ("PulseScribe-Light", "Light (API-only)"),
    ]

    for folder, description in variants:
        path = root / folder
        exe_path = path / "PulseScribe.exe"

        if path.exists():
            size = get_folder_size(path)
            exe_exists = "YES" if exe_path.exists() else "NO"
            print(f"\n{folder}/")
            print(f"  Description: {description}")
            print(f"  Size: {size}")
            print(f"  EXE exists: {exe_exists}")
        else:
            print(f"\n{folder}/ - NOT BUILT")

    print(f"\n{'='*60}")


def main():
    parser = argparse.ArgumentParser(
        description="Build PulseScribe Windows EXE",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="Build full version only (with local Whisper, ~800MB)"
    )
    parser.add_argument(
        "--light",
        action="store_true",
        help="Build light version only (API-only, ~150MB)"
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Clean build artifacts before building"
    )

    args = parser.parse_args()

    # If neither specified, build both
    build_full = args.full or (not args.full and not args.light)
    build_light = args.light or (not args.full and not args.light)

    if args.clean:
        clean_build_artifacts()

    results = []

    if build_full:
        success = build_variant(
            "build_windows.spec",
            "Full Version",
            build_local=True,
        )
        results.append(("Full", success))

    if build_light:
        success = build_variant(
            "build_windows_light.spec",
            "Light Version",
            build_local=False,
        )
        results.append(("Light", success))

    print_summary()

    # Exit with error if any build failed
    if not all(success for _, success in results):
        print("\nSome builds failed!")
        sys.exit(1)

    print("\nAll builds completed successfully!")


if __name__ == "__main__":
    main()
