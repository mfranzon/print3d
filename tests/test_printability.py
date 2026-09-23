import math
from pathlib import Path

from conftest import write_cube_stl
from text3d.printability import (
    BED_CHAMFER_MM,
    advise,
    analyze_mesh,
    worst_severity,
)


def _write_stl(path: Path, triangles) -> Path:
    lines = ["solid t"]
    for tri in triangles:
        lines.append("  facet normal 0 0 0")
        lines.append("    outer loop")
        lines.extend("      vertex %g %g %g" % v for v in tri)
        lines.append("    endloop")
        lines.append("  endfacet")
    lines.append("endsolid t")
    path.write_text("\n".join(lines), encoding="ascii")
    return path


def test_cube_has_no_overhang_and_full_bed_contact(tmp_path: Path):
    metrics = analyze_mesh(write_cube_stl(tmp_path / "cube.stl", 20.0))
    assert metrics["overhang_fraction"] == 0.0
    assert metrics["bed_contact_area_mm2"] == 400.0
    assert metrics["aspect_ratio"] == 1.0
    assert metrics["worst_overhang_slope_deg"] is None


def test_flat_ceiling_high_above_the_bed_is_an_overhang(tmp_path: Path):
    # A ceiling is only an overhang relative to something that reaches the bed;
    # on its own, its lowest face is what sits on the plate.
    path = _write_stl(
        tmp_path / "ceiling.stl",
        [
            ((0, 0, 0), (10, 0, 0), (0, 10, 0)),      # on the bed
            ((0, 0, 10), (0, 10, 10), (10, 0, 10)),   # flat ceiling, facing down
        ],
    )
    metrics = analyze_mesh(path)
    assert metrics["overhang_fraction"] > 0.4
    assert metrics["worst_overhang_slope_deg"] == 0.0
    assert metrics["highest_overhang_mm"] == 10.0


def test_a_lone_plate_rests_on_the_bed_rather_than_overhanging(tmp_path: Path):
    path = _write_stl(
        tmp_path / "plate.stl",
        [((0, 0, 10), (0, 10, 10), (10, 0, 10))],
    )
    metrics = analyze_mesh(path)
    assert metrics["overhang_fraction"] == 0.0
    assert metrics["bed_contact_area_mm2"] > 0


def test_steep_wall_is_not_an_overhang(tmp_path: Path):
    # A face sloping 60 deg from horizontal clears the 45 deg rule.
    run = 10.0
    rise = run * math.tan(math.radians(60))
    path = _write_stl(
        tmp_path / "steep.stl",
        [((0, 0, 0), (run, 0, rise), (0, 10, 0)), ((run, 0, rise), (run, 10, rise), (0, 10, 0))],
    )
    assert analyze_mesh(path)["overhang_fraction"] == 0.0


def _mesh(**overrides):
    base = {
        "overhang_fraction": 0.0,
        "worst_overhang_slope_deg": None,
        "highest_overhang_mm": 0.0,
        "aspect_ratio": 1.0,
        "bed_contact_area_mm2": 400.0,
        "min_dimension_mm": 20.0,
        "slope_threshold_deg": 45.0,
    }
    base.update(overrides)
    return base


def test_clean_mesh_produces_a_single_note():
    notes = advise(_mesh())
    assert [note.code for note in notes] == ["clean"]
    assert worst_severity(notes) == "note"


def test_big_overhang_is_an_error():
    notes = advise(_mesh(overhang_fraction=0.34, worst_overhang_slope_deg=0.0, highest_overhang_mm=30.0))
    assert worst_severity(notes) == "error"
    assert "rotate" in next(n.fix for n in notes if n.code == "overhang").lower()


def test_overhang_hugging_the_bed_is_only_a_note():
    near = advise(_mesh(overhang_fraction=0.04, worst_overhang_slope_deg=15.0,
                        highest_overhang_mm=BED_CHAMFER_MM / 2))
    assert worst_severity(near) == "note"
    far = advise(_mesh(overhang_fraction=0.04, worst_overhang_slope_deg=15.0,
                       highest_overhang_mm=BED_CHAMFER_MM * 5))
    assert worst_severity(far) == "warn"


def test_slender_and_thin_parts_are_flagged():
    codes = {n.code for n in advise(_mesh(aspect_ratio=9.0, bed_contact_area_mm2=20.0, min_dimension_mm=0.6))}
    assert {"slender", "adhesion", "thin"} <= codes


def test_layer_jump_needs_the_mesh_to_agree():
    """Infill-to-top-shell transitions look identical to mid-air material."""
    slice_ = {"estimated_minutes": 20, "max_layer_growth": 5.0, "max_layer_growth_z": 1.2,
              "printed_layers": 15}
    solid = advise(_mesh(), slice_)
    assert "layer_jump" not in {n.code for n in solid}

    overhanging = advise(
        _mesh(overhang_fraction=0.05, worst_overhang_slope_deg=10.0, highest_overhang_mm=30.0),
        slice_,
    )
    assert "layer_jump" in {n.code for n in overhanging}
