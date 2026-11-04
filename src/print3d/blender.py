"""Headless Blender is the modelling step.

A model script is plain bpy that builds geometry and nothing else: this module
supplies the empty scene, the export, and the bed placement around it.

Contract for a model script:
  - 1 Blender unit == 1 mm.
  - `params` is in scope as a dict (also importable via `bpy.types.Scene.print3d_params`).
  - Leave the finished geometry as the mesh objects in the scene. Helpers,
    empties and cameras are ignored.
  - For more than one printed part, call `part(objects, name, ...)` on each,
    modelled where it sits when assembled; `fit(a, b, kind)` declares how two
    parts mate. Without part() every mesh is joined into one part.
"""

from __future__ import annotations

import glob
import json
import os
import re
import shutil
import subprocess
import tempfile
from itertools import chain
from pathlib import Path
from typing import Any

from print3d.windows import blender_locations

BLENDER_ENV = "BLENDER_BIN"
DEFAULT_TIMEOUT = 600

# macOS and Linux locations from plugin.json; Windows Program Files locations
# are expanded at runtime by blender_locations(). Custom installs can use BLENDER_BIN.
BLENDER_LOCATIONS = (
    "/Applications/Blender.app/Contents/MacOS/Blender",
    "~/Applications/Blender.app/Contents/MacOS/Blender",
    "~/Library/Application Support/Steam/steamapps/common/Blender/Blender.app/Contents/MacOS/Blender",
    "/opt/homebrew/bin/blender",
    "/usr/local/bin/blender",
    "/usr/bin/blender",
    "/var/lib/flatpak/exports/bin/org.blender.Blender",
    "~/.local/share/flatpak/exports/bin/org.blender.Blender",
    "/snap/bin/blender",
    "~/.steam/steam/steamapps/common/Blender/blender",
    "~/.local/share/Steam/steamapps/common/Blender/blender",
    "~/blender-*/blender",
    "/opt/blender*/blender",
)


class BlenderError(RuntimeError):
    """Raised when Blender is missing or a model script fails."""


def find_blender(search_path: str | None = None) -> Path:
    candidates: list[str | Path] = []
    env = os.environ.get(BLENDER_ENV, "").strip()
    if env:
        candidates.append(env)
    which = shutil.which("blender", path=search_path)
    if which:
        candidates.append(which)
    candidates.extend(BLENDER_LOCATIONS)
    for candidate in chain(candidates, blender_locations()):
        path = Path(candidate).expanduser()
        # Hermes expands '*' in declared paths and tries higher versions first.
        # Escape other glob metacharacters, including those in the home directory.
        if "*" in str(path):
            pattern = "*".join(glob.escape(part) for part in str(path).split("*"))
            matches = sorted(
                (Path(match) for match in glob.glob(pattern)),
                key=lambda match: [(1, int(p)) if p.isdigit() else (0, p)
                                   for p in re.split(r"(\d+)", str(match))],
                reverse=True,
            )
        else:
            matches = [path]
        for match in matches:
            if match.is_file():
                return match.resolve()
    raise BlenderError(
        f"Blender not found. Install Blender or set {BLENDER_ENV} to the executable "
        "(on macOS: /Applications/Blender.app/Contents/MacOS/Blender)."
    )


