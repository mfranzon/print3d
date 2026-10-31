"""print3d as an MCP server, for any agent that speaks MCP.

The same operations as the CLI. Tools that produce pictures (the coordinate
grid, the plate preview, the likeness overlays) return them as images, so the
agent sees them without a separate file read.
"""

from __future__ import annotations

import functools
import json
from pathlib import Path
from typing import Any, Literal

from mcp.server.mcpserver import Image, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from print3d import printers
from print3d import spec as specs
from print3d.dfm import check_mesh
from print3d.doctor import dependency_status
from print3d.fits import load_fits, save_fits
from print3d.handoff import build_handoff
from print3d.likeness import check_job
from print3d.paths import coupon_script, job_path, safe_name, skill_dir
from print3d.photo import grid as draw_grid
from print3d.photo import meet as cross
from print3d.pipeline import run_job
from print3d.viewer import build_viewer
from print3d.printability import advise, analyze_mesh, analyze_slice, dfm_advice, worst_severity

INSTRUCTIONS = """\
print3d turns an idea, or a photo of an object, into a Bambu Studio project for the
user's Bambu Lab printer: the X2D, or the P2S (`printer` shows which one print3d
slices for, and switches). You write a Blender (bpy) model script; `make` runs it,
checks the mesh against that printer, slices it and returns advice, each note with
the fix to apply to the script. Iterate until no note is at `error`, then hand the
user the .3mf path and name the printer in the report's `printer`.

Call `doctor` before writing a model for a new job; resolve missing or unavailable
dependencies first. Read guide("workflow") before the first run. From a photo of
an object, also read
guide("from-photo"): it records every dimension with its evidence and adds a
likeness check against the photo. To replace something sold in a shop, read
guide("from-listing"): the listing's stated sizes and product photos stand in for
the object. For more than one part, colours, or parts that fit together, read
guide("parts"). Once a job is green, `handoff` writes a print sheet for the user
to share. Each run also writes `viewer`, an HTML page with the model in 3D: open
it in your preview pane when you have one, or give the user its path. Nothing
here sends anything to the printer.
"""

GUIDES = {
    "workflow": "SKILL.md",
    "modelling": "references/modelling.md",
    "from-photo": "references/from-photo.md",
    "from-listing": "references/from-listing.md",
    "parts": "references/parts-and-fits.md",
}

server = MCPServer("print3d", instructions=INSTRUCTIONS)


