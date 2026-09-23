"""Headless Blender is the modelling step.

A model script is plain bpy that builds geometry and nothing else: this module
supplies the empty scene, the export, and the bed placement around it.

Contract for a model script:
  - 1 Blender unit == 1 mm.
  - `params` is in scope as a dict (also importable via `bpy.types.Scene.text3d_params`).
  - Leave the finished geometry as the mesh objects in the scene. Helpers,
    empties and cameras are ignored.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

BLENDER_ENV = "BLENDER_BIN"
DEFAULT_TIMEOUT = 600


class BlenderError(RuntimeError):
    """Raised when Blender is missing or a model script fails."""


def find_blender(search_path: str | None = None) -> Path:
    candidates: list[str] = []
    env = os.environ.get(BLENDER_ENV, "").strip()
    if env:
        candidates.append(env)
    which = shutil.which("blender", path=search_path)
    if which:
        candidates.append(which)
    candidates.extend(
        [
            "/Applications/Blender.app/Contents/MacOS/Blender",
            "/opt/homebrew/bin/blender",
            "/usr/local/bin/blender",
            "/usr/bin/blender",
        ]
    )
    for candidate in candidates:
        path = Path(candidate).expanduser()
        if path.is_file():
            return path.resolve()
    raise BlenderError(
        f"Blender not found. Install Blender or set {BLENDER_ENV} to the executable "
        "(on macOS: /Applications/Blender.app/Contents/MacOS/Blender)."
    )


# Runs inside Blender's own Python, which cannot import text3d.
_HARNESS = r'''
import bpy, json, sys, runpy, traceback

argv = sys.argv[sys.argv.index("--") + 1:]
script_path, out_path, params_json, result_path = argv[:4]
params = json.loads(params_json)

bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.types.Scene.text3d_params = params

try:
    runpy.run_path(script_path, init_globals={"params": params}, run_name="__main__")
except Exception:
    traceback.print_exc()
    sys.exit(3)

meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
if not meshes:
    print("text3d: the model script left no mesh objects in the scene")
    sys.exit(4)

# Bake modifiers so what we measure is what gets sliced.
depsgraph = bpy.context.evaluated_depsgraph_get()
for obj in meshes:
    obj.data = bpy.data.meshes.new_from_object(obj.evaluated_get(depsgraph))
    obj.modifiers.clear()

for obj in bpy.context.scene.objects:
    obj.select_set(obj.type == "MESH")
bpy.context.view_layer.objects.active = meshes[0]
if len(meshes) > 1:
    bpy.ops.object.join()
obj = bpy.context.view_layer.objects.active
bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)

# Booleans and text conversion leave seams and doubles; the slicer needs a closed solid.
import bmesh
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

corners = [obj.matrix_world @ v.co for v in obj.data.vertices]
lo = [min(c[i] for c in corners) for i in range(3)]
hi = [max(c[i] for c in corners) for i in range(3)]

# Sit it on the bed and centre it, so DFM and the slicer agree on the origin.
offset = (-(lo[0] + hi[0]) / 2.0, -(lo[1] + hi[1]) / 2.0, -lo[2])
for vertex in obj.data.vertices:
    vertex.co[0] += offset[0]
    vertex.co[1] += offset[1]
    vertex.co[2] += offset[2]
obj.data.update()

kwargs = dict(filepath=out_path, global_scale=1.0, use_scene_unit=False,
              export_selected_objects=True, apply_modifiers=True)
if hasattr(bpy.ops.wm, "stl_export"):
    bpy.ops.wm.stl_export(**kwargs)
else:
    bpy.ops.export_mesh.stl(filepath=out_path, global_scale=1.0,
                            use_scene_unit=False, use_selection=True)

with open(result_path, "w") as handle:
    json.dump({
        "objects_joined": len(meshes),
        "vertices": len(obj.data.vertices),
        "polygons": len(obj.data.polygons),
        "size_mm": [hi[i] - lo[i] for i in range(3)],
        "non_manifold_edges": open_edges,
        "recentre_offset_mm": list(offset),
    }, handle)
print("text3d: exported", out_path)
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

    with tempfile.TemporaryDirectory() as tmp:
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
as mesh objects; text3d exports, centres and drops it onto the bed.
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
