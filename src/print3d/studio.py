"""Slicer CLI wrapper. Isolated --datadir, dry-run first.

The command line of ElegooSlicer (an OrcaSlicer fork) is flag-compatible with
Bambu Studio's, so one wrapper drives both. The request names which one, from
the printer's presets: each slicer only reads its own vendor's profiles, so
there is no falling back from one to the other.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Iterable

from print3d.printers import SLICERS
from print3d.project import inspect_project
from print3d.repair_3mf import repair_bambu_3mf
from print3d.windows import elegoo_locations, studio_locations


class StudioError(RuntimeError):
    """Raised for slicer discovery or slice failures."""


@dataclass(frozen=True)
class SliceRequest:
    input_mesh: Path
    output_3mf: Path
    machine: Path
    process: Path
    filaments: tuple[Path, ...]
    datadir: Path
    output_dir: Path
    output_project_3mf: Path | None = None
    slice_plate: int = 0
    orient: bool = True
    arrange: bool = True
    ensure_on_bed: bool = True
    bed_type: str = "Textured PEI Plate"
    debug_level: int = 2
    # Several parts: the slicer reads the meshes, filaments and plates from this
    # file instead of from input_mesh.
    assemble_list: Path | None = None
    # Which slicer reads the presets: a key of printers.SLICERS.
    slicer: str = "bambu"


def _first_file(candidates: Iterable[str | Path]) -> Path | None:
    for candidate in candidates:
        path = Path(candidate).expanduser()
        if path.is_file():
            return path.resolve()
    return None


def find_studio(search_path: str | None = None) -> Path:
    env = os.environ.get("BAMBU_STUDIO_BIN", "").strip()
    candidates: list[str | Path] = []
    if env:
        candidates.append(env)
    which = shutil.which("bambu-studio", path=search_path)
    if which:
        candidates.append(which)
    candidates.extend(
        [
            "/opt/homebrew/bin/bambu-studio",
            "/Applications/BambuStudio.app/Contents/MacOS/BambuStudio",
        ]
    )
    found = _first_file(candidates) or _first_file(studio_locations())
    if found:
        return found
    raise StudioError("Bambu Studio CLI not found. Install Bambu Studio or set BAMBU_STUDIO_BIN.")


def find_elegoo(search_path: str | None = None) -> Path:
    env = os.environ.get("ELEGOO_SLICER_BIN", "").strip()
    candidates: list[str | Path] = []
    if env:
        candidates.append(env)
    for name in ("elegoo-slicer", "elegoo-slicer.exe"):
        if which := shutil.which(name, path=search_path):
            candidates.append(which)
            break
    candidates.extend(
        [
            "/Applications/ElegooSlicer.app/Contents/MacOS/ElegooSlicer",
            "/opt/ElegooSlicer/elegoo-slicer",
        ]
    )
    found = _first_file(candidates) or _first_file(elegoo_locations())
    if found:
        return found
    raise StudioError(
        "ElegooSlicer CLI not found. Install ElegooSlicer or set ELEGOO_SLICER_BIN."
    )


def find_slicer(slicer: str = "bambu", search_path: str | None = None) -> Path:
    """The CLI of `slicer` (a key of printers.SLICERS)."""
    if slicer == "elegoo":
        return find_elegoo(search_path)
    if slicer == "bambu":
        return find_studio(search_path)
    raise StudioError(f"Unknown slicer {slicer!r}; print3d drives {', '.join(SLICERS)}.")


def _common_prefix(studio: Path, request: SliceRequest) -> list[str]:
    filaments = ";".join(str(path.resolve()) for path in request.filaments)
    settings = f"{request.machine.resolve()};{request.process.resolve()}"
    command = [
        str(studio),
        "--datadir",
        str(request.datadir.resolve()),
        "--debug",
        str(request.debug_level),
    ]
    # Studio refuses an assemble list combined with transforms; each plate in
    # the list says whether to arrange it.
    if request.assemble_list is None:
        if request.orient:
            command.extend(["--orient", "1"])
        if request.arrange:
            command.extend(["--arrange", "1"])
        if request.ensure_on_bed:
            command.append("--ensure-on-bed")
    command.extend(
        [
            "--load-settings",
            settings,
            "--load-filaments",
            filaments,
            "--curr-bed-type",
            request.bed_type,
            "--outputdir",
            str(request.output_dir.resolve()),
        ]
    )
    return command


def _models(request: SliceRequest, input_path: Path | None = None) -> list[str]:
    if request.assemble_list is not None:
        return ["--load-assemble-list", str(request.assemble_list.resolve())]
    return [str((input_path or request.input_mesh).resolve())]


def assemble_list(objects: list[dict[str, Any]]) -> dict[str, Any]:
    """Studio's assemble list: one entry per part, fused by group, placed on numbered plates.

    Each object is {"plate": n, "parts": [{"stl", "filament", "settings"}]}; a
    part's filament is 1-based. Parts of one object share an assemble_index, so
    Studio prints them as one object with a filament per part.
    """
    plates: dict[int, list[dict[str, Any]]] = {}
    for index, obj in enumerate(objects, start=1):
        fused = len(obj["parts"]) > 1
        for part in obj["parts"]:
            entry = {
                "path": str(Path(part["stl"]).resolve()),
                "count": 1,
                "filaments": [part["filament"]],
                "assemble_index": [index if fused else 0],
            }
            if part.get("settings"):
                entry["print_params"] = part["settings"]
            plates.setdefault(obj["plate"], []).append(entry)
    return {
        "plates": [
            {"plate_name": f"plate {n}", "need_arrange": True, "objects": plates[n]}
            for n in sorted(plates)
        ]
    }


def project_3mf_path(request: SliceRequest) -> Path:
    if request.output_project_3mf is not None:
        return request.output_project_3mf
    return request.output_dir / (request.output_3mf.name.replace(".gcode.3mf", ".3mf"))


def build_project_command(studio: Path, request: SliceRequest) -> list[str]:
    """Export an unsliced Studio project. File > Open this in the Prepare tab."""
    command = _common_prefix(studio, request)
    command.extend(["--export-3mf", project_3mf_path(request).name])
    command.extend(_models(request))
    return command


def build_slice_command(studio: Path, request: SliceRequest, *, input_path: Path | None = None) -> list[str]:
    command = _common_prefix(studio, request)
    command.extend(
        [
            "--slice",
            str(request.slice_plate),
            "--export-3mf",
            request.output_3mf.name,
        ]
    )
    command.extend(_models(request, input_path))
    return command


def _request_dict(request: SliceRequest) -> dict[str, Any]:
    return {
        "input_mesh": str(request.input_mesh),
        "output_3mf": str(request.output_3mf),
        "output_project_3mf": str(project_3mf_path(request)),
        "machine": str(request.machine),
        "process": str(request.process),
        "filaments": [str(path) for path in request.filaments],
        "datadir": str(request.datadir),
        "output_dir": str(request.output_dir),
        "slice_plate": request.slice_plate,
        "bed_type": request.bed_type,
        "assemble_list": str(request.assemble_list) if request.assemble_list else None,
    }


def slice_plan(request: SliceRequest, *, studio: Path | None = None) -> dict[str, Any]:
    binary = studio
    if binary is None:
        # A plan is only a preview; name the binary even when it is not installed.
        try:
            binary = find_slicer(request.slicer)
        except StudioError:
            binary = Path("elegoo-slicer" if request.slicer == "elegoo" else "bambu-studio")
    name = SLICERS[request.slicer]
    return {
        "dry_run": True,
        "studio": str(binary),
        "slicer": name,
        "project_command": build_project_command(binary, request),
        "command": build_slice_command(binary, request),
        "request": _request_dict(request),
        "open_in_studio": str(project_3mf_path(request)),
        "note": f"Open the .3mf (not .gcode.3mf) in {name} File > Open. "
        ".gcode.3mf is a sliced print job and the Prepare tab reports no geometry.",
    }


def _run(command: list[str], timeout_s: float) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=False, capture_output=True, text=True, timeout=timeout_s)


def _require_file(path: Path, result: subprocess.CompletedProcess[str], label: str) -> None:
    if result.returncode != 0:
        raise StudioError(
            f"Slicer {label} failed with exit {result.returncode}.\n"
            f"stderr tail:\n{(result.stderr or result.stdout)[-4000:]}"
        )
    if not path.is_file():
        raise StudioError(f"Slicer {label} exited 0 but did not write {path}")


def _publish(source: Path, destination: Path) -> None:
    """Replace one final file without exposing a partially copied archive."""
    with tempfile.NamedTemporaryFile(dir=destination.parent, prefix=".print3d-", delete=False) as stream:
        pending = Path(stream.name)
    try:
        shutil.copyfile(source, pending)
        pending.replace(destination)
    finally:
        pending.unlink(missing_ok=True)


def run_slice(
    request: SliceRequest,
    *,
    studio: Path | None = None,
    timeout_s: float = 180,
) -> dict[str, Any]:
    binary = studio or find_slicer(request.slicer)
    plan = slice_plan(request, studio=binary)
    plan["dry_run"] = False
    request.datadir.mkdir(parents=True, exist_ok=True)
    request.output_dir.mkdir(parents=True, exist_ok=True)
    request.output_3mf.parent.mkdir(parents=True, exist_ok=True)
    project = project_3mf_path(request)
    project.parent.mkdir(parents=True, exist_ok=True)
    if project.resolve() == request.output_3mf.resolve():
        raise StudioError("Editable and sliced projects must have different output paths.")

    # Existing exports are never evidence of this invocation's success. Keep
    # both stages private until both new archives have passed validation.
    with tempfile.TemporaryDirectory(dir=request.output_dir, prefix=".print3d-") as temporary:
        staging = Path(temporary)
        project_dir, slice_dir = staging / "project", staging / "slice"
        project_dir.mkdir()
        slice_dir.mkdir()
        exported_project = project_dir / project.name
        exported = slice_dir / request.output_3mf.name
        project_request = replace(request, output_dir=project_dir, output_project_3mf=exported_project)
        slice_request = replace(request, output_dir=slice_dir, output_3mf=exported)

        project_cmd = build_project_command(binary, project_request)
        plan["project_command"] = project_cmd
        project_result = _run(project_cmd, timeout_s)
        _require_file(exported_project, project_result, "project export")
        repair_bambu_3mf(exported_project)

        # Slice the STL, not the project 3mf. --load-settings on 3mf input has
        # segfaulted Studio CLI (bambulab/BambuStudio#10402).
        slice_cmd = build_slice_command(binary, slice_request, input_path=request.input_mesh)
        plan["command"] = slice_cmd
        slice_result = _run(slice_cmd, timeout_s)
        _require_file(exported, slice_result, "slice")
        repair_bambu_3mf(exported)
        inspection = inspect_project(exported)
        if any(plate["md5_ok"] is False for plate in inspection["plates"]):
            raise StudioError("Slicer output has an invalid G-code checksum.")

        # Each file is replaced atomically. The pair is not a filesystem
        # transaction; the pipeline marks publication failures as failed runs.
        _publish(exported_project, project)
        _publish(exported, request.output_3mf)

    plan["returncode"] = slice_result.returncode
    plan["stdout"] = slice_result.stdout
    plan["stderr"] = slice_result.stderr
    plan["output_3mf"] = str(request.output_3mf)
    plan["output_project_3mf"] = str(project)
    plan["output_size_bytes"] = request.output_3mf.stat().st_size
    plan["project_size_bytes"] = project.stat().st_size
    return plan