def tool(*, images: bool = False, read_only: bool = False):
    """Register a tool. print3d errors already say what to change, so the agent gets their text.

    `read_only` tools only read, or write views derived from what is there, so
    hosts can run them without asking.
    """

    def register(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001
                raise ToolError(str(exc)) from exc

        annotations = ToolAnnotations(read_only_hint=True) if read_only else None
        return server.tool(structured_output=False if images else None, annotations=annotations)(wrapper)

    return register


def _json(payload: Any) -> str:
    return json.dumps(payload, indent=2)


def _saved(slug: str, spec: dict[str, Any], entry: Any) -> str:
    return _json({"saved": str(specs.save_spec(job_path(slug), spec)), "entry": entry})


def _with_images(payload: dict[str, Any], paths: list[str | None]) -> list[Any]:
    return [_json(payload), *(Image(path=p) for p in paths if p and Path(p).is_file())]


@tool(read_only=True)
def doctor() -> str:
    """Check Blender and Bambu Studio paths, availability and versions before writing a model.

    Studio is ready when found; version detection never blocks readiness.
    Windows/macOS read metadata without launching Studio; Linux probes --help.
    Blender must pass --version. Unknown versions are reported as null.
    Does not create jobs or change agent settings.
    """
    return _json(dependency_status())


@tool(read_only=True)
def guide(topic: Literal["workflow", "modelling", "from-photo", "from-listing", "parts"]) -> str:
    """How to use print3d: the loop, bpy recipes and gotchas, reverse-engineering from a photo or a
    shop listing, and multi-part, multi-colour models with fits."""
    return (skill_dir() / GUIDES[topic]).read_text(encoding="utf-8")


@tool(images=True)
def make(
    prompt: str,
    script: str | None = None,
    mesh: str | None = None,
    color: str | None = None,
    dry_run: bool = False,
) -> list[Any]:
    """Model, check and slice. With no model yet, writes a starter model.py and names its path.

    Returns the report, then the plate preview and any likeness overlays.
    """
    report = run_job(
        prompt,
        script=Path(script) if script else None,
        mesh=Path(mesh) if mesh else None,
        color=color,
        execute=not dry_run,
    )
    overlays = [r["overlay"] for r in report.get("likeness", [])]
    plates = [p["png"] for p in report.get("plates", [])] or [report.get("plate_png")]
    return _with_images(report, [*plates, *overlays])


@tool(images=True)
def handoff(slug: str, cost_per_kg: float | None = None, currency: str | None = None) -> list[Any]:
    """Write jobs/<slug>/handoff: a print sheet (HTML), renders, the .3mf and an STL per part.

    Run it once the job is green. Returns the paths, then the renders.
    """
    result = build_handoff(job_path(slug), cost_per_kg=cost_per_kg, currency=currency or "")
    return _with_images(result, list(result["renders"].values()))


@tool(images=True)
def fit_coupon() -> list[Any]:
    """Slice the fit coupon: a pin and holes at 0.00 to 0.40 mm clearance, to calibrate fits on this printer."""
    report = run_job("fit coupon", script=coupon_script())
    return _with_images(report, [report.get("plate_png")])


@tool()
def fits(
    press: float | None = None, snug: float | None = None, slide: float | None = None, free: float | None = None
) -> str:
    """This printer's clearance per side for each fit, as model scripts get it in params["fit"].

    Pass values to record what the user felt on the printed fit coupon.
    """
    values = {k: v for k, v in {"press": press, "snug": snug, "slide": slide, "free": free}.items() if v is not None}
    return _json(save_fits(values) if values else load_fits())


@tool()
def printer(choose: str | None = None) -> str:
    """The Bambu Lab printer print3d slices for: build volume, nozzles, presets, and the others it has.

    Pass `choose` (e.g. "p2s-0.4" or "x2d-0.4") to switch; the choice is saved for every later
    job and rerun. Only switch when the user asks for a different printer.
    """
    return _json(printers.choose_printer(choose) if choose else printers.status())


@tool(images=True, read_only=True)
def grid(photo: str, crop: list[float] | None = None, step: int | None = None) -> list[Any]:
    """A photo, or a zoomed crop [x0, y0, x1, y1] of it, with photo-pixel coordinates drawn on.

    Read every point you measure off a crop: labels are photo pixels.
    """
    if crop is not None and len(crop) != 4:
        raise ValueError("crop is [x0, y0, x1, y1]")
    result = draw_grid(Path(photo), crop=tuple(crop) if crop else None, step=step)
    return _with_images(result, [str(result["grid"])])


@tool()
def measure_scale(
    slug: str,
    view: str,
    photo: str,
    ref: str,
    points: list[list[float]],
    look: str | None = None,
    up: str | None = None,
) -> str:
    """Scale a photo from a reference of known length in it.

    `ref` is a length in mm, dim:<name> for a dimension already quoted from the
    listing or given by the user, or one of: card, card-short, a4, a4-short,
    letter, letter-short, euro-1, euro-2, euro-0.50, us-quarter, us-penny, uk-pound.
    `points` are the reference's two ends. Views front/back/left/right/top/bottom
    set the camera axes; for another pose pass look/up (+x, -y, ...) in the printed part.
    """
    spec = specs.load_spec(job_path(slug))
    entry = specs.set_scale(spec, view, Path(photo), ref, points, look=look, up=up)
    return _saved(slug, spec, entry)


@tool(images=True)
def measure_rectify(
    slug: str,
    view: str,
    photo: str,
    corners: list[list[float]],
    size_mm: list[float],
    look: str | None = None,
    up: str | None = None,
) -> list[Any]:
    """Scale a view from a flat face of known size [W, H] seen at an angle.

    `corners` are the face's top-left, top-right, bottom-right, bottom-left as it
    should appear square-on. Returns the rectified image: read every later point
    for this view off it.
    """
    spec = specs.load_spec(job_path(slug))
    out = job_path(slug) / "views" / f"{safe_name(view, 'view')}.png"
    entry = specs.rectify_view(spec, view, Path(photo), corners, (size_mm[0], size_mm[1]), out, look=look, up=up)
    return _with_images(json.loads(_saved(slug, spec, entry)), [str(out)])


@tool(read_only=True)
def meet(edge_a: list[list[float]], edge_b: list[list[float]]) -> str:
    """Where two edges cross, each given as two points: a corner hidden behind something."""
    return _json({"corner": list(cross(*edge_a, *edge_b))})


@tool()
def measure_dim(
    slug: str,
    name: str,
    view: str | None = None,
    points: list[list[float]] | None = None,
    mm: float | None = None,
    source: Literal["user", "listing", "assumed"] | None = None,
    why: str | None = None,
    quote: str | None = None,
    tolerance_mm: float | None = None,
) -> str:
    """Record a dimension: measured from two points in a scaled view, or typed in as given, listed or assumed.

    A `listing` dimension needs `quote`, the listing's own words for it, and
    `tolerance_mm` when the listing rounds coarser than whole millimetres (5 for cm).
    The model script reads it as params["dims"][name].
    """
    spec = specs.load_spec(job_path(slug))
    if mm is not None and source == "listing":
        entry = specs.listing_dim(spec, name, mm, quote, tolerance_mm)
    elif mm is not None:
        entry = specs.given_dim(spec, name, mm, source or "", why)
    elif view and points:
        entry = specs.measure_dim(spec, name, view, points)
    else:
        raise ValueError("Give view and points to measure, or mm and source to type it in.")
    return _saved(slug, spec, entry)


@tool()
def measure_outline(
    slug: str, view: str, points: list[list[float]] | None = None, mask: str | None = None
) -> str:
    """The object's outline in a view, as a polygon in photo pixels or a photo-sized mask PNG.

    Every outlined view adds a likeness check to `make`.
    """
    spec = specs.load_spec(job_path(slug))
    entry = specs.set_outline(spec, view, points=points, mask=Path(mask) if mask else None)
    return _saved(slug, spec, entry)


@tool()
def set_listing(
    slug: str, url: str, name: str | None = None, price: float | None = None, currency: str | None = None
) -> str:
    """The product this part replaces: its shop page, name and price."""
    spec = specs.load_spec(job_path(slug))
    return _saved(slug, spec, specs.set_listing(spec, url, name, price, currency))


@tool()
def add_unknown(slug: str, text: str) -> str:
    """Record something no photo shows, for the user to check on the printed part."""
    spec = specs.load_spec(job_path(slug))
    return _saved(slug, spec, specs.add_unknown(spec, text))


@tool(read_only=True)
def show_spec(slug: str) -> str:
    """Everything recorded about the object: views, dimensions with their evidence, unknowns."""
    return _json(specs.load_spec(job_path(slug)))


@tool(images=True, read_only=True)
def likeness(slug: str, mesh: str | None = None) -> list[Any]:
    """Compare the job's mesh with every outlined photo view. Returns results, advice and overlays."""
    results, notes = check_job(job_path(slug), Path(mesh) if mesh else None)
    payload = {"likeness": results, "advice": [note.as_dict() for note in notes]}
    return _with_images(payload, [r["overlay"] for r in results])


@tool(read_only=True)
def view(slug: str) -> str:
    """Rebuild jobs/<slug>/view.html, the model in 3D, from the job's last run. Returns its path."""
    job_dir = job_path(slug)
    report = json.loads((job_dir / "report.json").read_text(encoding="utf-8"))
    viewer = build_viewer(job_dir, report)
    if viewer is None:
        raise ValueError(f"No mesh in {job_dir}; run make first.")
    return str(viewer)


@tool(read_only=True)
def check(mesh: str, project: str | None = None) -> str:
    """Structural validity and printability of an STL, and its sliced .gcode.3mf when given."""
    dfm = check_mesh(Path(mesh))
    mesh_metrics = analyze_mesh(Path(mesh))
    slice_metrics = analyze_slice(Path(project)) if project else None
    notes = advise(mesh_metrics, slice_metrics, extra=dfm_advice(dfm))
    return _json({
        "dfm": dfm.as_dict(), "mesh_metrics": mesh_metrics,
        "advice": [note.as_dict() for note in notes], "severity": worst_severity(notes),
    })


def main() -> None:
    server.run("stdio")
