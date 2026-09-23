#!/usr/bin/env python3
"""Go/no-go: slice a 20 mm cube with flattened X2D 0.4 presets."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from text3d.flatten import flatten_x2d  # noqa: E402
from text3d.project import extract_plate_png, inspect_project  # noqa: E402
from text3d.studio import SliceRequest, run_slice, slice_plan  # noqa: E402


def studio_conf_mtime() -> float | None:
    path = Path.home() / "Library/Application Support/BambuStudio/BambuStudio.conf"
    return path.stat().st_mtime if path.is_file() else None


def main() -> int:
    profiles = ROOT / "profiles" / "x2d-0.4"
    fixtures = ROOT / "tests" / "fixtures"
    scratch = ROOT / "scratch" / "spike"
    mesh = fixtures / "cube_20mm.stl"
    flattened = flatten_x2d(output_dir=profiles)
    print(json.dumps({"flatten": flattened["provenance"]}, indent=2))

    request = SliceRequest(
        input_mesh=mesh,
        output_3mf=scratch / "slice" / "cube_20mm.gcode.3mf",
        machine=Path(flattened["machine_path"]),
        process=Path(flattened["process_path"]),
        filaments=tuple(Path(path) for path in flattened["filament_paths"]),
        datadir=scratch / "datadir",
        output_dir=scratch / "slice",
    )
    plan = slice_plan(request)
    (scratch / "command.json").parent.mkdir(parents=True, exist_ok=True)
    (scratch / "command.json").write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
    print("command:", " ".join(plan["command"]))

    before = studio_conf_mtime()
    started = time.time()
    result = run_slice(request, timeout_s=240)
    elapsed = time.time() - started
    after = studio_conf_mtime()
    inspection = inspect_project(Path(result["output_3mf"]))
    png = extract_plate_png(
        Path(result["output_3mf"]), fixtures / "cube_20mm_plate.png"
    )
    golden = fixtures / "cube_20mm.gcode.3mf"
    golden.write_bytes(Path(result["output_3mf"]).read_bytes())
    report = {
        "elapsed_s": round(elapsed, 2),
        "returncode": result["returncode"],
        "output_size_bytes": result["output_size_bytes"],
        "studio_conf_mtime_changed": before != after,
        "plates": inspection["plates"],
        "png": str(png) if png else None,
        "golden": str(golden),
    }
    (scratch / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001
        print(f"SPIKE_FAILED: {exc}", file=sys.stderr)
        raise
