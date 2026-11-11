"""Which MLX build the packaged macOS app loads (utils.mlx_variant)."""

import struct
import sys

import pytest

from utils import mlx_variant

MH_MAGIC_64 = 0xFEEDFACF
LC_BUILD_VERSION = 0x32
LC_UUID = 0x1B
MACOS, IOS = 1, 2


def macho(*commands: bytes, magic: int = MH_MAGIC_64) -> bytes:
    """A 64-bit Mach-O header followed by the given load commands."""
    header = struct.pack("<8I", magic, 0x0100000C, 0, 6, len(commands), sum(map(len, commands)), 0, 0)
    return header + b"".join(commands)


def build_version(major: int, minor: int, *, platform: int = MACOS) -> bytes:
    return struct.pack("<6I", LC_BUILD_VERSION, 24, platform, (major << 16) | (minor << 8), 0, 0)


UUID = struct.pack("<II", LC_UUID, 24) + bytes(16)


def write_variant(bundle, *, core=(26, 2), libraries=((26, 2),), kernels=True):
    """A newer MLX build in ``bundle`` whose native files name the given minimum macOS."""
    variant = bundle / mlx_variant.VARIANT_DIR
    (variant / "lib").mkdir(parents=True)
    (variant / "core.cpython-312-darwin.so").write_bytes(macho(build_version(*core)))
    for index, minimum in enumerate(libraries):
        content = macho(build_version(*minimum)) if minimum else b"no mach-o"
        (variant / "lib" / f"lib{index}.dylib").write_bytes(content)
    if kernels:
        (variant / "lib" / "mlx.metallib").write_bytes(b"kernels")
    return variant


@pytest.fixture
def fresh(monkeypatch):
    """Each test starts with no decision made and gets its own import finders."""
    monkeypatch.setattr(mlx_variant, "_newer_core", None)
    monkeypatch.setattr(mlx_variant, "_decision", "default")
    monkeypatch.setattr(sys, "meta_path", list(sys.meta_path))


def on_macos(monkeypatch, version: str) -> None:
    monkeypatch.setattr(mlx_variant.platform, "mac_ver", lambda: (version, ("", "", ""), "arm64"))


class TestMachoMinMacos:
    def test_reads_the_minimum_after_other_commands(self, tmp_path):
        file = tmp_path / "lib.dylib"
        file.write_bytes(macho(UUID, build_version(26, 2)))
        assert mlx_variant.macho_min_macos(file) == (26, 2)

    @pytest.mark.parametrize(
        "content",
        [
            b"",
            b"plain text, long enough to hold a header of 32 bytes",
            macho(build_version(26, 2), magic=0xCAFEBABE),  # universal binary
            macho(UUID),  # names no version
            macho(build_version(26, 2, platform=IOS)),
            macho(build_version(26, 2))[:40],  # cut off inside the command
            macho(struct.pack("<II", LC_UUID, 4)),  # command shorter than its own header
        ],
        ids=["empty", "text", "universal", "no-version", "ios", "truncated", "bad-size"],
    )
    def test_none_without_a_readable_macos_minimum(self, tmp_path, content):
        file = tmp_path / "lib.dylib"
        file.write_bytes(content)
        assert mlx_variant.macho_min_macos(file) is None

    def test_none_for_a_missing_file(self, tmp_path):
        assert mlx_variant.macho_min_macos(tmp_path / "missing.dylib") is None


class TestVariantMinMacos:
    def test_highest_minimum_of_its_native_files(self, tmp_path):
        variant = write_variant(tmp_path, core=(26, 0), libraries=((26, 2), (15, 0)))
        assert mlx_variant.variant_min_macos(variant) == (26, 2)

    def test_none_without_the_build(self, tmp_path):
        assert mlx_variant.variant_min_macos(tmp_path / mlx_variant.VARIANT_DIR) is None

    def test_none_without_the_metal_kernels(self, tmp_path):
        assert mlx_variant.variant_min_macos(write_variant(tmp_path, kernels=False)) is None

    def test_none_without_libraries(self, tmp_path):
        assert mlx_variant.variant_min_macos(write_variant(tmp_path, libraries=())) is None

    def test_none_if_a_library_names_no_minimum(self, tmp_path):
        variant = write_variant(tmp_path, libraries=((26, 2), None))
        assert mlx_variant.variant_min_macos(variant) is None

    def test_none_with_two_extensions(self, tmp_path):
        variant = write_variant(tmp_path)
        (variant / "core.cpython-313-darwin.so").write_bytes(macho(build_version(26, 2)))
        assert mlx_variant.variant_min_macos(variant) is None


