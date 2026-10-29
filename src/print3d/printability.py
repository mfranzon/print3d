"""What the slice says about the model, and what to change in Blender.

Two signals feed the optimise loop:

  - the mesh, where overhang and footprint problems are visible before slicing
    and where the fix actually lives;
  - the sliced gcode, which is the ground truth for time, material and how the
    part is built up layer by layer.

The pinned X2D process has `enable_support = 0`, so the slicer will not warn
about an overhang - it will just print it badly. That is why the mesh analysis
carries most of the weight here.
"""

from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

from print3d.dfm import DfmReport, read_stl
from print3d.project import read_plate_gcode, slice_info_weight_g

# Slope measured from the horizontal plane: 0 deg is a flat ceiling, 90 deg a
# vertical wall. The classic 45 deg rule is the default.
OVERHANG_SLOPE_DEG = 45.0
BED_TOL_MM = 0.05
# An overhang confined to the first millimetre is a base chamfer, not a droop.
BED_CHAMFER_MM = 1.0

OVERHANG_FRACTION_WARN = 0.02
OVERHANG_FRACTION_ERROR = 0.10
ASPECT_WARN = 4.0
FOOTPRINT_WARN_MM2 = 100.0
THIN_PART_MM = 1.2
LONG_PRINT_MIN = 240
LAYER_GROWTH_WARN = 2.0

MOVE_RE = re.compile(r"^G[0-3]\b(.*)$")
AXIS_RE = re.compile(r"([XYZEF])(-?\d*\.?\d+)")
TIME_RE = re.compile(r"total estimated time:\s*(.+?)\s*$", re.IGNORECASE)
ORCA_TIME_RE = re.compile(
    r";?\s*estimated printing time \((?:normal|quiet) mode\)\s*=\s*(.+?)\s*$", re.IGNORECASE
)
WEIGHT_RE = re.compile(r"total filament weight \[g\]\s*[:=]\s*([0-9.]+)", re.IGNORECASE)
LAYERS_RE = re.compile(r"total layer number:\s*(\d+)", re.IGNORECASE)


@dataclass(frozen=True)
class Advice:
    code: str
    severity: str  # "error" | "warn" | "note"
    message: str
    fix: str
    part: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def dfm_advice(report: DfmReport) -> list[Advice]:
    """Include structural findings in the same verdict as overhang and slice advice."""
    notes = [
        Advice(
            "dfm", "error", message,
            "Repair the mesh, resize it to fit the bed, or reduce its complexity as indicated.",
        )
        for message in report.errors
    ]
    notes.extend(
        Advice(
            "dfm", "warn", message,
            "Review the mesh complexity and bed placement before slicing.",
        )
        for message in report.warnings
    )
    return notes


def _triangle_area_normal(tri) -> tuple[float, tuple[float, float, float]]:
    (ax, ay, az), (bx, by, bz), (cx, cy, cz) = tri
    ux, uy, uz = bx - ax, by - ay, bz - az
    vx, vy, vz = cx - ax, cy - ay, cz - az
    nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
    length = math.sqrt(nx * nx + ny * ny + nz * nz)
    if length < 1e-12:
        return 0.0, (0.0, 0.0, 0.0)
    return length / 2.0, (nx / length, ny / length, nz / length)


def analyze_mesh(path: Path, *, slope_deg: float = OVERHANG_SLOPE_DEG) -> dict[str, Any]:
    """Overhang, footprint and slenderness, straight off the triangles."""
    triangles = read_stl(path.expanduser().resolve())
    min_z = min(v[2] for tri in triangles for v in tri)

    total_area = 0.0
    downfacing_area = 0.0
    overhang_area = 0.0
    bed_area = 0.0
    worst_slope = 90.0
    highest_overhang_z = 0.0

    for tri in triangles:
        area, normal = _triangle_area_normal(tri)
        if area <= 0.0:
            continue
        total_area += area
        nz = normal[2]
        if nz >= -1e-6:
            continue
        downfacing_area += area
        on_bed = all(abs(v[2] - min_z) <= BED_TOL_MM for v in tri)
        if on_bed:
            bed_area += area
            continue
        slope = math.degrees(math.acos(min(1.0, -nz)))
        if slope < slope_deg:
            overhang_area += area
            worst_slope = min(worst_slope, slope)
            highest_overhang_z = max(highest_overhang_z, max(v[2] for v in tri) - min_z)

    xs = [v[0] for tri in triangles for v in tri]
    ys = [v[1] for tri in triangles for v in tri]
    zs = [v[2] for tri in triangles for v in tri]
    size = (max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs))
    footprint = max(min(size[0], size[1]), 1e-6)

    return {
        "triangles": len(triangles),
        "size_mm": size,
        "total_area_mm2": total_area,
        "downfacing_area_mm2": downfacing_area,
        "overhang_area_mm2": overhang_area,
        "overhang_fraction": overhang_area / total_area if total_area else 0.0,
        "worst_overhang_slope_deg": worst_slope if overhang_area else None,
        "highest_overhang_mm": highest_overhang_z if overhang_area else 0.0,
        "bed_contact_area_mm2": bed_area,
        "aspect_ratio": size[2] / footprint,
        "min_dimension_mm": min(size),
        "slope_threshold_deg": slope_deg,
    }


