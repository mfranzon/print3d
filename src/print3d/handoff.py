"""Everything a client, a teammate or a print shop needs, in one folder.

jobs/<slug>/handoff/ gets a one-page sheet (self-contained HTML that prints to
A4), renders of the part as assembled and pulled apart, the Studio project and
an STL per part in its print pose. The sheet says what was measured and what
was assumed, how the parts fit, what is left to check, and what it costs.
"""

from __future__ import annotations

import base64
import html
import json
import shutil
import time
from pathlib import Path
from typing import Any

from print3d.blender import BlenderError
from print3d.filaments import DEFAULT_COLOUR, colour_hex
from print3d.fits import load_fits
from print3d.printers import DEFAULT_PRINTER, printer_info
from print3d.render import render_parts
from print3d.spec import load_spec

SOURCES = {
    "measured": "measured off a photo",
    "listing": "stated by the listing",
    "user": "given",
    "assumed": "assumed",
}
VERDICTS = {
    "ok": "as intended",
    "tight": "tighter than intended",
    "loose": "looser than intended",
    "interference": "parts overlap",
    "unmeasured": "not measured",
    "fused": "too close to print apart",
    "clear": "",
}


class HandoffError(RuntimeError):
    """Raised when a job has no sliced run to hand off."""


def _esc(value: Any) -> str:
    return html.escape(str(value))


def _img(path: str | Path | None, alt: str) -> str:
    if not path or not Path(path).is_file():
        return ""
    data = base64.b64encode(Path(path).read_bytes()).decode("ascii")
    return f'<img src="data:image/png;base64,{data}" alt="{_esc(alt)}">'


def _size(mm: list[float]) -> str:
    return " × ".join(f"{v:.1f}" for v in mm) + " mm"


def _minutes(value: float | None) -> str:
    if value is None:
        return "–"
    hours, minutes = divmod(round(value), 60)
    return f"{hours} h {minutes:02d} min" if hours else f"{minutes} min"


def _parts(report: dict[str, Any], slug: str) -> list[dict[str, Any]]:
    if report.get("parts"):
        return report["parts"]
    return [
        {
            "name": slug,
            "object": slug,
            "plate": 1,
            "color": report["brief"].get("color_name") or DEFAULT_COLOUR,
            "print_size_mm": report["mesh_metrics"]["size_mm"],
            "settings": {},
        }
    ]


def job_printer(report: dict[str, Any]) -> dict[str, Any]:
    """The printer the job was sliced for. Reports from before print3d knew more than one printer are X2D jobs."""
    return report.get("printer") or printer_info(DEFAULT_PRINTER).as_dict()


def _table(head: list[str], rows: list[list[str]]) -> str:
    th = "".join(f"<th>{h}</th>" for h in head)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>" for row in rows)
    return f'<div class="wrap"><table><thead><tr>{th}</tr></thead><tbody>{body}</tbody></table></div>'


def _swatch(colour: str) -> str:
    return f'<span class="swatch" style="background:{colour_hex(colour)}"></span>{_esc(colour)}'


def _section(title: str, body: str) -> str:
    return f"<h2>{title}</h2>{body}" if body else ""


def _files(report: dict[str, Any], parts: list[dict[str, Any]], job_dir: Path, out: Path) -> list[str]:
    files: list[str] = []
    project = report.get("open_in_studio")
    if project and Path(project).is_file():
        shutil.copy2(project, out / Path(project).name)
        files.append(Path(project).name)
    viewer = job_dir / "view.html"
    if viewer.is_file():
        shutil.copy2(viewer, out / viewer.name)
        files.append(viewer.name)
    for part in parts:
        source = job_dir / "mesh" / "parts" / f"{part['name']}.stl"
        if not source.is_file():
            source = job_dir / "mesh" / f"{job_dir.name}.stl"
        if source.is_file():
            shutil.copy2(source, out / "stl" / f"{part['name']}.stl")
            files.append(f"stl/{part['name']}.stl")
    return files


