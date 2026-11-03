"""Check the actual shipped artwork, including every ICNS representation."""

import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="Uses macOS AppKit")

ASSETS = Path(__file__).resolve().parents[1] / "assets"
ICON_SIZES = {
    f"icon_{size}x{size}{suffix}.png": size * scale
    for size in (16, 32, 128, 256, 512)
    for suffix, scale in (("", 1), ("@2x", 2))
}


@pytest.mark.parametrize("filename,size", ICON_SIZES.items())
def test_iconset_has_transparent_rounded_corners(filename, size):
    from AppKit import NSBitmapImageRep

    reps = NSBitmapImageRep.imageRepsWithContentsOfFile_(
        str(ASSETS / "icon.iconset" / filename)
    )
    assert len(reps) == 1
    assert reps[0].pixelsWide() == reps[0].pixelsHigh() == size
    assert_transparent_corners(reps[0], padded=True)


def test_icns_has_transparent_rounded_corners_at_every_size():
    from AppKit import NSBitmapImageRep

    reps = NSBitmapImageRep.imageRepsWithContentsOfFile_(str(ASSETS / "icon.icns"))
    assert sorted(rep.pixelsWide() for rep in reps) == sorted(ICON_SIZES.values())
    for rep in reps:
        assert rep.pixelsWide() == rep.pixelsHigh()
        assert_transparent_corners(rep, padded=True)


def test_base_image_has_transparent_rounded_corners():
    from AppKit import NSBitmapImageRep

    rep = NSBitmapImageRep.imageRepWithContentsOfFile_(str(ASSETS / "icon_base.png"))
    assert rep.pixelsWide() == rep.pixelsHigh() == 1024
    assert_transparent_corners(rep, padded=False)


def assert_transparent_corners(rep, *, padded):
    """Sample inside the old white square, not just the transparent canvas."""
    size = rep.pixelsWide()
    corner = int(size * 0.11) if padded else 0
    assert rep.hasAlpha()
    for x in (corner, size - 1 - corner):
        for y in (corner, size - 1 - corner):
            assert rep.colorAtX_y_(x, y).alphaComponent() < 0.05, (size, x, y)
    # Keep the artwork opaque: an empty image must not pass the corner checks.
    assert rep.colorAtX_y_(size // 2, size // 2).alphaComponent() == 1.0
    if padded:
        assert rep.colorAtX_y_(0, size // 2).alphaComponent() == 0.0
        assert rep.colorAtX_y_(size // 2, int(size * 0.2)).alphaComponent() == 1.0
