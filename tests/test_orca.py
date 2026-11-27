"""OrcaSlicer as a third slicer vendor: a configured printer, flattened before use."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from print3d import flatten as F
from print3d import printers as P
from print3d import studio as S


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _orca_tree(tmp_path: Path, *, with_strangers: bool = False) -> Path:
    """One OrcaSlicer profile tree, with file names deliberately unlike preset names."""
    root = tmp_path / "orca-profiles"
    _write_json(
        root / "OrcaSlicer" / "machine" / "machine-a.json",
        {
            "name": "Printer 0.4 nozzle",
            "printer_model": "Printer",
            "nozzle_diameter": ["0.4"],
            "printable_area": ["0x0", "220x0", "220x220", "0x220"],
            "printable_height": 250,
        },
    )
    # `inherits` names a preset, never a file: this resolves only when the
    # index is keyed by the `name` field.
    _write_json(
        root / "OrcaSlicer" / "process" / "base.json",
        {"name": "base-process", "layer_height": "0.2"},
    )
    _write_json(
        root / "OrcaSlicer" / "process" / "process-a.json",
        {
            "name": "0.20mm Standard @Printer",
            "inherits": "base-process",
            "enable_support": "0",
        },
    )
    _write_json(
        root / "OrcaSlicer" / "filament" / "filament-a.json",
        {
            "name": "Generic PLA @Printer",
            "filament_settings_id": ["Generic PLA"],
            "filament_cost": 20,
        },
    )
    if with_strangers:
        # Same preset names outside the selected vendor tree must be ignored.
        _write_json(
            root / "user" / "backup" / "process" / "process-a.json",
            {"name": "0.20mm Standard @Printer", "layer_height": "9.9"},
        )
        _write_json(
            root / "OtherVendor" / "filament" / "filament-a.json",
            {"name": "Generic PLA @Printer", "filament_cost": 999},
        )
    return root


def test_slicers_register_orca():
    assert P.SLICERS["orca"] == "OrcaSlicer"
    # Configured, not pinned: no committed folder, so it is not in PRESETS.
    assert "orca-0.4" in P.all_presets()
    assert "orca-0.4" not in P.PRESETS
    assert P.all_presets()["orca-0.4"].slicer == "orca"
    assert P.all_presets()["orca-0.4"].vendor == "OrcaSlicer"


def test_configured_orca_presets_are_read_on_demand(monkeypatch):
    for name in (P.ORCA_MACHINE_ENV, P.ORCA_PROCESS_ENV, P.ORCA_FILAMENT_ENV, P.ORCA_VENDOR_ENV):
        monkeypatch.delenv(name, raising=False)
    assert P.configured_presets()["orca-0.4"].machine == ""

    monkeypatch.setenv(P.ORCA_MACHINE_ENV, "  Printer 0.4 nozzle  ")
    monkeypatch.setenv(P.ORCA_VENDOR_ENV, "61dd0422-example")
    presets = P.configured_presets()["orca-0.4"]
    assert presets.machine == "Printer 0.4 nozzle"
    assert presets.vendor == "61dd0422-example"


def test_find_orca_env(tmp_path, monkeypatch):
    exe = tmp_path / "orca-slicer"
    exe.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setenv("ORCA_SLICER_PATH", str(exe))
    assert S.find_orca() == exe.resolve()
    assert S.find_slicer("orca") == exe.resolve()


def test_detect_profiles_root_orca(tmp_path, monkeypatch):
    root = _orca_tree(tmp_path)
    monkeypatch.setenv("ORCA_PROFILES_DIR", str(root))
    assert F.detect_profiles_root(vendor="OrcaSlicer") == root
    assert F.detect_profiles_root(vendor="OrcaSlicer", slicer="orca") == root


def test_build_name_index_orca(tmp_path):
    root = _orca_tree(tmp_path, with_strangers=True)
    index = F.build_name_index(root, "OrcaSlicer")
    assert set(index) == {
        "Printer 0.4 nozzle",
        "base-process",
        "0.20mm Standard @Printer",
        "Generic PLA @Printer",
    }
    assert "process-a" not in index  # not the file stem
    flat = F.flatten_by_name("0.20mm Standard @Printer", index)
    assert flat["layer_height"] == "0.2"  # inherited by name
    assert flat["enable_support"] == "0"


def test_build_name_index_rejects_conflicting_duplicate_in_one_tree(tmp_path):
    root = _orca_tree(tmp_path)
    _write_json(
        root / "OrcaSlicer" / "process" / "process-b.json",
        {"name": "0.20mm Standard @Printer", "layer_height": "0.4"},
    )
    with pytest.raises(F.FlattenError, match="Two process presets"):
        F.build_name_index(root, "OrcaSlicer")


def test_flatten_presets_orca(tmp_path, monkeypatch):
    root = _orca_tree(tmp_path)
    monkeypatch.setenv("ORCA_PROFILES_DIR", str(root))
    result = F.flatten_presets(
        output_dir=tmp_path / "flat",
        machine_leaf="Printer 0.4 nozzle",
        process_leaf="0.20mm Standard @Printer",
        filament_leaves=["Generic PLA @Printer"],
        vendor="OrcaSlicer",
        slicer="orca",
    )
    machine = json.loads(Path(result["machine_path"]).read_text(encoding="utf-8"))
    process = json.loads(Path(result["process_path"]).read_text(encoding="utf-8"))
    assert "inherits" not in machine  # OrcaSlicer CLI cannot resolve user inherits
    assert "inherits" not in process
    assert "inherits" not in json.loads(Path(result["filament_paths"][0]).read_text())
    assert machine["print3d_slicer"] == "orca"
    assert Path(result["process_path"]).is_file()
    assert Path(result["filament_paths"][0]).is_file()


def test_flatten_printer_orca_needs_names(tmp_path, monkeypatch):
    root = _orca_tree(tmp_path)
    monkeypatch.setenv("ORCA_PROFILES_DIR", str(root))
    with pytest.raises(F.FlattenError):
        F.flatten_printer("orca-0.4", output_dir=tmp_path / "out", profiles_root=root)


def test_configured_orca_flow_flatten_choose_slice(tmp_path, monkeypatch, cube_stl):
    """End to end: no pinned folder -> flatten -> choose -> slice for OrcaSlicer."""
    from print3d import paths as Paths
    from print3d.pipeline import run_job

    root = _orca_tree(tmp_path)
    profiles = tmp_path / "profiles"
    monkeypatch.setattr(P, "PROFILES", profiles)
    monkeypatch.setattr(Paths, "PROFILES", profiles)
    monkeypatch.setattr(P, "choice_path", lambda: tmp_path / "printer.json")
    monkeypatch.delenv(P.PRINTER_ENV, raising=False)
    monkeypatch.delenv(P.ORCA_VENDOR_ENV, raising=False)
    monkeypatch.setenv("ORCA_PROFILES_DIR", str(root))
    monkeypatch.setenv(P.ORCA_MACHINE_ENV, "Printer 0.4 nozzle")
    monkeypatch.setenv(P.ORCA_PROCESS_ENV, "0.20mm Standard @Printer")
    monkeypatch.setenv(P.ORCA_FILAMENT_ENV, "Generic PLA @Printer")

    assert "orca-0.4" not in P.available()
    with pytest.raises(ValueError, match="no presets"):
        P.choose_printer("orca-0.4")

    result = F.flatten_printer("orca-0.4", output_dir=profiles / "orca-0.4")
    assert "orca-0.4" in P.available()
    folder = profiles / "orca-0.4"
    machine = json.loads((folder / "machine.json").read_text())
    process = json.loads((folder / "process.json").read_text())
    filament = json.loads((folder / "filament.json").read_text())
    provenance = json.loads((folder / "provenance.json").read_text())
    assert machine["printer_settings_id"] == "Printer 0.4 nozzle"
    assert process["print_settings_id"] == "0.20mm Standard @Printer"
    assert filament["filament_settings_id"] == ["Generic PLA @Printer"]
    assert provenance["machine"] == "Printer 0.4 nozzle"
    assert all(flat["from"] == "User" and "include" not in flat for flat in (machine, process, filament))

    info = P.printer_info("orca-0.4")
    assert (info.slicer, info.slicer_name) == ("orca", "OrcaSlicer")
    assert (info.nozzle_mm, info.layer_mm) == (0.4, 0.2)
    P.choose_printer("orca-0.4")
    assert P.active_printer() == "orca-0.4"

    report = run_job("a cube", mesh=cube_stl, execute=False, jobs_dir=tmp_path / "jobs")
    plan = report["slice_plan"]
    assert plan["slicer"] == "OrcaSlicer"
    assert Path(plan["request"]["machine"]) == folder / "machine.json"
    assert Path(plan["request"]["process"]) == folder / "process.json"