def _renders(parts: list[dict[str, Any]], job_dir: Path, out: Path) -> tuple[dict[str, Path], str]:
    items = []
    for part in parts:
        stl = job_dir / "mesh" / "assembly" / f"{part['name']}.stl"
        if not stl.is_file():
            stl = out / "stl" / f"{part['name']}.stl"
        items.append({"stl": str(stl), "hex": colour_hex(part["color"]), "group": part["object"]})
    renders: dict[str, Path] = {}
    try:
        renders["assembled"] = render_parts(items, out / "assembled.png")
        if len({item["group"] for item in items}) > 1:
            renders["exploded"] = render_parts(items, out / "exploded.png", explode=1.2)
    except BlenderError as exc:
        return renders, f"Renders skipped: {str(exc).splitlines()[0]}"
    return renders, ""


def build_handoff(
    job_dir: Path, *, cost_per_kg: float | None = None, currency: str = "", render: bool = True
) -> dict[str, Any]:
    report_path = job_dir / "report.json"
    if not report_path.is_file():
        raise HandoffError(f"No run in {job_dir}: run `print3d make` first.")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if report.get("status") not in ("ready_for_review", "needs_work"):
        raise HandoffError(
            f"The last run of {job_dir.name} is {report.get('status', 'incomplete')}; "
            "complete `print3d make` without --dry-run before handing it off."
        )
    slug = job_dir.name
    spec = load_spec(job_dir)
    parts = _parts(report, slug)
    out = job_dir / "handoff"
    if out.exists():
        shutil.rmtree(out)
    (out / "stl").mkdir(parents=True)

    files = _files(report, parts, job_dir, out)
    renders, render_note = _renders(parts, job_dir, out) if render else ({}, "")
    files.extend(path.name for path in renders.values())

    metrics = report.get("slice_metrics", {})
    grams = metrics.get("filament_g")
    printer = job_printer(report)
    per_kg = cost_per_kg if cost_per_kg is not None else printer.get("filament_cost_per_kg")
    cost = grams * per_kg / 1000.0 if grams is not None and per_kg is not None else None
    money = (lambda v: f"{v:.2f} {_esc(currency)}".strip())
    listing = report.get("listing") or spec.get("listing")
    plates = report.get("plates") or [
        {
            "plate": 1,
            "png": report.get("plate_png"),
            "estimated_minutes": metrics.get("estimated_minutes"),
            "filament_g": grams,
            "layer_count": metrics.get("layer_count"),
        }
    ]
    fits = load_fits()
    ready = report.get("status") == "ready_for_review"

    stats = [
        ("Print time", _minutes(metrics.get("estimated_minutes"))),
        ("Filament", f"{grams:.1f} g" if grams is not None else "–"),
        ("Material", money(cost) if cost is not None else "–"),
        ("Parts", str(len(parts))),
        ("Plates", str(len(plates))),
    ]
    if listing and listing.get("price") is not None:
        stats.append(("Instead of buying", f"{listing['price']:.2f} {_esc(listing.get('currency') or '')}".strip()))

    part_rows = [
        [
            f"<b>{_esc(p['name'])}</b>"
            + (f'<div class="sub">prints fused into {_esc(p["object"])}</div>' if p["object"] != p["name"] else ""),
            _swatch(p["color"]),
            _size(p["print_size_mm"]),
            str(p["plate"]),
            ", ".join(f"{_esc(k)} = {_esc(v)}" for k, v in (p.get("settings") or {}).items()) or "–",
        ]
        for p in parts
    ]
    fit_rows = []
    for f in report.get("fits", []):
        verdict = f.get("verdict", "clear")
        gap = (
            f"{f['clearance_mm']:.2f} mm" if f["clearance_mm"] is not None
            else "touching" if f["contact"] else "–"
        )
        overlap = f" ({f['interference_mm']:.2f} mm)" if f["interference_mm"] else ""
        fit_rows.append(
            [
                f"{_esc(f['a'])} ↔ {_esc(f['b'])}",
                _esc(f.get("declared") or "–"),
                gap,
                f'<span class="v-{_esc(verdict)}">{_esc(VERDICTS.get(verdict, verdict))}</span>{overlap}',
            ]
        )
    fit_basis = (
        f"Clearance is per side. Calibrated on this printer ({', '.join(fits['calibrated'])}, "
        f"{fits['calibrated_at']})."
        if fits["calibrated"]
        else "Clearance is per side. These are print3d's defaults for a 0.4 mm nozzle in PLA; "
        "print the fit coupon (print3d coupon) to calibrate them."
    )
    dim_rows = []
    for name, dim in spec.get("dims", {}).items():
        evidence = []
        if dim.get("tolerance_mm"):
            evidence.append(f"± {dim['tolerance_mm']:.2f} mm")
        if dim.get("view"):
            evidence.append(f"{_esc(dim['view'])} view")
        if dim.get("quote"):
            evidence.append(f"“{_esc(dim['quote'])}”")
        if dim.get("why"):
            evidence.append(_esc(dim["why"]))
        dim_rows.append(
            [_esc(name), f"{dim['mm']:.2f} mm", _esc(SOURCES.get(dim["source"], dim["source"])), "; ".join(evidence)]
        )
    unknowns = "".join(f"<li>{_esc(u)}</li>" for u in spec.get("unknowns", []))
    notes = "".join(
        f'<li class="{_esc(a["severity"])}"><b>{_esc(a["code"])}</b>'
        + (f" · {_esc(a['part'])}" if a.get("part") else "")
        + f": {_esc(a['message'])} <i>{_esc(a['fix'])}</i></li>"
        for a in report.get("advice", [])
        if a["severity"] in ("error", "warn")
    )
    plate_cards = "".join(
        f'<figure>{_img(p.get("png"), "plate " + str(p["plate"]))}<figcaption>Plate {p["plate"]} · '
        f'{_minutes(p.get("estimated_minutes"))} · {(p.get("filament_g") or 0):.1f} g · '
        f'{p.get("layer_count") or "–"} layers</figcaption></figure>'
        for p in plates
    )
    hero = "".join(
        f'<figure>{_img(path, name)}<figcaption>{name}</figcaption></figure>' for name, path in renders.items()
    )
    replaces = (
        f'<p class="sub">Replaces <a href="{_esc(listing.get("url", ""))}">'
        f'{_esc(listing.get("name") or listing.get("url", ""))}</a></p>'
        if listing
        else ""
    )
    rate = f'<p class="sub">Material at {money(per_kg)} per kg.</p>' if cost is not None else ""
    skipped = f'<p class="sub">{_esc(render_note)}</p>' if render_note else ""
    stat_cards = "".join(f'<div class="stat"><b>{v}</b><span>{k}</span></div>' for k, v in stats)
    file_list = "".join(f"<li><code>{_esc(f)}</code></li>" for f in files)

    sheet = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_esc(slug)} print sheet</title>
