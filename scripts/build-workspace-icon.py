"""Rebuild Workspace's vector, browser, and Windows icon assets.

Requires Pillow only for development; generated assets have no runtime dependency.
Run ``python scripts/build-workspace-icon.py`` or add ``--check`` to verify them.
Every ICO frame is rendered from geometry at its own size, never upscaled from a
small favicon. Small Windows frames use 32-bit DIB plus an AND mask; larger frames
use lossless RGBA PNG. The SVG uses the same geometry and palette.
"""

from __future__ import annotations

import argparse
from io import BytesIO
from pathlib import Path
import struct

from PIL import Image, ImageDraw


ICON_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)
BROWSER_SIZES = (192, 512)
INDIGO = "#626ad7"
WHITE = "#ffffff"
SPARK = ((32, 12), (37.4, 26.6), (52, 32), (37.4, 37.4),
         (32, 52), (26.6, 37.4), (12, 32), (26.6, 26.6))
SVG = f'''<svg xmlns="http://www.w3.org/2000/svg" width="512" height="512" viewBox="0 0 64 64">
  <title>Company Workspace</title>
  <rect x="2" y="2" width="60" height="60" rx="14" fill="{INDIGO}"/>
  <path d="M32 12 37.4 26.6 52 32 37.4 37.4 32 52 26.6 37.4 12 32 26.6 26.6Z" fill="{WHITE}"/>
</svg>
'''


def render(size: int) -> Image.Image:
    """Render the flat silhouette with antialiasing and transparent corners."""
    scale = 8
    side = size * scale
    unit = side / 64
    image = Image.new("RGBA", (side, side))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle(
        (2 * unit, 2 * unit, 62 * unit, 62 * unit),
        radius=14 * unit, fill=INDIGO,
    )
    draw.polygon([(x * unit, y * unit) for x, y in SPARK], fill=WHITE)
    image = image.resize((size, size), Image.Resampling.LANCZOS)
    # Remove tiny resampling halos outside the silhouette while retaining the
    # antialiased edge. Fully transparent corners matter for both shell themes.
    image.putalpha(image.getchannel("A").point(lambda value: 0 if value < 4 else value))
    return image


def png_bytes(image: Image.Image) -> bytes:
    output = BytesIO()
    image.save(output, "PNG", optimize=True)
    return output.getvalue()


def dib_bytes(image: Image.Image) -> bytes:
    """Encode a standard ICO bitmap, including its transparency AND mask."""
    width, height = image.size
    bottom_up = image.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
    xor = bottom_up.tobytes("raw", "BGRA")
    stride = ((width + 31) // 32) * 4
    mask = bytearray(stride * height)
    alpha = bottom_up.getchannel("A")
    for y in range(height):
        for x in range(width):
            if alpha.getpixel((x, y)) == 0:
                mask[y * stride + x // 8] |= 0x80 >> (x % 8)
    header = struct.pack(
        "<IiiHHIIiiII", 40, width, height * 2, 1, 32,
        0, len(xor), 0, 0, 0, 0,
    )
    return header + xor + bytes(mask)


def ico_bytes() -> bytes:
    images = [render(size) for size in ICON_SIZES]
    payloads = [dib_bytes(im) if im.width <= 48 else png_bytes(im) for im in images]
    header = struct.pack("<HHH", 0, 1, len(images))
    directory = bytearray()
    offset = 6 + 16 * len(images)
    for image, payload in zip(images, payloads):
        size = image.width if image.width < 256 else 0
        directory.extend(struct.pack("<BBBBHHII", size, size, 0, 0, 1, 32, len(payload), offset))
        offset += len(payload)
    return header + bytes(directory) + b"".join(payloads)


def assets() -> dict[str, bytes]:
    return {
        "icon.svg": SVG.encode("utf-8"),
        "app-icon.ico": ico_bytes(),
        **{f"app-icon-{size}.png": png_bytes(render(size)) for size in BROWSER_SIZES},
    }


def validate(outputs: dict[str, bytes]) -> None:
    icon = Image.open(BytesIO(outputs["app-icon.ico"]))
    expected = {(size, size) for size in ICON_SIZES}
    if icon.ico.sizes() != expected:
        raise ValueError(f"Incorrect ICO frames: {icon.ico.sizes()}")
    for size in ICON_SIZES:
        image = icon.ico.getimage((size, size)).convert("RGBA")
        if image.getpixel((0, 0))[3] != 0:
            raise ValueError(f"ICO frame {size} must have transparent corners")
        center = image.getpixel((size // 2, size // 2))
        if min(center[:3]) < 250 or center[3] != 255:
            raise ValueError(f"ICO frame {size} lost its high-contrast white center")
    for size in BROWSER_SIZES:
        image = Image.open(BytesIO(outputs[f"app-icon-{size}.png"]))
        if image.size != (size, size) or image.mode != "RGBA":
            raise ValueError(f"Incorrect browser icon: {size}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Validate checked-in files without changing them")
    options = parser.parse_args()
    root = Path(__file__).resolve().parents[1] / "local_app" / "web"
    outputs = assets()
    validate(outputs)
    for name, payload in outputs.items():
        target = root / name
        if options.check:
            if not target.is_file() or target.read_bytes() != payload:
                raise SystemExit(f"Icon asset needs rebuilding: {target}")
        else:
            target.write_bytes(payload)
    action = "Verified" if options.check else "Generated"
    print(f"{action} SVG, PNG 192/512, and ICO frames {', '.join(map(str, ICON_SIZES))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
