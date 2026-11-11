"""The macOS build step that adds MLX's build for newer macOS to the app (build_mlx_variant)."""

import os
import struct
import sys
import zipfile
from types import SimpleNamespace

import pytest

import build_mlx_variant
from utils.mlx_variant import VARIANT_DIR

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="macOS build step, uses symlinks")

NEWER = build_mlx_variant.NEWER_PLATFORM


def macho(major: int, minor: int) -> bytes:
    """A minimal 64-bit Mach-O that names macOS major.minor as its minimum."""
    command = struct.pack("<6I", 0x32, 24, 1, (major << 16) | (minor << 8), 0, 0)
    return struct.pack("<8I", 0xFEEDFACF, 0x0100000C, 0, 6, 1, len(command), 0, 0) + command


def wheel(path, files: dict[str, bytes]):
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return path


@pytest.fixture
def app(tmp_path):
    bundle = tmp_path / "PulseScribe.app"
    (bundle / "Contents" / "Frameworks").mkdir(parents=True)
    (bundle / "Contents" / "Resources").mkdir()
    return bundle


@pytest.fixture
def installed_mlx(tmp_path):
    package = tmp_path / "site-packages" / "mlx"
    (package / "nn").mkdir(parents=True)
    (package / "utils.py").write_text("shared = 1\n")
    (package / "nn" / "__init__.py").write_text("layers = 2\n")
    return package


@pytest.fixture
def wheels(tmp_path):
    """The two wheels of the newer build, with the Python files of the installed MLX."""
    return [
        wheel(
            tmp_path / f"mlx-0.32.4-cp312-cp312-{NEWER}.whl",
            {
                "mlx/core.cpython-312-darwin.so": macho(26, 0),
                "mlx/utils.py": "shared = 1\n",
                "mlx/nn/__init__.py": "layers = 2\n",
                "mlx/core/__init__.pyi": "stubs",
                "mlx-0.32.4.dist-info/WHEEL": "Tag: x",
            },
        ),
        wheel(
            tmp_path / f"mlx_metal-0.32.4-py3-none-{NEWER}.whl",
            {
                "mlx/lib/libmlx.dylib": macho(26, 2),
                "mlx/lib/libjaccl.dylib": macho(26, 2),
                "mlx/lib/mlx.metallib": b"kernels",
                "mlx/include/mlx/array.h": "header",
            },
        ),
    ]


def install(monkeypatch, *, mlx="0.32.4", metal="0.32.4", platform="macosx_15_0_arm64", package=None):
    """Pretend these MLX wheels are installed on the build machine."""
    versions = {"mlx": mlx, "mlx-metal": metal}

    def distribution(name):
        tag = f"cp312-cp312-{platform}" if name == "mlx" else f"py3-none-{platform}"
        return SimpleNamespace(
            read_text=lambda file: f"Wheel-Version: 1.0\nTag: {tag}\n",
            locate_file=lambda path: package,
        )

    monkeypatch.setattr(build_mlx_variant.importlib.metadata, "version", versions.__getitem__)
    monkeypatch.setattr(build_mlx_variant.importlib.metadata, "distribution", distribution)


@pytest.mark.parametrize(
    "tag,generation",
    [("macosx_15_0_arm64", 15), ("macosx_26_0_arm64", 26), ("macosx_14_0_universal2", 14)],
)
def test_macos_generation_of_a_wheel_platform(tag, generation):
    assert build_mlx_variant.macos_generation(tag) == generation


@pytest.mark.parametrize("tag", ["any", "win_amd64", "manylinux_2_28_x86_64"])
def test_macos_generation_rejects_other_platforms(tag):
    with pytest.raises(RuntimeError, match="Not a macOS wheel platform"):
        build_mlx_variant.macos_generation(tag)


def test_installed_platform_reads_the_wheel_tag(monkeypatch):
    install(monkeypatch, platform="macosx_15_0_arm64")
    assert build_mlx_variant.installed_platform("mlx") == "macosx_15_0_arm64"
    assert build_mlx_variant.installed_platform("mlx-metal") == "macosx_15_0_arm64"


def test_installed_platform_fails_without_a_tag(monkeypatch):
    monkeypatch.setattr(
        build_mlx_variant.importlib.metadata,
        "distribution",
        lambda name: SimpleNamespace(read_text=lambda file: None),
    )
    with pytest.raises(RuntimeError, match="names no platform tag"):
        build_mlx_variant.installed_platform("mlx")


