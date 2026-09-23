"""Bambu Studio CLI wrapper. Isolated --datadir, dry-run first."""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from text3d.repair_3mf import repair_bambu_3mf


class StudioError(RuntimeError):
    """Raised for Studio discovery or slice failures."""


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


def find_studio(search_path: str | None = None) -> Path:
    env = os.environ.get("BAMBU_STUDIO_BIN", "").strip()
    candidates: list[str] = []
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
    for candidate in candidates:
        path = Path(candidate).expanduser()
        if path.is_file():
            return path.resolve()
    raise StudioError("Bambu Studio CLI not found. Install Bambu Studio or set BAMBU_STUDIO_BIN.")


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


def project_3mf_path(request: SliceRequest) -> Path:
    if request.output_project_3mf is not None:
        return request.output_project_3mf
    return request.output_dir / (request.output_3mf.name.replace(".gcode.3mf", ".3mf"))


def build_project_command(studio: Path, request: SliceRequest) -> list[str]:
    """Export an unsliced Studio project. File > Open this in the Prepare tab."""
    command = _common_prefix(studio, request)
    command.extend(["--export-3mf", project_3mf_path(request).name])
    command.append(str(request.input_mesh.resolve()))
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
    command.append(str((input_path or request.input_mesh).resolve()))
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
    }


def slice_plan(request: SliceRequest, *, studio: Path | None = None) -> dict[str, Any]:
    binary = studio or find_studio()
    return {
        "dry_run": True,
        "studio": str(binary),
        "project_command": build_project_command(binary, request),
        "command": build_slice_command(binary, request),
        "request": _request_dict(request),
        "open_in_studio": str(project_3mf_path(request)),
        "note": "Open the .3mf (not .gcode.3mf) in Bambu Studio File > Open. "
        ".gcode.3mf is a sliced print job and the Prepare tab reports no geometry.",
    }


def _run(command: list[str], timeout_s: float) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=False, capture_output=True, text=True, timeout=timeout_s)


def _require_file(path: Path, result: subprocess.CompletedProcess[str], label: str) -> None:
    if result.returncode != 0:
        raise StudioError(
            f"Bambu Studio {label} failed with exit {result.returncode}.\n"
            f"stderr tail:\n{(result.stderr or result.stdout)[-4000:]}"
        )
    if not path.is_file():
        raise StudioError(f"Studio {label} exited 0 but did not write {path}")


def run_slice(
    request: SliceRequest,
    *,
    studio: Path | None = None,
    timeout_s: float = 180,
) -> dict[str, Any]:
    plan = slice_plan(request, studio=studio)
    plan["dry_run"] = False
    request.datadir.mkdir(parents=True, exist_ok=True)
    request.output_dir.mkdir(parents=True, exist_ok=True)
    request.output_3mf.parent.mkdir(parents=True, exist_ok=True)
    project = project_3mf_path(request)
    project.parent.mkdir(parents=True, exist_ok=True)

    binary = Path(plan["studio"])
    project_result = _run(build_project_command(binary, request), timeout_s)
    exported_project = request.output_dir / project.name
    _require_file(exported_project, project_result, "project export")
    if exported_project.resolve() != project.resolve():
        project.write_bytes(exported_project.read_bytes())
    repair_bambu_3mf(project)

    # Slice the STL, not the project 3mf. --load-settings on 3mf input has
    # segfaulted Studio CLI (bambulab/BambuStudio#10402).
    slice_cmd = build_slice_command(binary, request, input_path=request.input_mesh)
    plan["command"] = slice_cmd
    slice_result = _run(slice_cmd, timeout_s)
    exported = request.output_dir / request.output_3mf.name
    _require_file(exported, slice_result, "slice")
    if exported.resolve() != request.output_3mf.resolve():
        request.output_3mf.write_bytes(exported.read_bytes())
    repair_bambu_3mf(request.output_3mf)

    plan["returncode"] = slice_result.returncode
    plan["stdout"] = slice_result.stdout
    plan["stderr"] = slice_result.stderr
    plan["output_3mf"] = str(request.output_3mf)
    plan["output_project_3mf"] = str(project)
    plan["output_size_bytes"] = request.output_3mf.stat().st_size
    plan["project_size_bytes"] = project.stat().st_size
    return plan