class TestUseNewerMacosMlx:
    @pytest.mark.parametrize("version", ["26.2", "26.2.1", "26.10", "27.0.1", "28"])
    def test_new_enough_macos_gets_the_newer_build(self, tmp_path, monkeypatch, fresh, version):
        variant = write_variant(tmp_path)
        on_macos(monkeypatch, version)
        finders = len(sys.meta_path)

        mlx_variant.use_newer_macos_mlx(tmp_path)

        assert mlx_variant.newer_macos_core() == variant / "core.cpython-312-darwin.so"
        assert mlx_variant.mlx_build() == "mlx_macos26 (macOS 26.2+)"
        assert len(sys.meta_path) == finders + 1
        assert isinstance(sys.meta_path[0], mlx_variant._NewerCoreFinder)  # ahead of the bundle

    @pytest.mark.parametrize("version", ["26.1", "26.1.9", "15.7.2", "14.0"])
    def test_older_macos_keeps_the_default(self, tmp_path, monkeypatch, fresh, version):
        write_variant(tmp_path)
        on_macos(monkeypatch, version)
        finders = list(sys.meta_path)

        mlx_variant.use_newer_macos_mlx(tmp_path)

        assert mlx_variant.newer_macos_core() is None
        assert sys.meta_path == finders
        assert mlx_variant.mlx_build() == (
            f"default (mlx_macos26 needs macOS 26.2, running {version})"
        )

    @pytest.mark.parametrize("version", ["", "beta"])
    def test_unreadable_macos_version_keeps_the_default(self, tmp_path, monkeypatch, fresh, version):
        write_variant(tmp_path)
        on_macos(monkeypatch, version)

        mlx_variant.use_newer_macos_mlx(tmp_path)

        assert mlx_variant.newer_macos_core() is None
        assert mlx_variant.mlx_build().startswith("default (mlx_macos26 needs macOS 26.2, running ")

    def test_bundle_without_the_newer_build_keeps_the_default(self, tmp_path, monkeypatch, fresh):
        on_macos(monkeypatch, "27.0")
        finders = list(sys.meta_path)

        mlx_variant.use_newer_macos_mlx(tmp_path)

        assert sys.meta_path == finders
        assert mlx_variant.mlx_build() == "default"

    def test_incomplete_newer_build_keeps_the_default(self, tmp_path, monkeypatch, fresh):
        write_variant(tmp_path, kernels=False)
        on_macos(monkeypatch, "27.0")

        mlx_variant.use_newer_macos_mlx(tmp_path)

        assert mlx_variant.newer_macos_core() is None
        assert mlx_variant.mlx_build() == "default"


class TestNewerCoreFinder:
    def test_leaves_every_other_module_alone(self, tmp_path):
        finder = mlx_variant._NewerCoreFinder(tmp_path / "core.cpython-312-darwin.so")
        for name in ("mlx", "mlx.nn", "mlx.core.metal", "mlx_whisper", "core", "numpy"):
            assert finder.find_spec(name) is None

    @pytest.mark.skipif(sys.platform != "darwin", reason="needs macOS extension suffixes")
    def test_answers_mlx_core_with_the_extension_file(self, tmp_path):
        import importlib.machinery

        core = tmp_path / f"core{importlib.machinery.EXTENSION_SUFFIXES[0]}"
        core.write_bytes(b"")

        spec = mlx_variant._NewerCoreFinder(core).find_spec("mlx.core", ["bundle/mlx"])

        assert spec is not None and spec.name == "mlx.core" and spec.origin == str(core)
        assert isinstance(spec.loader, importlib.machinery.ExtensionFileLoader)
