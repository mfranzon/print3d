# Parts, colours and fits

For anything that is more than one solid: a box and its lid, a case in two
halves, a name inlaid in a second colour, a pin that goes into a hole. Without
`part()` the harness joins every mesh into one part, exactly as before.

`examples/lidded_box.py` is the worked reference.

## Model it assembled

Build every part **where it sits when the thing is put together**: the lid on
the box, the pin in its hole. That pose is what the run measures fits in, what
the likeness check compares with a photo, and what the hand-off renders. Then
say how each part turns to print:

```python
part(body, "body", color="white")
part(lid, "lid", color="white", rotate=(180, 0, 0))   # printed upside down
part(name, "name", color="black", group="lid", rotate=(180, 0, 0))
fit("lid", "body", "slide")
```

`part(objects, name, *, color, group, rotate, plate, settings)`, in scope in
every model script beside `params`:

| argument | what it does |
| --- | --- |
| `objects` | a mesh object, or a list of them, joined into this part |
| `name` | letters, digits, `-` and `_`; it names the STL files |
| `color` | a name (`white`, `black`, `red`, ... ) or `#RRGGBB`; default the brief's colour. Each colour gets its own filament |
| `group` | the name of another part to print fused with, as one multi-colour object |
| `rotate` | degrees about x, y, z from the assembled pose into the print pose |
| `plate` | a build plate number, when parts should not share one |
| `settings` | Studio process overrides for this part, e.g. `{"wall_loops": 4, "sparse_infill_density": "40%"}` |

Every mesh left in the scene must belong to a part; delete helpers and boolean
cutters. Parts in one `group` need the same `rotate` and `plate`: they print as
one object.

The harness centres each printed object and drops it on the bed, so `rotate` is
all the placement a part needs. Studio arranges the objects on each plate.

## Colours

The X2D has two nozzles: two colours print without a filament change. A third
colour is swapped in on one of them, with a purge each time; the run says so.
The P2S has one nozzle: every colour after the first is a filament change
through the AMS, with a purge each time and a prime tower beside the part; the
run says so too. Colour never changes the material: every filament is the
pinned Bambu PLA Basic in another colour.

**Inlays** (a name, a logo, a stripe) are a part in the second colour fused with
the part around it:

1. Model the inlay as a solid, flush with the surface.
2. Cut its pocket with a **taller copy** that breaks through the surface. A
   cutter that ends flush with the surface leaves a sealed void under an
   unbroken skin; the run reports that as `buried`.
3. `part(inlay, ..., group="<the part around it>")`.

Put the inlaid face on the bed when you can (`rotate`): the first layer is the
flattest and sharpest surface the printer makes, and the colour change happens
in a few layers instead of all the way up. 0.6 mm (three layers) of inlay is
enough to read.

## Fits

`params["fit"]` holds this printer's clearance per side, in mm, for four fits:

| fit | feels like | default |
| --- | --- | --- |
| `press` | pressed or tapped in, stays put | 0.05 |
| `snug` | pushes in by hand, holds | 0.10 |
| `slide` | moves without play | 0.20 |
| `free` | turns or drops in loosely | 0.35 |

Size the mating feature from it instead of guessing a number per part: a lid
plug that slides into a 36 mm opening is `36 - 2 * params["fit"]["slide"]`
wide. Then declare the fit with `fit(a, b, kind)` and the run measures it.

What the run measures between every two parts that come within 2 mm:

- **interference** (error): one part pokes into the other. They will not go
  together.
- **fit** (warn): a declared fit whose gap is tighter or looser than its
  clearance, or that could not be measured because the parts are not modelled
  together.
- **fused** (warn): two undeclared parts closer than a press fit but not
  touching. They will print as one, or not at all.

Touching faces (a lid resting on a rim) are contact, not a fit. The gap is read
from each part's vertices to the other's surface, so a curved fit needs enough
segments (64 or more on a 10 mm bore) to be read to a hundredth.

### Calibrating

The defaults are a starting point. To measure this printer:

```bash
print3d coupon            # slices a pin and holes at 0.00 to 0.40 mm clearance
```

Print it, push the pin into each hole (the label is the clearance in hundredths
of a mm), and record where it presses, holds, slides and turns:

```bash
print3d fit set --press 0.05 --snug 0.10 --slide 0.15 --free 0.30
print3d fit show
```

Every later run hands these to the model scripts and measures against them.
Calibration lives in `~/print3d/fits.json` (or `$PRINT3D_FITS`).

## What the run reports

Beside the usual report, a multi-part run carries `parts` (each with its
object, plate, colour and filament), `filaments` (grams per colour), `fits`
(each pair with `clearance_mm`, `interference_mm`, `declared` and `verdict`)
and `plates` (time, grams, layers and a preview PNG per plate). Every advice
note names the `part` it is about.

Files, in `jobs/<slug>/mesh/`: `assembly/<part>.stl` as modelled,
`parts/<part>.stl` in print pose (what Studio slices), and
`objects/<group>.stl` (the fused solid the checks read).

## Hand-off

```bash
print3d handoff <slug> [--currency EUR] [--cost-per-kg 25]
```

Writes `jobs/<slug>/handoff/`: a one-page print sheet (`<slug>-sheet.html`,
self-contained, prints to A4) with renders of the part assembled and exploded,
time, grams and material cost, the parts and their colours, the fits and how
they measured, every dimension with its evidence, the unknowns to check on the
print, and the open notes; plus the Studio `.3mf` and an STL per part. Look at
the renders before you send it: they are the quickest way to catch a colour
that does not show.