def test_download_asks_pip_for_the_newer_wheels_of_this_python(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(
        build_mlx_variant.subprocess, "run", lambda command, check: calls.append((command, check))
    )
    (tmp_path / "b.whl").write_bytes(b"")
    (tmp_path / "a.whl").write_bytes(b"")

    found = build_mlx_variant.download_newer_wheels("0.32.4", tmp_path)

    (command, check), = calls
    python = f"{sys.version_info.major}.{sys.version_info.minor}"
    assert check is True
    assert command[:4] == [sys.executable, "-m", "pip", "download"]
    assert command[-2:] == ["mlx==0.32.4", "mlx-metal==0.32.4"]
    for option, value in (
        ("--platform", NEWER),
        ("--python-version", python),
        ("--abi", f"cp{python.replace('.', '')}"),
        ("--dest", str(tmp_path)),
    ):
        assert command[command.index(option) + 1] == value
    assert "--no-deps" in command and "--only-binary=:all:" in command
    assert [path.name for path in found] == ["a.whl", "b.whl"]


def test_native_files_land_where_the_bundle_can_be_signed(app, installed_mlx, wheels):
    written = build_mlx_variant.place_native_files(wheels, app, installed_mlx)

    code = app / "Contents" / "Frameworks" / VARIANT_DIR
    data = app / "Contents" / "Resources" / VARIANT_DIR
    kernels = data / "lib" / "mlx.metallib"
    assert sorted(written) == sorted(
        [
            code / "core.cpython-312-darwin.so",
            code / "lib" / "libjaccl.dylib",
            code / "lib" / "libmlx.dylib",
            kernels,
        ]
    )
    for file in (code / "core.cpython-312-darwin.so", code / "lib" / "libmlx.dylib"):
        assert not file.is_symlink() and os.access(file, os.X_OK)
    # MLX looks for its kernels beside libmlx.dylib; data lives under Resources.
    link = code / "lib" / "mlx.metallib"
    assert link.is_symlink() and not os.path.isabs(os.readlink(link))
    assert link.resolve() == kernels.resolve() and link.read_bytes() == b"kernels"
    # Nothing but native files: Python code, stubs and headers stay with the default build.
    everything = {path.name for root in (code, data) for path in root.rglob("*") if not path.is_dir()}
    assert everything == {
        "core.cpython-312-darwin.so", "libjaccl.dylib", "libmlx.dylib", "mlx.metallib"
    }


def test_placing_twice_replaces_the_files(app, installed_mlx, wheels):
    build_mlx_variant.place_native_files(wheels, app, installed_mlx)
    build_mlx_variant.place_native_files(wheels, app, installed_mlx)

    link = app / "Contents" / "Frameworks" / VARIANT_DIR / "lib" / "mlx.metallib"
    assert link.is_symlink() and link.read_bytes() == b"kernels"


def test_differing_python_code_stops_the_build(app, installed_mlx, wheels):
    (installed_mlx / "utils.py").write_text("shared = 2\n")

    with pytest.raises(RuntimeError, match="mlx/utils.py differs from the installed MLX"):
        build_mlx_variant.place_native_files(wheels, app, installed_mlx)


def test_adds_the_newer_build_to_an_app_built_on_older_macos(
    monkeypatch, capsys, app, installed_mlx, wheels
):
    install(monkeypatch, package=installed_mlx)
    asked = []
    monkeypatch.setattr(
        build_mlx_variant,
        "download_newer_wheels",
        lambda version, destination: asked.append(version) or wheels,
    )

    build_mlx_variant.add_newer_macos_mlx(app)

    assert asked == ["0.32.4"]
    assert (app / "Contents" / "Frameworks" / VARIANT_DIR / "lib" / "libmlx.dylib").is_file()
    assert capsys.readouterr().out.startswith(
        "Added MLX 0.32.4 for macOS 26.2 and newer: 4 files, 0 MB (base build: macosx_15_0_arm64)"
    )


@pytest.mark.parametrize("platform", [NEWER, "macosx_27_0_arm64"])
def test_app_built_on_new_macos_needs_no_second_build(monkeypatch, capsys, app, platform):
    install(monkeypatch, platform=platform)
    monkeypatch.setattr(
        build_mlx_variant, "download_newer_wheels", lambda *args: pytest.fail("no download")
    )

    build_mlx_variant.add_newer_macos_mlx(app)

    assert not (app / "Contents" / "Frameworks" / VARIANT_DIR).exists()
    assert f"installed as the {platform} build" in capsys.readouterr().out


def test_mismatched_mlx_packages_stop_the_build(monkeypatch, app):
    install(monkeypatch, metal="0.32.3")
    with pytest.raises(RuntimeError, match="different versions"):
        build_mlx_variant.add_newer_macos_mlx(app)


def test_missing_wheel_stops_the_build(monkeypatch, app, installed_mlx, wheels):
    install(monkeypatch, package=installed_mlx)
    monkeypatch.setattr(build_mlx_variant, "download_newer_wheels", lambda *args: wheels[:1])
    with pytest.raises(RuntimeError, match="Expected 2 MLX wheels"):
        build_mlx_variant.add_newer_macos_mlx(app)


def test_wheels_without_a_complete_build_stop_the_build(monkeypatch, tmp_path, app, installed_mlx):
    install(monkeypatch, package=installed_mlx)
    incomplete = [
        wheel(tmp_path / "mlx.whl", {"mlx/core.cpython-312-darwin.so": macho(26, 0)}),
        wheel(tmp_path / "mlx_metal.whl", {"mlx/lib/libmlx.dylib": macho(26, 2)}),  # no kernels
    ]
    monkeypatch.setattr(build_mlx_variant, "download_newer_wheels", lambda *args: incomplete)
    with pytest.raises(RuntimeError, match="Incomplete MLX build"):
        build_mlx_variant.add_newer_macos_mlx(app)
