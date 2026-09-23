"""Nameplate: a rounded plate with raised lettering.

Reference model script. 1 Blender unit == 1 mm; `params` carries the brief.
Raised text prints without support; engraved text needs none either but loses
legibility below about 0.6 mm of relief.
"""

import bpy

text = str(params.get("text") or "TEXT").upper()
width, depth, height = params.get("size_mm") or (40.0, 16.0, 3.0)
plate_h = min(2.0, height * 0.66)
relief = max(0.6, height - plate_h)
bevel = min(1.5, depth * 0.08)

bpy.ops.mesh.primitive_cube_add(size=1)
plate = bpy.context.active_object
plate.name = "plate"
plate.scale = (width, depth, plate_h)
bpy.ops.object.transform_apply(scale=True)
plate.location.z = plate_h / 2.0
bpy.ops.object.transform_apply(location=True)

# Break the sharp top edge so the first perimeter is not a knife edge.
if bevel > 0.05:
    modifier = plate.modifiers.new("bevel", "BEVEL")
    modifier.width = bevel
    modifier.segments = 3
    modifier.limit_method = "ANGLE"

bpy.ops.object.text_add()
letters = bpy.context.active_object
letters.data.body = text
letters.data.align_x = "CENTER"
letters.data.align_y = "CENTER"
# extrude grows both ways, so size the solid to span from the sink depth to the target top.
embed = 0.2
letters.data.extrude = (relief + embed) / 2.0
bpy.ops.object.convert(target="MESH")

# Scale the lettering into the plate's safe area, then sink it in to guarantee overlap.
corners = [letters.matrix_world @ v.co for v in letters.data.vertices]
span_x = max(c.x for c in corners) - min(c.x for c in corners)
span_y = max(c.y for c in corners) - min(c.y for c in corners)
fit = min(width * 0.82 / max(span_x, 1e-6), depth * 0.55 / max(span_y, 1e-6))
letters.scale = (fit, fit, 1.0)
bpy.ops.object.transform_apply(scale=True)
letters.location = (0.0, 0.0, plate_h - embed + (relief + embed) / 2.0)

union = plate.modifiers.new("letters", "BOOLEAN")
union.operation = "UNION"
union.object = letters
union.solver = "EXACT"

bpy.context.view_layer.objects.active = plate
bpy.ops.object.modifier_apply(modifier="bevel")
bpy.ops.object.modifier_apply(modifier="letters")
bpy.data.objects.remove(letters, do_unlink=True)
