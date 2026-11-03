"""Regenerate macOS icons from assets/icon.svg with AppKit and iconutil.

Run with the project's Python environment on macOS. Windows' icon.ico is not
changed. SVG transparency is preserved; no white thumbnail background is used.
"""

import subprocess
from pathlib import Path

from AppKit import (
    NSBitmapImageRep,
    NSBitmapImageFileTypePNG,
    NSCalibratedRGBColorSpace,
    NSColor,
    NSCompositingOperationCopy,
    NSGraphicsContext,
    NSImage,
    NSImageInterpolationHigh,
    NSRectFillUsingOperation,
)


def render_png(image, size, *, padding=0.0):
    """Render SVG artwork on a transparent square with fractional canvas padding."""
    bitmap = NSBitmapImageRep.alloc().initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bytesPerRow_bitsPerPixel_(
        None, size, size, 8, 4, True, False, NSCalibratedRGBColorSpace, 0, 0
    )
    context = NSGraphicsContext.graphicsContextWithBitmapImageRep_(bitmap)
    inset = size * padding
    NSGraphicsContext.saveGraphicsState()
    try:
        NSGraphicsContext.setCurrentContext_(context)
        NSColor.clearColor().setFill()
        NSRectFillUsingOperation(((0, 0), (size, size)), NSCompositingOperationCopy)
        context.setImageInterpolation_(NSImageInterpolationHigh)
        image.drawInRect_fromRect_operation_fraction_(
            ((inset, inset), (size - 2 * inset, size - 2 * inset)),
            ((0, 0), image.size()),
            NSCompositingOperationCopy,
            1.0,
        )
    finally:
        NSGraphicsContext.restoreGraphicsState()
    return bytes(bitmap.representationUsingType_properties_(NSBitmapImageFileTypePNG, {}))


def main():
    """Write the base PNG, all ten iconset sizes, and the packaged ICNS."""
    assets = Path(__file__).resolve().parents[1] / "assets"
    image = NSImage.alloc().initWithContentsOfFile_(str(assets / "icon.svg"))
    if image is None:
        raise RuntimeError("Cannot load assets/icon.svg")
    (assets / "icon_base.png").write_bytes(render_png(image, 1024))
    iconset = assets / "icon.iconset"
    iconset.mkdir(exist_ok=True)
    for size in (16, 32, 128, 256, 512):
        for suffix, scale in (("", 1), ("@2x", 2)):
            # Keep the existing 96 px inset on the 1024 px macOS canvas.
            png = render_png(image, size * scale, padding=96 / 1024)
            (iconset / f"icon_{size}x{size}{suffix}.png").write_bytes(png)
    subprocess.run(
        ["iconutil", "-c", "icns", str(iconset), "-o", str(assets / "icon.icns")],
        check=True,
    )


if __name__ == "__main__":
    main()