<style>
:root {{ --ink:#1b1d21; --muted:#5f6670; --line:#e3e5e8; --bg:#ffffff; --panel:#f4f5f7;
        --ok:#1f7a3a; --warn:#9a6200; --err:#b3261e; }}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--bg); color:var(--ink);
        font:14px/1.5 -apple-system, "Segoe UI", Inter, Roboto, sans-serif; }}
main {{ max-width:960px; margin:0 auto; padding:32px 16px 48px; }}
header {{ display:flex; justify-content:space-between; gap:16px; align-items:flex-start;
          border-bottom:2px solid var(--ink); padding-bottom:12px; }}
h1 {{ font-size:24px; margin:0 0 4px; }}
h2 {{ font-size:12px; text-transform:uppercase; letter-spacing:.08em; color:var(--muted); margin:28px 0 8px; }}
.sub {{ color:var(--muted); font-size:12px; margin:4px 0 0; }}
.badge {{ padding:4px 10px; border-radius:999px; font-weight:600; font-size:12px; white-space:nowrap;
          background:{'#e6f4ea' if ready else '#fce8e6'}; color:{'var(--ok)' if ready else 'var(--err)'}; }}
.heroes {{ display:grid; grid-template-columns:repeat(auto-fit, minmax(260px, 1fr)); gap:12px; margin-top:16px; }}
figure {{ margin:0; background:var(--panel); border-radius:8px; padding:8px; }}
figure img {{ width:100%; height:auto; max-height:380px; object-fit:contain; display:block; }}
.plates figure img {{ max-height:260px; }}
figcaption {{ font-size:12px; color:var(--muted); text-align:center; }}
.heroes figcaption {{ text-transform:capitalize; }}
.stats {{ display:grid; grid-template-columns:repeat(auto-fit, minmax(120px, 1fr)); gap:8px; margin-top:16px; }}
.stat {{ border:1px solid var(--line); border-radius:8px; padding:10px 12px; }}
.stat b {{ display:block; font-size:18px; }}
.stat span {{ color:var(--muted); font-size:12px; }}
.wrap {{ overflow-x:auto; }}
table {{ width:100%; border-collapse:collapse; font-size:13px; }}
th, td {{ text-align:left; padding:6px 8px; border-bottom:1px solid var(--line); vertical-align:top; }}
th {{ font-weight:600; color:var(--muted); font-size:12px; }}
.swatch {{ display:inline-block; width:12px; height:12px; border-radius:3px; margin-right:6px;
           border:1px solid rgba(0,0,0,.25); vertical-align:-1px; }}
