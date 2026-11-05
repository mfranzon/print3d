"""Draw a plate preview when the slicer's CLI leaves no thumbnail behind.

Bambu Studio writes `Metadata/plate_N.png`; a headless CLI slice does not -- its
thumbnail code renders through the GUI's GL framebuffer, so a slice run from the
command line (and every MCP/agent run) comes back with no picture of the plate
(upstream print3d issue #23). This draws one from `Metadata/plate_N.json`, which
both Bambu Studio and ElegooSlicer do write: the bed, each object as its filament
colour, and a caption. It is a preview, not a check, so anything it cannot read
is a `None`, never an exception.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from print3d.printers import printer_info
from print3d.project import read_plate_json

SIZE = 512
MARGIN = 10
BED = (236, 236, 236)
BED_EDGE = (176, 176, 176)
CAPTION = (32, 32, 32)
UNKNOWN_SWATCH = (128, 128, 128)


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """A real font when one is installed, else Pillow's built-in bitmap face."""
    for name in ("DejaVuSans.ttf", "LiberationSans-Regular.ttf", "Arial.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _hex_rgb(value: object) -> tuple[int, int, int]:
    text = str(value or "").strip().lstrip("#")
    if len(text) == 6:
        try:
            return (int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16))
        except ValueError:
            pass
    return UNKNOWN_SWATCH


def _bbox(value: object) -> list[float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        return [float(v) for v in value]
    except (TypeError, ValueError):
        return None


def draw_plate(
    project: Path,
    dest: Path,
    *,
    plate: int = 1,
    build_volume_mm: tuple[float, float, float] | None = None,
) -> Path | None:
    """Draw plate `plate` of a sliced project to `dest`, or None when there is nothing to draw."""
    data = read_plate_json(project, plate)
    if not data:
        return None
    if build_volume_mm is None:
        build_volume_mm = printer_info().bed_mm
    bed_x, bed_y = float(build_volume_mm[0]), float(build_volume_mm[1])
    if bed_x <= 0 or bed_y <= 0:
        return None

    span = SIZE - 2 * MARGIN
    canvas = Image.new("RGB", (SIZE, SIZE), BED)
    draw = ImageDraw.Draw(canvas)
    draw.rectangle([MARGIN, MARGIN, SIZE - 1 - MARGIN, SIZE - 1 - MARGIN], outline=BED_EDGE, width=2)

    def to_px(box: list[float]) -> list[float]:
        x0, y0, x1, y1 = box
        # Printer y grows towards the back of the bed and the image's grows
        # downward, so y is flipped to read like the slicer's own plate view.
        left = MARGIN + (min(x0, x1) / bed_x) * span
        right = MARGIN + (max(x0, x1) / bed_x) * span
        top = MARGIN + (1 - max(y0, y1) / bed_y) * span
        bottom = MARGIN + (1 - min(y0, y1) / bed_y) * span
        return [left, top, right, bottom]

    colours = data.get("filament_colors") or []
    objects = data.get("bbox_objects") or []
    drawn = 0
    for index, obj in enumerate(objects):
        box = _bbox(obj.get("bbox")) if isinstance(obj, dict) else None
        if box is None:
            continue
        swatch = _hex_rgb(colours[index % len(colours)]) if colours else UNKNOWN_SWATCH
        draw.rectangle(to_px(box), fill=swatch, outline=CAPTION)
        drawn += 1

    caption = f"Plate {plate} - {drawn} object" + ("" if drawn == 1 else "s")
    draw.text((MARGIN + 4, SIZE - MARGIN - 22), caption, fill=CAPTION, font=_font(18))

    dest.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(dest, format="PNG")
    return dest
