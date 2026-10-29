"""ElegooSlicer (Orca) sliced projects: settings block at the bottom of the gcode."""

import zipfile
from pathlib import Path

from print3d.printability import analyze_slice

SLICE_INFO = """<?xml version="1.0" encoding="UTF-8"?>
<config>
  <plate>
    <metadata key="index" value="1"/>
    <metadata key="weight" value="4.06"/>
    <filament id="1" type="PLA" used_g="4.06" used_m="1.35"/>
  </plate>
</config>
"""

# Orca lineage writes the gcode first, the summary lines just before the
# settings block, and the settings block last.
GCODE = """;===== CC_START_GCODE ================
G90
M83
; total layer number: 100
G1 Z0.3 F900
G1 X0 Y0 E10.156
G1 Z0.5
G1 X10 Y0 E20.156
G1 Z0.7
G1 X20 Y0 E30.156
; estimated printing time (normal mode) = 10m 5s
; CONFIG_BLOCK_START
; z_hop = 0.4
; CONFIG_BLOCK_END
"""


def _orca_project(tmp_path: Path) -> Path:
    project = tmp_path / "cube.gcode.3mf"
    with zipfile.ZipFile(project, "w") as archive:
        archive.writestr("Metadata/plate_1.gcode", GCODE)
        archive.writestr("Metadata/slice_info.config", SLICE_INFO)
        archive.writestr("3D/3dmodel.model", "<model/>")
    return project


def test_analyze_slice_reads_orca_layout(tmp_path: Path):
    metrics = analyze_slice(_orca_project(tmp_path))
    assert metrics["layer_count"] == 100
    assert metrics["printed_layers"] == 3
    assert [layer["z"] for layer in metrics["layers"]] == [0.3, 0.5, 0.7]
    assert metrics["estimated_time"] == "10m 5s"
    assert metrics["estimated_minutes"] == 10.1
    # The weight is not in the gcode; it comes from slice_info.
    assert metrics["filament_g"] == 4.06


def test_bambu_layout_still_reads_from_the_top(tmp_path: Path):
    project = tmp_path / "cube.gcode.3mf"
    with zipfile.ZipFile(project, "w") as archive:
        archive.writestr(
            "Metadata/plate_1.gcode",
            "; total filament weight [g]: 3.63\n; CONFIG_BLOCK_START\n; z_hop = 0.4\n; CONFIG_BLOCK_END\n"
            "; total layer number: 100\nG1 Z0.2\nG1 X5 Y0 E1.0\n",
        )
    metrics = analyze_slice(project)
    assert metrics["filament_g"] == 3.63
    assert metrics["printed_layers"] == 1
    # The layer count line sits after the settings block; the single pass
    # still reads it, so it stands in for nothing.
    assert metrics["layer_count"] == 100
