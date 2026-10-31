"""print3d command line."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from print3d.blender import run_model_script
from print3d.brief import parse_brief
from print3d import printers
from print3d import setup as installer
from print3d import spec as specs
from print3d.dfm import check_mesh
from print3d.doctor import dependency_status, discover_dependencies
from print3d.fits import KINDS, load_fits, save_fits
from print3d.flatten import flatten_printer
from print3d.handoff import build_handoff
from print3d.likeness import check_job
from print3d.paths import coupon_script, default_profiles_dir, job_path, safe_name
from print3d.photo import grid, meet
from print3d.pipeline import _model_params, run_job
from print3d.printability import advise, analyze_mesh, analyze_slice, dfm_advice, worst_severity
from print3d.project import inspect_project
from print3d.studio import SliceRequest, slice_plan
from print3d.viewer import build_viewer


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
    params = {"fit": load_fits()["clearance_mm"], **(json.loads(args.params) if args.params else {})}
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
    dfm = check_mesh(Path(args.mesh))
    mesh_metrics = analyze_mesh(Path(args.mesh))
    slice_metrics = analyze_slice(Path(args.project)) if args.project else None
    notes = advise(mesh_metrics, slice_metrics, extra=dfm_advice(dfm))
    payload = {
        "dfm": dfm.as_dict(),
        "mesh_metrics": mesh_metrics,
        "advice": [note.as_dict() for note in notes],
        "severity": worst_severity(notes),
    }
    if slice_metrics:
        payload["slice_metrics"] = {k: v for k, v in slice_metrics.items() if k != "layers"}
        payload["layers"] = slice_metrics["layers"]
    _print_json(payload)
    return 1 if payload["severity"] == "error" else 0


def cmd_inspect(args: argparse.Namespace) -> int:
    _print_json(inspect_project(Path(args.project)))
    return 0


def cmd_grid(args: argparse.Namespace) -> int:
    photo = Path(args.photo)
    crop = tuple(float(v) for v in args.crop.split(",")) if args.crop else None
    if crop is not None and len(crop) != 4:
        raise ValueError("--crop is x0,y0,x1,y1")
    _print_json(grid(photo, Path(args.out) if args.out else None, crop=crop, step=args.step))
    return 0


def cmd_measure(args: argparse.Namespace) -> int:
    job_dir = job_path(args.slug)
    spec = specs.load_spec(job_dir)
    if args.action == "scale":
        result = specs.set_scale(
            spec, args.view, Path(args.photo), args.ref, specs.parse_points(args.points, count=2),
            look=args.look, up=args.up,
        )
    elif args.action == "rectify":
        width, height = (float(v) for v in args.size.split(","))
        result = specs.rectify_view(
            spec, args.view, Path(args.photo), specs.parse_points(args.corners, count=4),
            (width, height), job_dir / "views" / f"{safe_name(args.view, 'view')}.png", look=args.look, up=args.up,
        )
    elif args.action == "dim" and args.source == "listing":
        result = specs.listing_dim(spec, args.name, args.mm, args.quote, args.tolerance)
    elif args.action == "dim" and args.mm is not None:
        result = specs.given_dim(spec, args.name, args.mm, args.source, args.why)
    elif args.action == "dim":
        if not (args.view and args.points):
            raise ValueError("Give --view and --points to measure, or --mm and --source to type it in.")
        result = specs.measure_dim(spec, args.name, args.view, specs.parse_points(args.points, count=2))
    elif args.action == "outline":
        points = specs.parse_points(args.points) if args.points else None
        result = specs.set_outline(spec, args.view, points=points, mask=Path(args.mask) if args.mask else None)
    elif args.action == "unknown":
        result = specs.add_unknown(spec, args.text)
    elif args.action == "listing":
        result = specs.set_listing(spec, args.url, args.name, args.price, args.currency)
    else:
        result = spec
    if args.action != "show":
        result = {"saved": str(specs.save_spec(job_dir, spec)), "entry": result}
    _print_json(result)
    return 0


def cmd_meet(args: argparse.Namespace) -> int:
    a1, a2 = specs.parse_points(args.edge_a, count=2)
    b1, b2 = specs.parse_points(args.edge_b, count=2)
    _print_json({"corner": list(meet(a1, a2, b1, b2))})
    return 0


def cmd_likeness(args: argparse.Namespace) -> int:
    results, notes = check_job(job_path(args.slug), Path(args.mesh) if args.mesh else None)
    _print_json({"likeness": results, "advice": [note.as_dict() for note in notes]})
    return 1 if any(note.severity == "error" for note in notes) else 0


def cmd_handoff(args: argparse.Namespace) -> int:
    _print_json(
        build_handoff(
            job_path(args.slug),
            cost_per_kg=args.cost_per_kg,
            currency=args.currency or "",
            render=not args.no_render,
        )
    )
    return 0


def cmd_view(args: argparse.Namespace) -> int:
    job_dir = job_path(args.slug)
    report_path = job_dir / "report.json"
    if not report_path.is_file():
        print(f"No report in {job_dir}; run make first.", file=sys.stderr)
        return 1
    viewer = build_viewer(job_dir, json.loads(report_path.read_text(encoding="utf-8")))
    if viewer is None:
        print(f"No mesh in {job_dir}; run make first.", file=sys.stderr)
        return 1
    print(viewer)
    return 0


def cmd_coupon(args: argparse.Namespace) -> int:
    report = run_job("fit coupon", script=coupon_script(), execute=not args.dry_run)
    report["next_step"] = (
        "Print the .3mf. Push the pin into each hole: the label is the clearance per side in "
        "hundredths of a mm. Then record what you felt, e.g. "
        "print3d fit set --press 0.05 --snug 0.10 --slide 0.20 --free 0.35"
    )
    _print_json(report)
    return 1 if report.get("severity") == "error" else 0


def cmd_fit(args: argparse.Namespace) -> int:
    if args.action == "set":
        values = {kind: getattr(args, kind) for kind in KINDS if getattr(args, kind) is not None}
        if not values:
            raise ValueError(f"Give at least one of --{', --'.join(KINDS)}.")
        _print_json(save_fits(values))
    else:
        _print_json(load_fits())
    return 0


def cmd_mcp(_args: argparse.Namespace) -> int:
    from print3d.mcp_server import main as serve

    serve()
    return 0


def cmd_setup(args: argparse.Namespace) -> int:
    changes = installer.plan(args.agent)
    if not changes:
        print(f"No agent found. Name one with --agent ({', '.join(installer.AGENTS)}).")
        return 1
    print("print3d setup will:")
    for change in changes:
        print("  " + change.describe())
    print(f"MCP server command: {' '.join(installer.server_command())}")
    if args.dry_run:
        return 0
    if not args.yes:
        if not sys.stdin.isatty():
            print("Run again with --yes to apply.")
            return 1
        if input("Apply? [y/N] ").strip().lower() not in ("y", "yes"):
            return 1
    failed = False
    for change in changes:
        try:
            change.apply()
            print(f"  done  {change.describe()}")
        except installer.SetupError as exc:
            failed = True
            print(f"  FAIL  {change.describe()}\n        {exc}")
    print("Restart your agent, then ask it to make something, e.g. "
          '"recreate the knob in ~/Desktop/knob.jpg".')
    return 1 if failed else 0


def cmd_flatten(args: argparse.Namespace) -> int:
    printer = args.printer or printers.active_printer()
    output = Path(args.output) if args.output else printers.PROFILES / printer
    _print_json(flatten_printer(printer, output_dir=output))
    return 0


def cmd_printer(args: argparse.Namespace) -> int:
    _print_json(printers.choose_printer(args.choose) if args.choose else printers.status())
    return 0


def cmd_doctor(_args: argparse.Namespace) -> int:
    report = dependency_status()
    _print_json(report)
    return 0 if report["ready"] else 1


def cmd_status(_args: argparse.Namespace) -> int:
    profiles = default_profiles_dir()
    dependencies = discover_dependencies()
    paths = {name: check["path"] or f"missing: {check['message']}"
             for name, check in dependencies.items()}
    choice = printers.printer_choice()
    _print_json(
        {
            **paths,
            "printer": {**choice, "summary": printers.printer_info(choice["printer"]).describe()},
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
        prog="print3d", description="Idea to a reviewable Bambu Studio project for a Bambu Lab X2D or P2S"
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

    grid_cmd = sub.add_parser("grid", help="Draw pixel coordinates over a photo to read points off")
    grid_cmd.add_argument("photo")
    grid_cmd.add_argument("--crop", help="x0,y0,x1,y1 in photo pixels: zoom into a feature")
    grid_cmd.add_argument("--step", type=int, help="Grid spacing in photo pixels")
    grid_cmd.add_argument("--out")
    grid_cmd.set_defaults(func=cmd_grid)

    measure_cmd = sub.add_parser("measure", help="Record dimensions and their evidence in jobs/<slug>/spec.json")
    measure_cmd.add_argument("slug")
    measure_cmd.set_defaults(func=cmd_measure)
    actions = measure_cmd.add_subparsers(dest="action", required=True)
    scale = actions.add_parser("scale", help="Scale a photo from a reference object in it")
    scale.add_argument("view", help="front, back, left, right, top, bottom, or your own name")
    scale.add_argument("photo")
    scale.add_argument("--ref", required=True,
                       help=f"mm, dim:<name> for a listed dimension, or one of: {', '.join(specs.REFERENCES_MM)}")
    scale.add_argument("--points", required=True, help="x1,y1,x2,y2: the two ends of the reference")
    scale.add_argument("--look", choices=specs.AXES, help="Axis the camera looks along, in the printed part")
    scale.add_argument("--up", choices=specs.AXES, help="Axis pointing up in the photo, in the printed part")
    rectify = actions.add_parser("rectify", help="Scale a view from a flat face of known size seen at an angle")
    rectify.add_argument("view")
    rectify.add_argument("photo")
    rectify.add_argument("--corners", required=True,
                         help="x,y x4: the face's top-left, top-right, bottom-right, bottom-left as it should appear")
    rectify.add_argument("--size", required=True, help="W,H of the face in mm")
    rectify.add_argument("--look", choices=specs.AXES)
    rectify.add_argument("--up", choices=specs.AXES)
    dim = actions.add_parser("dim", help="Measure a dimension off a photo, or type one in")
    dim.add_argument("name")
    dim.add_argument("--view")
    dim.add_argument("--points", help="x1,y1,x2,y2: the two ends of the feature")
    dim.add_argument("--mm", type=float)
    dim.add_argument("--source", choices=("user", "listing", "assumed"))
    dim.add_argument("--why")
    dim.add_argument("--quote", help="With --source listing: the listing's own words for this dimension")
    dim.add_argument("--tolerance", type=float,
                     help=f"With --source listing: how the listing rounds, in mm (default {specs.LISTING_TOLERANCE_MM})")
    outline = actions.add_parser("outline", help="The object's outline in a view, for the likeness check")
    outline.add_argument("view")
    outline.add_argument("--points", help="x,y,x,y,...: polygon around the object")
    outline.add_argument("--mask", help="PNG the size of the photo: object white or opaque")
    unknown = actions.add_parser("unknown", help="Record something no photo shows")
    unknown.add_argument("text")
    listing = actions.add_parser("listing", help="The product this part replaces: its shop page and price")
    listing.add_argument("url")
    listing.add_argument("--name")
    listing.add_argument("--price", type=float)
    listing.add_argument("--currency")
    actions.add_parser("show", help="Print the spec")

    meet_cmd = sub.add_parser("meet", help="Where two edges cross: a corner hidden behind something")
    meet_cmd.add_argument("edge_a", help="x1,y1,x2,y2: two points on one edge")
    meet_cmd.add_argument("edge_b", help="x1,y1,x2,y2: two points on the other edge")
    meet_cmd.set_defaults(func=cmd_meet)

    likeness_cmd = sub.add_parser("likeness", help="Compare the mesh with the photographed outlines")
    likeness_cmd.add_argument("slug")
    likeness_cmd.add_argument("--mesh", help="Default: jobs/<slug>/mesh/<slug>.stl")
    likeness_cmd.set_defaults(func=cmd_likeness)

    handoff_cmd = sub.add_parser(
        "handoff", help="A print sheet, renders, the project and an STL per part, in jobs/<slug>/handoff"
    )
    handoff_cmd.add_argument("slug")
    handoff_cmd.add_argument("--cost-per-kg", type=float,
                             help="Filament price per kg (default: the pinned profile's)")
    handoff_cmd.add_argument("--currency", help="Shown next to the cost, e.g. EUR")
    handoff_cmd.add_argument("--no-render", action="store_true", help="Skip the Blender renders")
    handoff_cmd.set_defaults(func=cmd_handoff)

    view_cmd = sub.add_parser("view", help="Rebuild jobs/<slug>/view.html, the model in 3D to rotate")
    view_cmd.add_argument("slug")
    view_cmd.set_defaults(func=cmd_view)

    coupon_cmd = sub.add_parser("coupon", help="Slice the fit coupon that calibrates press/snug/slide/free")
    coupon_cmd.add_argument("--dry-run", action="store_true")
    coupon_cmd.set_defaults(func=cmd_coupon)

    fit_cmd = sub.add_parser("fit", help="Show or record this printer's clearance per fit")
    fit_cmd.set_defaults(func=cmd_fit)
    fit_actions = fit_cmd.add_subparsers(dest="action", required=True)
    fit_actions.add_parser("show", help="The clearances model scripts get as params['fit']")
    fit_set = fit_actions.add_parser("set", help="Record what the fit coupon showed, per side in mm")
    for kind in KINDS:
        fit_set.add_argument(f"--{kind}", type=float)

    mcp_cmd = sub.add_parser("mcp", help="Run the MCP server on stdio")
    mcp_cmd.set_defaults(func=cmd_mcp)

    setup_cmd = sub.add_parser("setup", help="Register print3d with Claude Code, Codex, Cursor, Gemini and Hermes")
    setup_cmd.add_argument("--agent", action="append", choices=installer.AGENTS,
                           help="Only this agent (repeatable). Default: every one found")
    setup_cmd.add_argument("--dry-run", action="store_true", help="Show the plan and change nothing")
    setup_cmd.add_argument("--yes", action="store_true", help="Apply without asking")
    setup_cmd.set_defaults(func=cmd_setup)

    printer_cmd = sub.add_parser("printer", help="Show the Bambu Lab printer print3d slices for, or choose another")
    printer_cmd.add_argument("choose", nargs="?", help=f"One of: {', '.join(printers.available())}")
    printer_cmd.set_defaults(func=cmd_printer)

    flatten_cmd = sub.add_parser("flatten", help="Rebuild a printer's pinned presets from Bambu Studio's")
    flatten_cmd.add_argument("--printer", choices=sorted(printers.PRESETS),
                             help="Default: the printer print3d slices for")
    flatten_cmd.add_argument("--output", help="Default: src/print3d/profiles/<printer>")
    flatten_cmd.set_defaults(func=cmd_flatten)

    status_cmd = sub.add_parser("status", help="Show Blender, slicer and profile paths")
    status_cmd.set_defaults(func=cmd_status)

    doctor_cmd = sub.add_parser("doctor", help="Check Blender and Bambu Studio before starting a job")
    doctor_cmd.set_defaults(func=cmd_doctor)

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