def analyze_slice(project: Path, plate: int = 1) -> dict[str, Any]:
    """Per-layer material from the gcode. No feature comments needed."""
    gcode = read_plate_gcode(project, plate)
    # Bambu Studio writes the settings block at the top, ending at
    # CONFIG_BLOCK_END. ElegooSlicer (Orca lineage) writes the same block at
    # the bottom, after the gcode, so the moves and summary lines sit before
    # CONFIG_BLOCK_START. Either way the block holds only comments, so one
    # pass over all lines - skipping the block itself - collects both the
    # summary and the moves without a second scan.
    estimated_time = None
    weight_g = None
    layer_count = None
    relative_e = True
    current_z = 0.0
    last_e = 0.0
    by_z: dict[float, float] = {}
    in_block = False

    for raw in gcode.splitlines():
        stripped = raw.strip()
        if not stripped:
            continue
        first = stripped[0]
        if "CONFIG_BLOCK_START" in stripped:
            in_block = True
            continue
        if "CONFIG_BLOCK_END" in stripped:
            in_block = False
            continue
        if in_block:
            continue
        if first == ";":
            if estimated_time is None:
                match = TIME_RE.search(stripped) or ORCA_TIME_RE.search(stripped)
                if match:
                    estimated_time = match.group(1).rstrip(";").strip()
            if weight_g is None:
                match = WEIGHT_RE.search(stripped)
                if match:
                    weight_g = float(match.group(1))
            if layer_count is None:
                match = LAYERS_RE.search(stripped)
                if match:
                    layer_count = int(match.group(1))
            continue
        if stripped.startswith("M83"):
            relative_e = True
            continue
        if stripped.startswith("M82"):
            relative_e = False
            continue
        if stripped.startswith("G92"):
            last_e = 0.0
            continue
        match = MOVE_RE.match(stripped)
        if not match:
            continue
        axes = dict(AXIS_RE.findall(match.group(1).split(";")[0]))
        if "Z" in axes:
            current_z = round(float(axes["Z"]), 3)
        if "E" in axes:
            value = float(axes["E"])
            delta = value if relative_e else value - last_e
            last_e = value
            # A move with no X or Y is a retract or a prime, not deposited material.
            if delta > 0 and ("X" in axes or "Y" in axes):
                by_z[current_z] = by_z.get(current_z, 0.0) + delta

    layers = [{"z": z, "extruded_mm": amount} for z, amount in sorted(by_z.items())]
    amounts = [layer["extruded_mm"] for layer in layers]
    growth = 0.0
    growth_z = None
    for index in range(1, len(amounts)):
        previous = amounts[index - 1]
        if previous > 0.5 and amounts[index] / previous > growth:
            growth = amounts[index] / previous
            growth_z = layers[index]["z"]

    if weight_g is None:
        # Orca-lineage slicers put the weight in slice_info, not the gcode header.
        weight_g = slice_info_weight_g(project, plate)

    return {
        "plate": plate,
        "estimated_time": estimated_time,
        "estimated_minutes": _minutes(estimated_time),
        "filament_g": weight_g,
        "layer_count": layer_count or len(layers),
        "printed_layers": len(layers),
        "max_z_mm": max((layer["z"] for layer in layers), default=0.0),
        "first_layer_mm": amounts[0] if amounts else 0.0,
        "total_extruded_mm": sum(amounts),
        "max_layer_growth": growth,
        "max_layer_growth_z": growth_z,
        "layers": layers,
    }


def _minutes(estimate: str | None) -> float | None:
    if not estimate:
        return None
    total = 0.0
    for value, unit in re.findall(r"(\d+(?:\.\d+)?)\s*([hms])", estimate):
        total += float(value) * {"h": 60.0, "m": 1.0, "s": 1 / 60.0}[unit]
    return round(total, 1) if total else None


def corroborates_layer_jump(mesh: dict[str, Any]) -> bool:
    """A layer jump only means mid-air material where the mesh already shows a raised overhang."""
    return mesh["overhang_fraction"] >= OVERHANG_FRACTION_WARN and mesh["highest_overhang_mm"] > BED_CHAMFER_MM


