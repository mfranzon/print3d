---
name: print3d
description: Model a part in Blender, slice it for the Bambu X2D, and iterate until it prints clean.
argument-hint: "[what to make, and/or a reference image]"
disable-model-invocation: true
---

# /print3d — idea to a printable X2D part

Run everything from the repo root (the parent of this skill folder). `text3d` below means
`.venv/bin/python -m text3d`.

```
idea / image / text  →  model.py (Blender)  →  mesh  →  DFM + printability
                             ↑                                │
                             └────────  advice: what to fix ───┘
                                              │
                                        slice → .3mf → user prints from Studio
```

Nothing here reaches the printer. The last step is a sentence handing the user a
`.3mf`. Printing is theirs to do in Studio, with the plate in front of them.

## 1. Read the brief

Size, text, colour, what the part has to do. From a reference image, describe
what you see and state the dimensions you are assuming — a photo carries no
scale. Ask at most one question, and only when a missing number makes the part
unprintable.

## 2. Scaffold

```bash
text3d make "<prompt>"
```

With no model yet this writes `jobs/<slug>/model.py` and stops, naming the path.
Take the slug from that path.

## 3. Write the model

Replace the scaffold's body with the real part. The contract:

- Plain bpy. **1 Blender unit == 1 mm.**
- `params` is in scope: `prompt`, `slug`, `text`, `intent`, `size_mm`, `color`,
  `material`.
- Leave the geometry as mesh objects. The harness joins them, removes doubles,
  fills holes, recalculates normals, triangulates, centres the part, drops it on
  the bed and exports.
- **Orientation is yours.** Studio's auto-orient is off, so what you model is
  what prints.

`examples/nameplate.py` is the worked reference. `references/modelling.md` has
the bpy recipes, the gotchas that cost an iteration, and the move each check
asks for.

## 4. Run the loop until it goes green

```bash
text3d make "<prompt>"
```

Models, DFM-checks, slices with the pinned X2D presets, and reports. The run is
**red** when `severity` is `error` — exit code 1, `status: needs_work`.

While red: read `advice`, apply the `fix` each note carries to `model.py`, run
again. Each note is phrased as a change to the model, because that is where the
fix lives. `jobs/<slug>/history.jsonl` accumulates what each iteration changed.

Hand over only once it is green.

## 5. Look at the plate

Read the PNG at `plate_png`. The numbers go green on parts that are printable
and still wrong — mirrored text, a hole in the wrong face, letters too shallow
to read. Look before you hand it over.

## 6. Hand off

Report time, grams and layers, then give the path:

```
jobs/<slug>/slice/<slug>.3mf
```

That is the project file. The `.gcode.3mf` beside it is a sliced job and
Studio's Prepare tab rejects it as having no geometry. Tell the user to open the
`.3mf`, check the preview, and print from Studio — and offer another iteration
if the preview is not what they wanted.

## Done when

`text3d make` exits 0, no note is at `error`, you have looked at the plate PNG,
and the user has the `.3mf` path.

## The machine

X2D, 0.4 mm nozzle, 256 × 256 × 260 mm bed, 0.20 mm layers, Bambu PLA Basic,
textured PEI plate. **The pinned process has supports off** — that is why an
overhang note is a real defect and not a setting to flip.