# Runs inside Blender's own Python, which cannot import print3d.
_HARNESS = r'''
import bpy, bmesh, json, math, os, re, sys, runpy, traceback
from mathutils import Euler, Matrix
from mathutils.bvhtree import BVHTree

argv = sys.argv[sys.argv.index("--") + 1:]
script_path, out_path, params_json, result_path = argv[:4]
params = json.loads(params_json)
FIT_KINDS = tuple(params.get("fit") or ("press", "snug", "slide", "free"))
# Pairs further apart than this are not mating, and gaps above it are not a fit.
NEAR_MM = 2.0
CONTACT_MM = 0.01

PARTS = {}
FITS = []


def fail(code, message):
    print("print3d: " + message)
    sys.exit(code)


def part(objects, name, *, color=None, group=None, rotate=(0, 0, 0), plate=None, settings=None):
    """Declare a printed part: one or more mesh objects, modelled where they sit when assembled.

    color: a name or #RRGGBB; each colour gets its own filament.
    group: print fused with the part of that name, as one multi-colour object.
    rotate: degrees about x, y, z that turn the part from assembled into its print pose.
    plate: build plate number, when the parts should not share one.
    settings: Studio process overrides for this part, e.g. {"wall_loops": 4}.
    """
    if isinstance(objects, bpy.types.Object):
        objects = [objects]
    name, group = str(name), str(group or name)
    for label in (name, group):
        if not re.match(r"^[A-Za-z0-9][A-Za-z0-9_-]*$", label):
            raise ValueError(f"part name {label!r}: use letters, digits, - and _ (it names files)")
    if name in PARTS:
        raise ValueError(f"part {name!r} is declared twice")
    if not objects:
        raise ValueError(f"part {name!r} has no objects")
    for obj in objects:
        if obj.type != "MESH":
            raise ValueError(f"{obj.name} is a {obj.type}, not a mesh: convert it before part()")
        if "print3d_part" in obj:
            raise ValueError(f"{obj.name} already belongs to part {obj['print3d_part']!r}")
        obj["print3d_part"] = name
    PARTS[name] = {
        "color": color,
        "group": group,
        "rotate": [float(a) for a in rotate],
        "plate": int(plate) if plate is not None else None,
        "settings": {str(k): str(v) for k, v in (settings or {}).items()},
    }
    return objects[0]


def fit(a, b, kind):
    """Declare that parts a and b mate with a fit of this kind; the run checks the gap."""
    if kind not in FIT_KINDS:
        raise ValueError(f"fit kind {kind!r}: use one of {', '.join(FIT_KINDS)}")
    FITS.append({"a": str(a), "b": str(b), "kind": kind})


bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.types.Scene.print3d_params = params

try:
    runpy.run_path(
        script_path, init_globals={"params": params, "part": part, "fit": fit}, run_name="__main__"
    )
except Exception:
    traceback.print_exc()
    sys.exit(3)

meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
if not meshes:
    fail(4, "the model script left no mesh objects in the scene")

# Bake modifiers so what we measure is what gets sliced.
depsgraph = bpy.context.evaluated_depsgraph_get()
for obj in meshes:
    obj.data = bpy.data.meshes.new_from_object(obj.evaluated_get(depsgraph))
    obj.modifiers.clear()


def select(objects):
    for obj in bpy.context.scene.objects:
        obj.select_set(obj in objects)
    bpy.context.view_layer.objects.active = objects[0]


def join(objects):
    select(objects)
    if len(objects) > 1:
        bpy.ops.object.join()
    obj = bpy.context.view_layer.objects.active
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    return obj


def clean(obj):
    """Booleans and text conversion leave seams and doubles; the slicer needs a closed solid."""
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-5)
    bmesh.ops.dissolve_degenerate(bm, dist=1e-6, edges=bm.edges)
    bmesh.ops.holes_fill(bm, edges=bm.edges)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    bmesh.ops.triangulate(bm, faces=bm.faces)
    open_edges = sum(1 for e in bm.edges if len(e.link_faces) != 2)
    bm.to_mesh(obj.data)
    bm.free()
    obj.data.update()
    return open_edges


def bounds(objects):
    corners = [o.matrix_world @ v.co for o in objects for v in o.data.vertices]
    return ([min(c[i] for c in corners) for i in range(3)], [max(c[i] for c in corners) for i in range(3)])


def place_on_bed(objects):
    """Centre on x/y and sit on z=0, so DFM and the slicer agree on the origin."""
    lo, hi = bounds(objects)
    offset = (-(lo[0] + hi[0]) / 2.0, -(lo[1] + hi[1]) / 2.0, -lo[2])
    for obj in objects:
        obj.data.transform(Matrix.Translation(offset))
        obj.data.update()
    return offset


def export(objects, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    select(objects)
    if hasattr(bpy.ops.wm, "stl_export"):
        bpy.ops.wm.stl_export(filepath=path, global_scale=1.0, use_scene_unit=False,
                              export_selected_objects=True, apply_modifiers=True)
    else:
        bpy.ops.export_mesh.stl(filepath=path, global_scale=1.0,
                                use_scene_unit=False, use_selection=True)


def duplicate(obj, name):
    copy = obj.copy()
    copy.data = obj.data.copy()
    copy.name = name
    copy.pop("print3d_part", None)
    bpy.context.scene.collection.objects.link(copy)
    return copy


def size(objects):
    lo, hi = bounds(objects)
    return [hi[i] - lo[i] for i in range(3)]


def gap(a, b):
    """Closest approach between two parts, from each one's vertices to the other's surface.

    Vertices inside the other part are interference; the rest give the smallest
    clearance that is not a resting contact.
    """
    trees = {}
    for obj in (a, b):
        trees[obj.name] = BVHTree.FromPolygons(
            [v.co.copy() for v in obj.data.vertices], [p.vertices[:] for p in obj.data.polygons]
        )
    contact, clearance, interference = False, None, 0.0
    for src, dst in ((a, b), (b, a)):
        tree = trees[dst.name]
        for v in src.data.vertices:
            loc, normal, _, dist = tree.find_nearest(v.co, NEAR_MM)
            if loc is None:
                continue
            if (v.co - loc).dot(normal) < -CONTACT_MM:
                interference = max(interference, dist)
            elif dist <= CONTACT_MM:
                contact = True
            elif clearance is None or dist < clearance:
                clearance = dist
    return {
        "contact": contact,
        "clearance_mm": round(clearance, 3) if clearance is not None else None,
        "interference_mm": round(interference, 3),
    }


def exposed_area(part_obj, others):
    """Area of part_obj's faces that are neither inside nor lying on any of the others."""
    trees = [BVHTree.FromPolygons([v.co.copy() for v in o.data.vertices], [p.vertices[:] for p in o.data.polygons])
             for o in others]
    area = 0.0
    for poly in part_obj.data.polygons:
        hidden = False
        for tree in trees:
            loc, normal, _, dist = tree.find_nearest(poly.center, NEAR_MM)
            if loc is not None and (dist <= CONTACT_MM or (poly.center - loc).dot(normal) < 0):
                hidden = True
                break
        if not hidden:
            area += poly.area
    return area


def single():
    obj = join(meshes)
    open_edges = clean(obj)
    offset = place_on_bed([obj])
    lo, hi = bounds([obj])
    export([obj], out_path)
    return {
        "objects_joined": len(meshes),
        "vertices": len(obj.data.vertices),
        "polygons": len(obj.data.polygons),
        "size_mm": [hi[i] - lo[i] for i in range(3)],
        "non_manifold_edges": open_edges,
        "recentre_offset_mm": list(offset),
    }


def assembly():
    loose = sorted(o.name for o in meshes if "print3d_part" not in o)
    if loose:
        fail(6, "mesh objects not given to part(): " + ", ".join(loose)
             + ". Pass them to part(), or delete them (bpy.data.objects.remove) if they are helpers.")
    for spec in FITS:
        for key in ("a", "b"):
            if spec[key] not in PARTS:
                fail(7, f"fit() names part {spec[key]!r}, but the parts are: {', '.join(PARTS)}")
    groups = {}
    for name, info in PARTS.items():
        groups.setdefault(info["group"], []).append(name)
    for group, members in groups.items():
        rotations = {tuple(PARTS[m]["rotate"]) for m in members}
        plates = {PARTS[m]["plate"] for m in members} - {None}
        if len(rotations) > 1 or len(plates) > 1:
            fail(8, f"parts {', '.join(members)} print fused as {group!r}, so they need the same rotate and plate")

    out_dir = os.path.dirname(out_path)
    solids, report = {}, []
    for name, info in PARTS.items():
        obj = join([o for o in meshes if o.get("print3d_part") == name])
        obj.name = name
        obj.pop("print3d_part", None)
        open_edges = clean(obj)
        solids[name] = obj
        info.update(name=name, non_manifold_edges=open_edges, vertices=len(obj.data.vertices),
                    assembly_stl=os.path.join(out_dir, "assembly", name + ".stl"))
        export([obj], info["assembly_stl"])
        report.append(info)

    # As modelled, every part where it sits when assembled: for likeness, renders and fits.
    export(list(solids.values()), out_path)

    names = list(PARTS)
    fits = []
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            if PARTS[a]["group"] == PARTS[b]["group"]:
                continue
            (alo, ahi), (blo, bhi) = bounds([solids[a]]), bounds([solids[b]])
            if any(alo[k] > bhi[k] + NEAR_MM or blo[k] > ahi[k] + NEAR_MM for k in range(3)):
                continue
            entry = {"a": a, "b": b, **gap(solids[a], solids[b])}
            if entry["contact"] or entry["clearance_mm"] is not None or entry["interference_mm"]:
                fits.append(entry)

    # A colour part sealed inside its partner prints as nothing: usually a
    # pocket cut by a flush cutter, which leaves a skin over it.
    buried = []
    for group, members in groups.items():
        for m in members:
            others = [solids[o] for o in members if o != m]
            if others and exposed_area(solids[m], others) < 1e-3:
                buried.append(m)

    objects = []
    for group, members in groups.items():
        rotation = Euler([math.radians(d) for d in PARTS[members[0]]["rotate"]], "XYZ").to_matrix().to_4x4()
        posed = []
        for m in members:
            copy = duplicate(solids[m], m + "_print")
            copy.data.transform(rotation)
            copy.data.update()
            posed.append(copy)
        place_on_bed(posed)
        for m, copy in zip(members, posed):
            PARTS[m]["print_stl"] = os.path.join(out_dir, "parts", m + ".stl")
            PARTS[m]["print_size_mm"] = size([copy])
            export([copy], PARTS[m]["print_stl"])
        # Checks need the solid that actually prints: the members fused.
        check = posed[0]
        fused = "single"
        if len(posed) > 1:
            check = duplicate(posed[0], group + "_check")
            for other in posed[1:]:
                mod = check.modifiers.new("fuse", "BOOLEAN")
                mod.operation, mod.object, mod.solver = "UNION", other, "EXACT"
                select([check])
                bpy.ops.object.modifier_apply(modifier="fuse")
            fused = "union"
            if clean(check) != 0:
                # Inlays share faces with their pocket, which the exact solver
                # rarely closes. A voxel remesh of the members, one layer fine,
                # is closed and close enough to check overhangs and size.
                bpy.data.objects.remove(check, do_unlink=True)
                check = join([duplicate(c, group + "_check") for c in posed])
                mod = check.modifiers.new("weld", "REMESH")
                mod.mode, mod.voxel_size = "VOXEL", 0.2
                bpy.ops.object.modifier_apply(modifier="weld")
                fused = "remeshed"
        stl = os.path.join(out_dir, "objects", group + ".stl")
        export([check], stl)
        plate = next((PARTS[m]["plate"] for m in members if PARTS[m]["plate"]), None)
        objects.append({"name": group, "parts": members, "plate": plate, "stl": stl,
                        "size_mm": size([check]), "fused": fused})

    return {
        "objects_joined": len(meshes),
        "size_mm": size(list(solids.values())),
        "non_manifold_edges": sum(p["non_manifold_edges"] for p in report),
        "parts": report,
        "objects": objects,
        "fits": fits,
        "declared_fits": FITS,
        "buried": buried,
    }


result = assembly() if PARTS else single()
with open(result_path, "w") as handle:
    json.dump(result, handle)
print("print3d: exported", out_path)
'''


