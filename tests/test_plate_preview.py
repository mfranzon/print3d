"""The plate preview drawn when a headless slice leaves no thumbnail behind.

`Metadata/plate_N.png` is only written by the slicer's GUI (it renders through
the GL framebuffer), so every CLI/MCP slice comes back with a null `plate_png`
(upstream print3d issue #23). These tests cover the fallback: reading the
`Metadata/plate_N.json` both slicers do write, and drawing a 512 PNG from it.
"""

import json
import zipfile
from pathlib import Path

from PIL import Image

from print3d.plate_preview import draw_plate
from print3d.project import extract_plate_png, read_plate_json

GOLDEN = Path(__file__).parent / "fixtures" / "cube_20mm.gcode.3mf"

PLATE_JSON = {
    "bbox_all": [128.0, 118.0, 170.0, 138.0],
    "bbox_objects": [
        {"id": 28, "name": "cube.stl", "bbox": [128.0, 118.0, 148.0, 138.0]},
        {"id": 29, "name": "cube2.stl", "bbox": [150.0, 118.0, 170.0, 138.0]},
    ],
    "filament_colors": ["#00AE42", "#FF0000"],
    "bed_type": "textured_plate",
}

GCODE = """\
; HEADER_BLOCK_START
; BambuStudio 01.10.00.00
; total layer number: 50
; total filament weight [g] = 4.01
; HEADER_BLOCK_END
G1 Z0.2
G1 X10 Y10 E1.5
"""


def _write_3mf(path: Path, *, plate_json: bool = True, plate_png: bool = False) -> Path:
    """A minimal sliced project: gcode always, the plate JSON and PNG on request."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("Metadata/plate_1.gcode", GCODE)
        if plate_json:
            archive.writestr("Metadata/plate_1.json", json.dumps(PLATE_JSON))
        if plate_png:
            archive.writestr("Metadata/plate_1.png", b"\x89PNG\r\n\x1a\n")
    return path


def test_read_plate_json_returns_the_summary(tmp_path: Path):
    data = read_plate_json(_write_3mf(tmp_path / "slice.gcode.3mf"))
    assert data is not None
    assert data["filament_colors"] == ["#00AE42", "#FF0000"]
    assert len(data["bbox_objects"]) == 2
    assert data["bed_type"] == "textured_plate"


def test_read_plate_json_is_none_when_absent(tmp_path: Path):
    project = _write_3mf(tmp_path / "bare.gcode.3mf", plate_json=False)
    assert read_plate_json(project) is None


def test_draw_plate_writes_a_512_png(tmp_path: Path):
    project = _write_3mf(tmp_path / "slice.gcode.3mf")
    dest = draw_plate(project, tmp_path / "out" / "plate.png", build_volume_mm=(256.0, 256.0, 256.0))
    assert dest is not None
    with Image.open(dest) as image:
        assert image.format == "PNG"
        assert image.size == (512, 512)
        assert image.mode == "RGB"


def test_draw_plate_returns_none_and_writes_nothing_without_plate_json(tmp_path: Path):
    project = _write_3mf(tmp_path / "bare.gcode.3mf", plate_json=False)
    dest = tmp_path / "out" / "plate.png"
    assert draw_plate(project, dest) is None
    assert not dest.exists()


def test_draw_plate_defaults_the_bed_to_the_active_printer(tmp_path: Path):
    project = _write_3mf(tmp_path / "slice.gcode.3mf")
    dest = draw_plate(project, tmp_path / "plate.png")
    assert dest is not None and dest.is_file()


def test_fallback_fires_only_when_the_slicer_wrote_no_png(tmp_path: Path):
    """The pipeline's exact branch: no plate PNG -> extract returns None -> draw one."""
    project = _write_3mf(tmp_path / "slice.gcode.3mf")
    dest = tmp_path / "slice" / "nameplate_plate.png"
    assert extract_plate_png(project, dest) is None
    drawn = draw_plate(project, dest, plate=1, build_volume_mm=(256.0, 256.0, 256.0))
    assert drawn == dest
    with Image.open(dest) as image:
        assert image.size == (512, 512)


def test_extract_is_unchanged_when_the_slicer_did_write_a_png(tmp_path: Path):
    """A GUI slice keeps its own thumbnail; the fallback must not override it."""
    project = _write_3mf(tmp_path / "slice.gcode.3mf", plate_png=True)
    dest = extract_plate_png(project, tmp_path / "slice" / "plate.png")
    assert dest is not None and dest.read_bytes().startswith(b"\x89PNG")


def test_draw_plate_previews_the_bambu_fixture_too(tmp_path: Path):
    # The existing fixture carries a plate_1.json, so the same code previews it.
    dest = draw_plate(GOLDEN, tmp_path / "bambu.png")
    assert dest is not None
    with Image.open(dest) as image:
        assert image.size == (512, 512)
