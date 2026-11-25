"""A colour picture of the part as assembled, for the hand-off sheet.

Blender's Workbench engine, flat studio light, transparent background: the
point is to show shape and colour to someone who will not open a slicer.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from print3d.blender import BlenderError, _tail, blender_arg, find_blender

# Runs inside Blender's own Python, which cannot import print3d.
_RENDER = r'''
import bpy, json, math, sys
from mathutils import Vector

argv = sys.argv[sys.argv.index("--") + 1:]
items, out_png, width, height, explode = json.loads(argv[0]), argv[1], int(argv[2]), int(argv[3]), float(argv[4])

bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene
objects = []
for item in items:
    bpy.ops.wm.stl_import(filepath=item["stl"])
    obj = bpy.context.selected_objects[0]
    hex_ = item["hex"].lstrip("#")
    rgb = [int(hex_[i:i + 2], 16) / 255.0 for i in (0, 2, 4)]
    material = bpy.data.materials.new(item.get("name", "part"))
    material.diffuse_color = (*[c ** 2.2 for c in rgb], 1.0)
    obj.data.materials.append(material)
    obj["group"] = item.get("group") or obj.name
    objects.append(obj)


def box(objs):
    corners = [o.matrix_world @ v.co for o in objs for v in o.data.vertices]
    return (Vector([min(c[i] for c in corners) for i in range(3)]),
            Vector([max(c[i] for c in corners) for i in range(3)]))


if explode:
    # Each fused group moves away from the middle along the line through its own centre.
    lo, hi = box(objects)
    middle = (lo + hi) / 2
    groups = {}
    for obj in objects:
        groups.setdefault(obj["group"], []).append(obj)
    for members in groups.values():
        glo, ghi = box(members)
        away = ((glo + ghi) / 2 - middle) * explode
        for obj in members:
            obj.location += away
    bpy.context.view_layer.update()

lo, hi = box(objects)
centre, span = (lo + hi) / 2, (hi - lo).length

camera = bpy.data.objects.new("camera", bpy.data.cameras.new("camera"))
scene.collection.objects.link(camera)
camera.data.type = "ORTHO"
camera.data.ortho_scale = span * 1.15
direction = Vector((1.0, -1.2, 0.9)).normalized()
camera.location = centre + direction * span * 3
camera.rotation_euler = (-direction).to_track_quat("-Z", "Y").to_euler()
camera.data.clip_end = span * 10
scene.camera = camera

scene.render.engine = "BLENDER_WORKBENCH"
scene.view_settings.view_transform = "Standard"
shading = scene.display.shading
shading.light = "STUDIO"
shading.color_type = "MATERIAL"
shading.show_cavity = True
shading.show_shadows = True
shading.show_object_outline = True
scene.render.film_transparent = True
scene.render.resolution_x, scene.render.resolution_y = width, height
scene.render.filepath = out_png
bpy.ops.render.render(write_still=True)
'''


def render_parts(
    items: list[dict[str, Any]],
    out_png: Path,
    *,
    explode: float = 0.0,
    size: tuple[int, int] = (1200, 900),
    timeout: int = 300,
) -> Path:
    """Render STLs, each {"stl", "hex", "group"}, in one isometric view.

    `explode` pulls the groups apart, as a multiple of each one's distance from
    the middle of the assembly.
    """
    out_png = out_png.expanduser().resolve()
    out_png.parent.mkdir(parents=True, exist_ok=True)
    binary = find_blender()
    # Beside the output, not in /tmp: a flatpak Blender sees the home folder but not the host's /tmp.
    with tempfile.TemporaryDirectory(prefix=".blender-", dir=out_png.parent) as tmp:
        script = Path(tmp) / "render.py"
        script.write_text(_RENDER, encoding="utf-8")
        command = [
            str(binary), "--background", "--factory-startup", "--python-exit-code", "5",
            "--python", blender_arg(binary, script), "--",
            json.dumps([
                {**item, "stl": blender_arg(binary, Path(item["stl"]).resolve())}
                for item in items
            ]),
            blender_arg(binary, out_png), str(size[0]), str(size[1]), str(explode),
        ]
        try:
            done = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
        except subprocess.TimeoutExpired as exc:
            raise BlenderError(f"Blender timed out rendering after {timeout}s") from exc
    if done.returncode != 0 or not out_png.is_file():
        raise BlenderError(f"Render failed (exit {done.returncode}).\n{_tail(done.stdout)}\n{_tail(done.stderr)}".strip())
    return out_png