def mesh_advice(mesh: dict[str, Any], part: str | None = None) -> list[Advice]:
    """What the triangles of one printed object say, before slicing."""
    notes: list[Advice] = []
    fraction = mesh["overhang_fraction"]
    if fraction >= OVERHANG_FRACTION_ERROR:
        notes.append(
            Advice(
                "overhang",
                "error",
                f"{fraction:.1%} of the surface overhangs below "
                f"{mesh['slope_threshold_deg']:.0f} deg, the worst at "
                f"{mesh['worst_overhang_slope_deg']:.0f} deg and up to "
                f"{mesh['highest_overhang_mm']:.1f} mm above the bed. The pinned process "
                "has supports off, so this will droop.",
                "Chamfer the underside to 45 deg, or rotate the part so the overhang faces up.",
            )
        )
    elif fraction >= OVERHANG_FRACTION_WARN:
        near_bed = mesh["highest_overhang_mm"] <= BED_CHAMFER_MM
        notes.append(
            Advice(
                "overhang",
                "note" if near_bed else "warn",
                f"{fraction:.1%} of the surface is a shallow overhang "
                f"(worst {mesh['worst_overhang_slope_deg']:.0f} deg, up to "
                f"{mesh['highest_overhang_mm']:.1f} mm above the bed)."
                + (" That is a base chamfer; the bed carries it." if near_bed else ""),
                "Leave it as is." if near_bed
                else "A small chamfer or fillet on those faces will clean up the underside.",
            )
        )
    if mesh["aspect_ratio"] > ASPECT_WARN:
        notes.append(
            Advice(
                "slender",
                "warn",
                f"The part is {mesh['aspect_ratio']:.1f}x taller than its narrowest "
                "footprint and can be knocked off the plate.",
                "Widen the base, add a foot, or lay the part down.",
            )
        )
    if mesh["bed_contact_area_mm2"] < FOOTPRINT_WARN_MM2:
        notes.append(
            Advice(
                "adhesion",
                "warn",
                f"Only {mesh['bed_contact_area_mm2']:.0f} mm^2 touches the bed.",
                "Add a flat pad under the part, or turn a brim on in Studio before printing.",
            )
        )
    if mesh["min_dimension_mm"] < THIN_PART_MM:
        notes.append(
            Advice(
                "thin",
                "warn",
                f"Thinnest dimension is {mesh['min_dimension_mm']:.2f} mm, near the "
                "0.4 mm nozzle's practical floor.",
                "Thicken it to at least 1.2 mm so it gets more than a couple of perimeters.",
            )
        )
    return [replace(note, part=part) for note in notes]


def slice_advice(slice_: dict[str, Any], corroborated: bool) -> list[Advice]:
    """What one sliced plate says."""
    notes: list[Advice] = []
    minutes = slice_.get("estimated_minutes")
    if minutes and minutes > LONG_PRINT_MIN:
        notes.append(
            Advice(
                "duration",
                "note",
                f"Estimated {minutes:.0f} min.",
                "Scale the part down or raise layer height if that is longer than you want.",
            )
        )
    # A material jump alone means nothing: the sparse-infill-to-top-shell
    # transition looks identical. Only trust it where the mesh already shows
    # an unsupported region, and the gcode carries no feature comments to
    # tell the two apart.
    growth = slice_.get("max_layer_growth") or 0.0
    if growth > LAYER_GROWTH_WARN and corroborated:
        notes.append(
            Advice(
                "layer_jump",
                "warn",
                f"Material per layer jumps {growth:.1f}x at z="
                f"{slice_.get('max_layer_growth_z')} mm, which is new area starting in mid air.",
                "Check that step in the plate preview; chamfer it if it is an unsupported ledge.",
            )
        )
    if slice_.get("printed_layers", 0) < 3:
        notes.append(
            Advice(
                "too_flat",
                "warn",
                f"Only {slice_.get('printed_layers')} printed layers.",
                "Below about 0.6 mm the part is a sticker; raise its height.",
            )
        )
    return notes


def advise(
    mesh: dict[str, Any], slice_: dict[str, Any] | None = None, extra: list[Advice] = ()
) -> list[Advice]:
    """Actionable notes, phrased as changes to make in the model script."""
    notes = [*extra, *mesh_advice(mesh)]
    if slice_:
        notes.extend(slice_advice(slice_, corroborates_layer_jump(mesh)))
    return with_clean(notes)


def with_clean(notes: list[Advice]) -> list[Advice]:
    if not notes:
        notes = [Advice("clean", "note", "No printability problems found.", "Ready to review in Studio.")]
    return notes


def worst_severity(notes: list[Advice]) -> str:
    for level in ("error", "warn", "note"):
        if any(note.severity == level for note in notes):
            return level
    return "note"
