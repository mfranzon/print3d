from pathlib import Path

from text3d.project import inspect_project

GOLDEN = Path(__file__).parent / "fixtures" / "cube_20mm.gcode.3mf"


def test_golden_cube_project():
    info = inspect_project(GOLDEN)
    assert info["sliced"]
    plate = info["plates"][0]
    assert plate["index"] == 1
    assert plate["md5_ok"] is True
    assert plate["layer_count"] == 100
    assert plate["estimated_time"] == "13m 52s"
    assert plate["filament_g"] == 3.63
    assert plate["png_path"] == "Metadata/plate_1.png"
    assert info["slice_info_parsed"]["metadata"]["weight"] == "3.63"
