"""text3d command line."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from text3d.blender import BlenderError, find_blender, run_model_script
from text3d.brief import parse_brief
from text3d.flatten import flatten_x2d
from text3d.paths import default_profiles_dir
from text3d.pipeline import _model_params, run_job
from text3d.printability import advise, analyze_mesh, analyze_slice
from text3d.project import inspect_project
from text3d.studio import SliceRequest, find_studio, slice_plan


def _print_json(payload: object) -> None:
    json.dump(payload, sys.stdout, indent=2)
    sys.stdout.write("\n")


def cmd_make(args: argparse.Namespace) -> int:
    report = run_job(
        args.prompt,
        script=Path(args.script) if args.script else None,
        mesh=Path(args.mesh) if args.mesh else None,
        color=args.color,
        execute=not args.dry_run,
    )
    _print_json(report)
    return 1 if report.get("severity") == "error" else 0


def cmd_model(args: argparse.Namespace) -> int:
    params = json.loads(args.params) if args.params else {}
    if args.prompt:
        params = {**_model_params(parse_brief(args.prompt)), **params}
    _print_json(run_model_script(Path(args.script), Path(args.out), params=params))
    return 0


def cmd_slice(args: argparse.Namespace) -> int:
    mesh = Path(args.mesh)
    report = run_job(args.prompt or f"slice {mesh.name}", mesh=mesh, execute=not args.dry_run)
    _print_json(report)
    return 1 if report.get("severity") == "error" else 0


def cmd_check(args: argparse.Namespace) -> int:
    mesh_metrics = analyze_mesh(Path(args.mesh))
    slice_metrics = analyze_slice(Path(args.project)) if args.project else None
    payload = {
        "mesh_metrics": mesh_metrics,
        "advice": [note.as_dict() for note in advise(mesh_metrics, slice_metrics)],
    }
    if slice_metrics:
        payload["slice_metrics"] = {k: v for k, v in slice_metrics.items() if k != "layers"}
        payload["layers"] = slice_metrics["layers"]
    _print_json(payload)
    return 0


def cmd_inspect(args: argparse.Namespace) -> int:
    _print_json(inspect_project(Path(args.project)))
    return 0


def cmd_flatten(args: argparse.Namespace) -> int:
    _print_json(flatten_x2d(output_dir=Path(args.output) if args.output else default_profiles_dir()))
    return 0


def cmd_status(_args: argparse.Namespace) -> int:
    profiles = default_profiles_dir()
    try:
        blender = str(find_blender())
    except BlenderError as exc:
        blender = f"missing: {exc}"
    _print_json(
        {
            "blender": blender,
            "studio": str(find_studio()),
            "profiles": str(profiles),
            "profiles_present": all(
                (profiles / name).is_file()
                for name in ("machine.json", "process.json", "filament.json")
            ),
            "stops_at": "review in Bambu Studio",
        }
    )
    return 0


def cmd_plan(args: argparse.Namespace) -> int:
    profiles = default_profiles_dir()
    output = Path(args.output)
    request = SliceRequest(
        input_mesh=Path(args.mesh),
        output_3mf=output,
        machine=profiles / "machine.json",
        process=profiles / "process.json",
        filaments=(profiles / "filament.json",),
        datadir=output.parent / "studio-datadir",
        output_dir=output.parent,
    )
    _print_json(slice_plan(request))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="text3d", description="Idea to a reviewable Bambu Studio project for an X2D"
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    make_cmd = sub.add_parser("make", help="Model in Blender, DFM, slice, and report printability")
    make_cmd.add_argument("prompt")
    make_cmd.add_argument("--script", help="Blender model script (default: jobs/<slug>/model.py)")
    make_cmd.add_argument("--mesh", help="Skip modelling and use this STL")
    make_cmd.add_argument("--color")
    make_cmd.add_argument("--dry-run", action="store_true")
    make_cmd.set_defaults(func=cmd_make)

    model_cmd = sub.add_parser("model", help="Run a Blender model script to an STL")
    model_cmd.add_argument("script")
    model_cmd.add_argument("out")
    model_cmd.add_argument("--prompt", default="")
    model_cmd.add_argument("--params", help="JSON passed to the script as `params`")
    model_cmd.set_defaults(func=cmd_model)

    slice_cmd = sub.add_parser("slice", help="Slice an existing mesh")
    slice_cmd.add_argument("mesh")
    slice_cmd.add_argument("--prompt", default="")
    slice_cmd.add_argument("--dry-run", action="store_true")
    slice_cmd.set_defaults(func=cmd_slice)

    check_cmd = sub.add_parser("check", help="Printability of a mesh, and of its slice")
    check_cmd.add_argument("mesh")
    check_cmd.add_argument("--project", help="Sliced .gcode.3mf to read layer data from")
    check_cmd.set_defaults(func=cmd_check)

    inspect_cmd = sub.add_parser("inspect", help="Read a sliced .gcode.3mf")
    inspect_cmd.add_argument("project")
    inspect_cmd.set_defaults(func=cmd_inspect)

    flatten_cmd = sub.add_parser("flatten", help="Rebuild pinned X2D Studio profiles")
    flatten_cmd.add_argument("--output")
    flatten_cmd.set_defaults(func=cmd_flatten)

    status_cmd = sub.add_parser("status", help="Show Blender, slicer and profile paths")
    status_cmd.set_defaults(func=cmd_status)

    plan_cmd = sub.add_parser("plan", help="Print the Studio command without running it")
    plan_cmd.add_argument("mesh")
    plan_cmd.add_argument("output")
    plan_cmd.set_defaults(func=cmd_plan)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except Exception as exc:  # noqa: BLE001
        print(f"error: {exc}", file=sys.stderr)
        return 1
