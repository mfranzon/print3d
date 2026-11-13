"""OrcaSlicer as a third slicer vendor (additive: Bambu/Elegoo stay)."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from print3d import flatten as F
from print3d import printers as P
from print3d import studio as S


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _orca_tree(tmp_path: Path) -> Path:
    root = tmp_path / "orca-profiles"
    _write_json(
        root / "OrcaSlicer" / "machine" / "Printer 0.4 nozzle.json",
        {
            "name": "Printer 0.4 nozzle",
            "printer_model": "Printer",
            "nozzle_diameter": ["0.4"],
            "printable_area": ["0x0", "220x0", "220x220", "0x220"],
            "printable_height": 250,
        },
    )
    _write_json(
        root / "OrcaSlicer" / "process" / "0.20mm Standard @Printer.json",
        {"name": "0.20mm Standard @Printer", "layer_height": 0.2, "enable_support": "0"},
    )
    _write_json(
        root / "OrcaSlicer" / "filament" / "Generic PLA @Printer.json",
        {
            "name": "Generic PLA @Printer",
            "filament_settings_id": ["Generic PLA"],
            "filament_cost": 20,
        },
    )
    return root


def test_slicers_register_orca():
    assert P.SLICERS["orca"] == "OrcaSlicer"
    assert "orca-0.4" in P.PRESETS
    assert P.PRESETS["orca-0.4"].slicer == "orca"
    assert P.PRESETS["orca-0.4"].vendor == "OrcaSlicer"


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


def test_build_name_index_orca(tmp_path):
    root = _orca_tree(tmp_path)
    index = F.build_name_index(root, "OrcaSlicer")
    for name in ("Printer 0.4 nozzle", "0.20mm Standard @Printer", "Generic PLA @Printer"):
        assert name in index


def test_flatten_presets_orca(tmp_path, monkeypatch):
    root = _orca_tree(tmp_path)
    monkeypatch.setenv("ORCA_PROFILES_DIR", str(root))
    result = F.flatten_presets(
        output_dir=tmp_path / "flat",
        machine_leaf="Printer 0.4 nozzle",
        process_leaf="0.20mm Standard @Printer",
        filament_leaves=["Generic PLA @Printer"],
        vendor="OrcaSlicer",
    )
    machine = json.loads(Path(result["machine_path"]).read_text(encoding="utf-8"))
    assert machine["print3d_slicer"] == "orca"
    assert Path(result["process_path"]).is_file()
    assert Path(result["filament_paths"][0]).is_file()


def test_flatten_printer_orca_needs_names(tmp_path, monkeypatch):
    root = _orca_tree(tmp_path)
    monkeypatch.setenv("ORCA_PROFILES_DIR", str(root))
    with pytest.raises(F.FlattenError):
        F.flatten_printer("orca-0.4", output_dir=tmp_path / "out", profiles_root=root)