def run_model_script(
    script: Path,
    out_mesh: Path,
    *,
    params: dict[str, Any] | None = None,
    timeout: int = DEFAULT_TIMEOUT,
    blender: Path | None = None,
) -> dict[str, Any]:
    """Run a bpy model script headless and export its geometry as an STL."""
    script = script.expanduser().resolve()
    if not script.is_file():
        raise BlenderError(f"Model script does not exist: {script}")
    out_mesh = out_mesh.expanduser().resolve()
    out_mesh.parent.mkdir(parents=True, exist_ok=True)
    binary = blender or find_blender()

    # Beside the output, not in /tmp: a flatpak Blender sees the home folder but not the host's /tmp.
    with tempfile.TemporaryDirectory(prefix=".blender-", dir=out_mesh.parent) as tmp:
        harness = Path(tmp) / "harness.py"
        harness.write_text(_HARNESS, encoding="utf-8")
        result_path = Path(tmp) / "result.json"
        command = [
            str(binary),
            "--background",
            "--factory-startup",
            "--python-exit-code",
            "5",
            "--python",
            str(harness),
            "--",
            str(script),
            str(out_mesh),
            json.dumps(params or {}),
            str(result_path),
        ]
        try:
            completed = subprocess.run(
                command, capture_output=True, text=True, timeout=timeout, check=False
            )
        except subprocess.TimeoutExpired as exc:
            raise BlenderError(f"Blender timed out after {timeout}s on {script.name}") from exc

        if completed.returncode != 0 or not out_mesh.is_file():
            raise BlenderError(
                f"{script.name} failed in Blender (exit {completed.returncode}).\n"
                f"{_tail(completed.stdout)}\n{_tail(completed.stderr)}".strip()
            )
        detail = json.loads(result_path.read_text()) if result_path.is_file() else {}

    return {
        "blender": str(binary),
        "script": str(script),
        "mesh": str(out_mesh),
        "params": params or {},
        "mesh_bytes": out_mesh.stat().st_size,
        **detail,
    }


def _tail(text: str, lines: int = 25) -> str:
    return "\n".join(text.strip().splitlines()[-lines:])


SCAFFOLD = '''"""Blender model for: {prompt}

1 Blender unit == 1 mm. `params` holds the brief. Leave the finished geometry
as mesh objects; print3d exports, centres and drops it onto the bed.
"""

import bpy

WIDTH, DEPTH, HEIGHT = {size}

# TODO: replace this block with the real part.
bpy.ops.mesh.primitive_cube_add(size=1)
part = bpy.context.active_object
part.scale = (WIDTH, DEPTH, HEIGHT)
part.name = "{slug}"
'''


def scaffold_script(path: Path, *, prompt: str, slug: str, size: tuple[float, float, float]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        SCAFFOLD.format(prompt=prompt, slug=slug, size=tuple(round(v, 3) for v in size)),
        encoding="utf-8",
    )
    return path
