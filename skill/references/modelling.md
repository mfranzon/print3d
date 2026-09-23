# Modelling in Blender for the X2D

Recipes and gotchas for writing `jobs/<slug>/model.py`. The contract itself is
in `SKILL.md`; this is what to reach for once you are writing geometry.

## The move each check asks for

| note | what it means | the fix in `model.py` |
| --- | --- | --- |
| `overhang` | An underside shallower than 45°, with supports off. It will droop. | Chamfer the underside to 45°, or rotate the part so the overhang faces up. Rotating is usually cheaper than redesigning. |
| `adhesion` | Too little bed contact. | Widen the base, add a foot, or lay the part down. |
| `slender` | Tall and narrow; it gets knocked off the plate. | Lay it down, or give it a base. |
| `thin` | Below the practical floor of a 0.4 mm nozzle. | Thicken to ≥ 1.2 mm — three passes. |
| `layer_jump` | Material starting over air, corroborated by the mesh. | Look at the z it names, then chamfer that ledge. |

Choosing orientation first is the single highest-leverage decision: the flattest
large face goes on the bed, and most `overhang`/`adhesion`/`slender` notes
disappear at once.

## Gotchas that cost an iteration

- **`transform_apply` before you measure.** `obj.scale = (...)` does not move a
  vertex. Read `obj.data.vertices` only after
  `bpy.ops.object.transform_apply(scale=True)`, or you measure the unscaled
  mesh.
- **Text `extrude` grows both ways.** `data.extrude = t` yields a solid `2t`
  thick, centred on the origin. To span `z0..z1`, set
  `extrude = (z1 - z0) / 2` and `location.z = (z0 + z1) / 2`.
- **Overlap solids before a boolean.** Coplanar faces make the solver produce
  non-manifold junk. Sink the operand 0.2 mm in.
- **Use `solver = "EXACT"`.** The fast solver is for viewport preview.
- **A bevel with `limit_method = "ANGLE"` bevels every edge**, the bottom one
  included. On a flat part that trades bed contact for a chamfer. Use a vertex
  group when you only want the top.
- **Apply modifiers in the order you created them.** `modifier_apply` takes the
  name, and a boolean applied before a bevel gives a different result.
- **Remove the boolean operand** with `bpy.data.objects.remove(op,
  do_unlink=True)`, or it is exported as part of the model.

## Recipes

```python
# Box, sized in mm and sitting on the bed.
bpy.ops.mesh.primitive_cube_add(size=1)
box = bpy.context.active_object
box.scale = (w, d, h); bpy.ops.object.transform_apply(scale=True)
box.location.z = h / 2; bpy.ops.object.transform_apply(location=True)

# Cut B out of A.
m = a.modifiers.new("cut", "BOOLEAN")
m.operation, m.object, m.solver = "DIFFERENCE", b, "EXACT"
bpy.context.view_layer.objects.active = a
bpy.ops.object.modifier_apply(modifier="cut")
bpy.data.objects.remove(b, do_unlink=True)

# Rounded edges.
m = obj.modifiers.new("bevel", "BEVEL")
m.width, m.segments, m.limit_method = 1.0, 3, "ANGLE"

# Text as a solid — see the extrude gotcha above.
bpy.ops.object.text_add()
t = bpy.context.active_object
t.data.body, t.data.align_x, t.data.align_y = "HELLO", "CENTER", "CENTER"
t.data.extrude = thickness / 2
bpy.ops.object.convert(target="MESH")

# Revolve a profile.
m = profile.modifiers.new("screw", "SCREW")
m.angle, m.steps, m.axis = math.radians(360), 64, "Z"

# Rotate the whole part (the usual overhang fix).
obj.rotation_euler = (math.pi, 0, 0)
bpy.ops.object.transform_apply(rotation=True)
```

## Working from a reference image

A photo carries no scale, so pick one real dimension, say it out loud, and drive
everything else off it in `params["size_mm"]`. Build parametrically: name the
driving numbers at the top of the script so the next iteration is an edit to one
constant rather than a rewrite.

## Checking without a full run

`print3d check <mesh.stl> [--project <job>.gcode.3mf]` reports the same
printability metrics and advice for a mesh you already have.
