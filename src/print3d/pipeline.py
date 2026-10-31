"""Idea to a reviewable Bambu Studio project.

    prompt or photos  ->  Blender model script  ->  mesh  ->  DFM + printability
         |                    ^                                + likeness to the photos
     spec.json                +---- advice tells you what to fix ---+
                                              |
                                        slice -> .3mf -> review in Studio

The loop stops at review. Nothing is ever sent to the printer from here.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from print3d.blender import BlenderError, run_model_script, scaffold_script
from print3d.brief import PrintBrief, parse_brief
from print3d.dfm import check_mesh, require_printable
from print3d.filaments import DEFAULT_COLOUR, colour_advice, colour_hex, job_filaments
from print3d.fits import fit_advice, load_fits
from print3d.likeness import check_likeness, likeness_advice
from print3d.paths import default_jobs_dir, default_profiles_dir
from print3d.printers import Printer, read_printer
from print3d.printability import (
    Advice,
    advise,
    analyze_mesh,
    analyze_slice,
    corroborates_layer_jump,
    mesh_advice,
    slice_advice,
    with_clean,
    worst_severity,
)
from print3d.project import extract_plate_png, inspect_project
from print3d.spec import load_spec, model_dims
from print3d.studio import SliceRequest, assemble_list, find_studio, run_slice, slice_plan
from print3d.viewer import build_viewer

MODEL_SCRIPT = "model.py"
HISTORY = "history.jsonl"


class PipelineError(RuntimeError):
    """Raised when a job cannot be prepared."""


def _model_params(brief: PrintBrief) -> dict[str, Any]:
    return {
        "prompt": brief.prompt,
        "slug": brief.slug,
        "text": brief.text,
        "intent": brief.intent,
        "size_mm": list(brief.target_size_mm) if brief.target_size_mm else None,
        "color": brief.color_name,
        "material": brief.material,
    }


def _resolve_script(brief: PrintBrief, job_dir: Path, script: Path | None) -> Path:
    if script is not None:
        resolved = script.expanduser().resolve()
        if not resolved.is_file():
            raise PipelineError(f"Model script does not exist: {resolved}")
        return resolved
    in_job = job_dir / MODEL_SCRIPT
    if in_job.is_file():
        return in_job
    scaffold_script(
        in_job,
        prompt=brief.prompt,
        slug=brief.slug,
        size=brief.target_size_mm or (40.0, 40.0, 40.0),
    )
    raise PipelineError(
        f"No model yet. A starter script is at {in_job} - write the part in it "
        "(bpy, 1 unit = 1 mm) and run again, or pass --script/--mesh. "
        "See examples/nameplate.py."
    )


def _append_history(job_dir: Path, entry: dict[str, Any]) -> None:
    with (job_dir / HISTORY).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry) + "\n")


def _save_report(job_dir: Path, report: dict[str, Any]) -> None:
    """Replace the report only after the complete JSON has been written."""
    pending = job_dir / "report.json.tmp"
    pending.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    pending.replace(job_dir / "report.json")


def run_job(
    prompt: str,
    *,
    script: Path | None = None,
    mesh: Path | None = None,
    color: str | None = None,
    execute: bool = True,
    jobs_dir: Path | None = None,
    profiles_dir: Path | None = None,
) -> dict[str, Any]:
    brief = parse_brief(prompt, color=color)
    job_dir = (jobs_dir or default_jobs_dir()) / brief.slug
    job_dir.mkdir(parents=True, exist_ok=True)
    # Modelling can overwrite meshes before a failure or a dry-run return.
    # Invalidate the previous slice's report before touching any job artifacts.
    report: dict[str, Any] = {"brief": brief.as_dict(), "job_dir": str(job_dir), "status": "running"}
    _save_report(job_dir, report)
    try:
        result = _run_job(
            brief, job_dir, report, script=script, mesh=mesh, execute=execute,
            profiles=profiles_dir or default_profiles_dir(),
        )
    except Exception as exc:
        report.update(status="failed", error=str(exc))
        _save_report(job_dir, report)
        raise
    _save_report(job_dir, result)
    return result


def _run_job(
    brief: PrintBrief,
    job_dir: Path,
    report: dict[str, Any],
    *,
    script: Path | None,
    mesh: Path | None,
    execute: bool,
    profiles: Path,
) -> dict[str, Any]:
    for required in ("machine.json", "process.json", "filament.json"):
        if not (profiles / required).is_file():
            raise PipelineError(
                f"Pinned profile missing: {profiles / required}. "
                f"Run: python -m print3d flatten --printer {profiles.name}"
            )
    printer = read_printer(profiles)
    report["printer"] = printer.as_dict()

    fits = load_fits()
    spec = load_spec(job_dir)
    if spec["dims"] or spec["unknowns"]:
        report["spec"] = {"dims": spec["dims"], "unknowns": spec["unknowns"]}
    if "listing" in spec:
        report["listing"] = spec["listing"]
    (job_dir / "brief.json").write_text(json.dumps(brief.as_dict(), indent=2) + "\n", encoding="utf-8")

    # 1-2. Model.
    if mesh is not None:
        mesh_path = mesh.expanduser().resolve()
        if not mesh_path.is_file():
            raise PipelineError(f"Mesh does not exist: {mesh_path}")
    else:
        model_script = _resolve_script(brief, job_dir, script)

    # Keep scaffolding and dry runs usable without Studio. For a real run,
    # fail inside the report lifecycle before Blender can overwrite geometry.
    if execute:
        find_studio()

    if mesh is not None:
        report["model"] = {"mesh": str(mesh_path), "source": "supplied"}
    else:
        mesh_path = job_dir / "mesh" / f"{brief.slug}.stl"
        try:
            report["model"] = run_model_script(
                model_script,
                mesh_path,
                params={**_model_params(brief), "dims": model_dims(spec), "fit": fits["clearance_mm"]},
            )
        except BlenderError as exc:
            raise PipelineError(str(exc)) from exc
        report["model"]["source"] = "blender"

    if report["model"].get("parts"):
        return _run_parts(brief, job_dir, profiles, printer, spec, fits, mesh_path, report, execute)

    # 3. Is it even a solid, and does it fit this printer?
    dfm = require_printable(mesh_path, bed_mm=printer.bed_mm)
    report["dfm"] = dfm.as_dict()
    mesh_metrics = analyze_mesh(mesh_path)
    report["mesh_metrics"] = mesh_metrics
    likeness = check_likeness(mesh_path, spec, job_dir / "likeness")
    if likeness:
        report["likeness"] = likeness
    likeness_notes = likeness_advice(likeness)

    request = SliceRequest(
        input_mesh=mesh_path,
        output_3mf=job_dir / "slice" / f"{brief.slug}.gcode.3mf",
        output_project_3mf=job_dir / "slice" / f"{brief.slug}.3mf",
        machine=profiles / "machine.json",
        process=profiles / "process.json",
        filaments=(
            tuple(job_filaments(profiles / "filament.json", [brief.color_name], job_dir / "filaments"))
            if brief.color_name
            else (profiles / "filament.json",)
        ),
        datadir=job_dir / "studio-datadir",
        output_dir=job_dir / "slice",
        # Auto-orient would silently rotate the part and invalidate every
        # overhang note below. Orientation is the model script's business.
        orient=False,
    )
    report["slice_plan"] = slice_plan(request)

    if not execute:
        report["status"] = "dry_run"
        notes = advise(mesh_metrics, extra=likeness_notes)
        report["advice"] = [note.as_dict() for note in notes]
        report["severity"] = worst_severity(notes)
        return report

    # 4. Slice, then read back what the slicer actually did.
    slice_result = run_slice(request)
    project = Path(slice_result["output_3mf"])
    inspection = inspect_project(project)
    png = extract_plate_png(project, job_dir / "slice" / f"{brief.slug}_plate.png")
    slice_metrics = analyze_slice(project)
    notes = advise(mesh_metrics, slice_metrics, extra=likeness_notes)
    severity = worst_severity(notes)

    report.update(
        {
            "status": "needs_work" if severity == "error" else "ready_for_review",
            "slice_result": {
                "returncode": slice_result["returncode"],
                "output_3mf": slice_result["output_3mf"],
                "output_project_3mf": slice_result.get("output_project_3mf"),
                "output_size_bytes": slice_result["output_size_bytes"],
            },
            "inspection": {"plates": inspection["plates"], "path": inspection["path"]},
            "plate_png": str(png) if png else None,
            "slice_metrics": {k: v for k, v in slice_metrics.items() if k != "layers"},
            "layers": slice_metrics["layers"],
            "advice": [note.as_dict() for note in notes],
            "severity": severity,
            "open_in_studio": slice_result.get("open_in_studio")
            or slice_result.get("output_project_3mf"),
            "next_step": (
                "Fix the errors in the model script and run again."
                if severity == "error"
                else f"Open the .3mf in Bambu Studio (set up for the {printer.name}), check the preview, "
                "and print it from there."
            ),
        }
    )
    viewer = build_viewer(job_dir, report)
    report["viewer"] = str(viewer) if viewer else None
    _append_history(
        job_dir,
        {
            "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "printer": printer.key,
            "severity": severity,
            "size_mm": [round(v, 2) for v in mesh_metrics["size_mm"]],
            "triangles": mesh_metrics["triangles"],
            "overhang_fraction": round(mesh_metrics["overhang_fraction"], 4),
            "estimated_minutes": slice_metrics["estimated_minutes"],
            "filament_g": slice_metrics["filament_g"],
            "codes": [note.code for note in notes],
            "likeness": {r["view"]: r["iou"] for r in likeness},
        },
    )
    return report


def _run_parts(
    brief: PrintBrief,
    job_dir: Path,
    profiles: Path,
    printer: Printer,
    spec: dict[str, Any],
    fits: dict[str, Any],
    assembly: Path,
    report: dict[str, Any],
    execute: bool,
) -> dict[str, Any]:
    """Several printed parts: a check per printed object, fits between them, one slice for all."""
    model = report["model"]
    parts = {part["name"]: part for part in model["parts"]}

    colours: list[str] = []
    for part in parts.values():
        part["color"] = part["color"] or brief.color_name or DEFAULT_COLOUR
        colour_hex(part["color"])
        key = part["color"].strip().lower()
        if key not in colours:
            colours.append(key)
        part["filament"] = colours.index(key) + 1
    filaments = job_filaments(profiles / "filament.json", colours, job_dir / "filaments")
    report["filaments"] = [
        {"filament": i, "color": c, "hex": colour_hex(c)} for i, c in enumerate(colours, start=1)
    ]

    # Plates are numbered 1..n for Studio, in the order the script named them.
    named = sorted({obj["plate"] or 1 for obj in model["objects"]})
    for obj in model["objects"]:
        obj["plate"] = named.index(obj["plate"] or 1) + 1
        for name in obj["parts"]:
            parts[name]["plate"], parts[name]["object"] = obj["plate"], obj["name"]

    notes: list[Advice] = []
    errors: list[str] = []
    metrics: dict[str, dict[str, Any]] = {}
    for obj in model["objects"]:
        dfm = check_mesh(Path(obj["stl"]), bed_mm=printer.bed_mm)
        errors.extend(f"{obj['name']}: {error}" for error in dfm.errors)
        metrics[obj["name"]] = analyze_mesh(Path(obj["stl"]))
        notes.extend(mesh_advice(metrics[obj["name"]], part=obj["name"]))
    for name in model.get("buried", []):
        notes.append(
            Advice(
                "buried",
                "error",
                f"{name} is sealed inside {parts[name]['object']}: none of its surface shows, so its "
                "colour will not either.",
                "Cut its pocket with a cutter that breaks through the surface (taller than the "
                "pocket), not one flush with it.",
                part=name,
            )
        )
    if errors:
        raise PipelineError("; ".join(errors))
    if note := colour_advice(len(colours), printer):
        notes.append(note)
    report["mesh_metrics"] = metrics

    likeness = check_likeness(assembly, spec, job_dir / "likeness")
    if likeness:
        report["likeness"] = likeness
    notes.extend(likeness_advice(likeness))
    notes.extend(fit_advice(model["fits"], model["declared_fits"], fits["clearance_mm"]))
    report["fits"] = model["fits"]
    report["fit_clearance_mm"] = fits["clearance_mm"]
    report["parts"] = [
        {k: part[k] for k in ("name", "object", "plate", "color", "filament", "print_size_mm", "settings")}
        for part in parts.values()
    ]

    slice_dir = job_dir / "slice"
    slice_dir.mkdir(parents=True, exist_ok=True)
    assemble = slice_dir / "assemble.json"
    assemble.write_text(
        json.dumps(
            assemble_list(
                [
                    {
                        "plate": obj["plate"],
                        "parts": [
                            {
                                "stl": parts[name]["print_stl"],
                                "filament": parts[name]["filament"],
                                "settings": parts[name]["settings"],
                            }
                            for name in obj["parts"]
                        ],
                    }
                    for obj in model["objects"]
                ]
            ),
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    request = SliceRequest(
        input_mesh=assembly,
        output_3mf=slice_dir / f"{brief.slug}.gcode.3mf",
        output_project_3mf=slice_dir / f"{brief.slug}.3mf",
        machine=profiles / "machine.json",
        process=profiles / "process.json",
        filaments=tuple(filaments),
        datadir=job_dir / "studio-datadir",
        output_dir=slice_dir,
        orient=False,
        assemble_list=assemble,
    )
    report["slice_plan"] = slice_plan(request)

    if not execute:
        notes = with_clean(notes)
        report.update(status="dry_run", advice=[n.as_dict() for n in notes], severity=worst_severity(notes))
        return report

    slice_result = run_slice(request)
    project = Path(slice_result["output_3mf"])
    inspection = inspect_project(project)
    # Per plate, slice_info has every filament; the gcode header weighs only the first.
    by_plate = {p["index"]: p for p in inspection["slice_info_parsed"].get("plates", [])}
    used: dict[int, float] = {}
    for info in by_plate.values():
        for f in info["filaments"]:
            used[int(f["id"])] = round(used.get(int(f["id"]), 0.0) + float(f.get("used_g", 0)), 2)
    plates = []
    for plate in inspection["plates"]:
        index = plate["index"]
        metrics_ = analyze_slice(project, index)
        on_plate = [metrics[obj["name"]] for obj in model["objects"] if obj["plate"] == index]
        notes.extend(slice_advice(metrics_, any(corroborates_layer_jump(m) for m in on_plate)))
        png = extract_plate_png(project, slice_dir / f"{brief.slug}_plate_{index}.png", plate=index)
        plates.append(
            {
                "plate": index,
                "objects": [obj["name"] for obj in model["objects"] if obj["plate"] == index],
                "png": str(png) if png else None,
                **{k: v for k, v in metrics_.items() if k not in ("layers", "plate")},
            }
        )
        if by_plate.get(index, {}).get("weight_g") is not None:
            plates[-1]["filament_g"] = by_plate[index]["weight_g"]
    for filament in report["filaments"]:
        filament["used_g"] = used.get(filament["filament"])
    notes = with_clean(notes)
    severity = worst_severity(notes)
    minutes = [p["estimated_minutes"] for p in plates if p["estimated_minutes"] is not None]
    grams = [p["filament_g"] for p in plates if p["filament_g"] is not None]

    report.update(
        {
            "status": "needs_work" if severity == "error" else "ready_for_review",
            "slice_result": {
                "returncode": slice_result["returncode"],
                "output_3mf": slice_result["output_3mf"],
                "output_project_3mf": slice_result.get("output_project_3mf"),
                "output_size_bytes": slice_result["output_size_bytes"],
            },
            "inspection": {"plates": inspection["plates"], "path": inspection["path"]},
            "plates": plates,
            "plate_png": plates[0]["png"] if plates else None,
            "slice_metrics": {
                "estimated_minutes": round(sum(minutes), 1) if minutes else None,
                "filament_g": round(sum(grams), 2) if grams else None,
                "plates": len(plates),
            },
            "advice": [n.as_dict() for n in notes],
            "severity": severity,
            "open_in_studio": slice_result.get("output_project_3mf"),
            "next_step": (
                "Fix the errors in the model script and run again."
                if severity == "error"
                else f"Open the .3mf in Bambu Studio (set up for the {printer.name}), check each plate, "
                "and print from there."
            ),
        }
    )
    viewer = build_viewer(job_dir, report)
    report["viewer"] = str(viewer) if viewer else None
    _append_history(
        job_dir,
        {
            "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "printer": printer.key,
            "severity": severity,
            "size_mm": [round(v, 2) for v in model["size_mm"]],
            "parts": {name: part["color"] for name, part in parts.items()},
            "estimated_minutes": report["slice_metrics"]["estimated_minutes"],
            "filament_g": report["slice_metrics"]["filament_g"],
            "codes": [n.code for n in notes],
            "fits": {f"{f['a']}/{f['b']}": f["clearance_mm"] for f in model["fits"]},
            "likeness": {r["view"]: r["iou"] for r in likeness},
        },
    )
    return report
