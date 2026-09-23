"""Idea to a reviewable Bambu Studio project.

    prompt or image  ->  Blender model script  ->  mesh  ->  DFM + printability
                              ^                                    |
                              +---- advice tells you what to fix ---+
                                              |
                                        slice -> .3mf -> review in Studio

The loop stops at review. Nothing is ever sent to the printer from here.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from text3d.blender import BlenderError, run_model_script, scaffold_script
from text3d.brief import PrintBrief, parse_brief
from text3d.dfm import require_printable
from text3d.paths import default_jobs_dir, default_profiles_dir
from text3d.printability import advise, analyze_mesh, analyze_slice, worst_severity
from text3d.project import extract_plate_png, inspect_project
from text3d.studio import SliceRequest, run_slice, slice_plan

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
    profiles = profiles_dir or default_profiles_dir()
    for required in ("machine.json", "process.json", "filament.json"):
        if not (profiles / required).is_file():
            raise PipelineError(
                f"Pinned profile missing: {profiles / required}. Run: python -m text3d flatten"
            )

    report: dict[str, Any] = {"brief": brief.as_dict(), "job_dir": str(job_dir)}
    (job_dir / "brief.json").write_text(json.dumps(brief.as_dict(), indent=2) + "\n", encoding="utf-8")

    # 1-2. Model.
    if mesh is not None:
        mesh_path = mesh.expanduser().resolve()
        if not mesh_path.is_file():
            raise PipelineError(f"Mesh does not exist: {mesh_path}")
        report["model"] = {"mesh": str(mesh_path), "source": "supplied"}
    else:
        model_script = _resolve_script(brief, job_dir, script)
        mesh_path = job_dir / "mesh" / f"{brief.slug}.stl"
        try:
            report["model"] = run_model_script(
                model_script, mesh_path, params=_model_params(brief)
            )
        except BlenderError as exc:
            raise PipelineError(str(exc)) from exc
        report["model"]["source"] = "blender"

    # 3. Is it even a solid?
    dfm = require_printable(mesh_path)
    report["dfm"] = dfm.as_dict()
    mesh_metrics = analyze_mesh(mesh_path)
    report["mesh_metrics"] = mesh_metrics

    request = SliceRequest(
        input_mesh=mesh_path,
        output_3mf=job_dir / "slice" / f"{brief.slug}.gcode.3mf",
        output_project_3mf=job_dir / "slice" / f"{brief.slug}.3mf",
        machine=profiles / "machine.json",
        process=profiles / "process.json",
        filaments=(profiles / "filament.json",),
        datadir=job_dir / "studio-datadir",
        output_dir=job_dir / "slice",
        # Auto-orient would silently rotate the part and invalidate every
        # overhang note below. Orientation is the model script's business.
        orient=False,
    )
    report["slice_plan"] = slice_plan(request)

    if not execute:
        report["status"] = "dry_run"
        report["advice"] = [note.as_dict() for note in advise(mesh_metrics)]
        return report

    # 4. Slice, then read back what the slicer actually did.
    slice_result = run_slice(request)
    project = Path(slice_result["output_3mf"])
    inspection = inspect_project(project)
    png = extract_plate_png(project, job_dir / "slice" / f"{brief.slug}_plate.png")
    slice_metrics = analyze_slice(project)
    notes = advise(mesh_metrics, slice_metrics)
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
                else "Open the .3mf in Bambu Studio, check the preview, and print it from there."
            ),
        }
    )
    (job_dir / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    _append_history(
        job_dir,
        {
            "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "severity": severity,
            "size_mm": [round(v, 2) for v in mesh_metrics["size_mm"]],
            "triangles": mesh_metrics["triangles"],
            "overhang_fraction": round(mesh_metrics["overhang_fraction"], 4),
            "estimated_minutes": slice_metrics["estimated_minutes"],
            "filament_g": slice_metrics["filament_g"],
            "codes": [note.code for note in notes],
        },
    )
    return report