.v-ok {{ color:var(--ok); }}
.v-tight, .v-loose, .v-fused, .v-unmeasured {{ color:var(--warn); }}
.v-interference {{ color:var(--err); font-weight:600; }}
ul {{ padding-left:18px; margin:0; }}
li {{ margin:4px 0; }}
li.error b {{ color:var(--err); }}
li.warn b {{ color:var(--warn); }}
li i {{ color:var(--muted); }}
.plates {{ display:grid; grid-template-columns:repeat(auto-fit, minmax(200px, 1fr)); gap:12px; }}
footer {{ margin-top:32px; color:var(--muted); font-size:12px; border-top:1px solid var(--line); padding-top:8px; }}
@media print {{ main {{ padding:0; }} figure, table, .stat {{ break-inside:avoid; }} }}
</style></head>
<body><main>
<header>
  <div>
    <h1>{_esc(report["brief"]["prompt"])}</h1>
    <p class="sub">{_esc(slug)} · {time.strftime("%Y-%m-%d")} · {_esc(printer["summary"])}</p>
    {replaces}
  </div>
  <span class="badge">{'Ready to print' if ready else 'Needs work'}</span>
</header>
<div class="heroes">{hero}</div>
{skipped}
<div class="stats">{stat_cards}</div>
{rate}
{_section("Parts", _table(["Part", "Colour", "Printed size", "Plate", "Settings"], part_rows))}
{_section("Fits", _table(["Parts", "Intended", "Clearance", "Result"], fit_rows) + f'<p class="sub">{_esc(fit_basis)}</p>' if fit_rows else "")}
{_section("Dimensions", _table(["Name", "Value", "Source", "Evidence"], dim_rows) if dim_rows else "")}
{_section("Check on the printed part", f"<ul>{unknowns}</ul>" if unknowns else "")}
{_section("Open notes", f"<ul>{notes}</ul>" if notes else "")}
{_section("Plates", f'<div class="plates">{plate_cards}</div>')}
{_section("Files", f"<ul>{file_list}</ul>")}
<p class="sub">Open the <code>.3mf</code> in {_esc(printer.get("slicer_name", "Bambu Studio"))} to review and print. Each STL is one part in its print pose.</p>
<footer>Made with print3d. Nothing here was sent to a printer.</footer>
</main></body></html>
"""
    sheet_path = out / f"{slug}-sheet.html"
    sheet_path.write_text(sheet, encoding="utf-8")
    return {
        "sheet": str(sheet_path),
        "dir": str(out),
        "files": [str(out / f) for f in files],
        "renders": {name: str(path) for name, path in renders.items()},
        "filament_g": grams,
        "material_cost": round(cost, 2) if cost is not None else None,
        "cost_per_kg": per_kg,
        **({"render_note": render_note} if render_note else {}),
    }
