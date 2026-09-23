# text-3d

Idea to a reviewable **Bambu Lab X2D 0.4 mm** project.

An agent (or you) writes a Blender model script, the CLI runs it headless,
checks the mesh, slices it with the official Bambu Studio CLI, and reports what
the slice says about printability. Fix the script, run again, repeat until it is
clean.

```
idea / image / text  ->  Blender model script  ->  mesh  ->  DFM + printability
                              ^                                     |
                              +------- advice says what to fix ------+
                                              |
                                        slice -> .3mf -> review in Studio
```

**It stops at review.** Nothing in this repo talks to the printer. When the
slice looks right, open the `.3mf` in Bambu Studio and print it from there.

## Install

```bash
git clone git@github.com:mfranzon/text-3d.git
cd text-3d
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/python -m text3d status
```

Needs [Blender](https://www.blender.org/download/) 4.2+ and
[Bambu Studio](https://bambulab.com/en/download/studio). Both are found
automatically on macOS; otherwise set `BLENDER_BIN` and `BAMBU_STUDIO_BIN`.
X2D 0.4 presets ship in `profiles/x2d-0.4/`. Other printers:
`python -m text3d flatten` against that machine's Studio install.

## Use

```bash
.venv/bin/python -m text3d make "a 40mm nameplate that says MARCO" --script examples/nameplate.py
```

With no `--script`, a starter `jobs/<slug>/model.py` is written for you to fill in.

```bash
.venv/bin/python -m text3d model examples/nameplate.py out.stl --prompt "..."  # model only
.venv/bin/python -m text3d slice path/to/model.stl                             # slice a mesh
.venv/bin/python -m text3d check out.stl --project job.gcode.3mf               # printability only
.venv/bin/python -m text3d inspect tests/fixtures/cube_20mm.gcode.3mf
```

`make` exits 1 when a check comes back at `error` severity.

## The /print3d skill

`skill/` is an agent skill wrapping the whole loop: read the brief, write the
model, run until the checks go green, look at the plate, hand over the `.3mf`.
It is user-invoked, so it fires only when you type it.

```bash
ln -sfn "$(pwd)/skill" ~/.claude/skills/print3d
```

Then `/print3d a 40mm nameplate that says MARCO`. The bpy recipes and the
gotchas live in `skill/references/modelling.md`.

## Model scripts

A model script is plain bpy that builds geometry and nothing else. The harness
supplies an empty scene and, afterwards, joins the mesh objects, removes
doubles, fills holes, recalculates normals, triangulates, centres the part and
drops it on the bed before exporting.

- **1 Blender unit == 1 mm.**
- `params` is in scope: `prompt`, `slug`, `text`, `intent`, `size_mm`, `color`,
  `material`.
- Orientation is yours. Studio's auto-orient is **off**, so what you model is
  what prints - otherwise the slicer would quietly rotate the part and every
  overhang note below would be about geometry that never gets printed.

`examples/nameplate.py` is the reference.

## What the checks look at

Two independent signals, because neither is sufficient alone:

- **The mesh** - overhang area against the 45 deg rule, bed contact, slenderness,
  thinnest dimension. This is where most of the weight sits: the pinned process
  has `enable_support = 0`, so the slicer never warns about an overhang, it just
  prints it badly.
- **The slice** - time, filament, and material per layer recovered from the
  gcode. The Studio CLI strips `;TYPE:` feature comments, so a material jump is
  only reported when the mesh independently shows an unsupported region; a
  sparse-infill-to-top-shell transition looks identical otherwise.

Every note carries a `fix` phrased as a change to make in the model script.
`jobs/<slug>/history.jsonl` records each iteration.

## Layout

- `src/text3d/` pipeline, Blender runner, DFM, printability, Studio wrapper, 3mf repair
- `examples/` reference model scripts
- `profiles/x2d-0.4/` flattened Studio machine/process/filament JSON
- `skill/` the `/print3d` agent skill
- `jobs/` run artifacts (gitignored)

## Tests

```bash
.venv/bin/python -m pytest -q                          # unit
TEXT3D_STUDIO_IT=1 .venv/bin/python -m pytest -q       # plus live Blender and Studio
```
