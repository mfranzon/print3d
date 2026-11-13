"""Which printer print3d slices for, and what its presets say about it.

Each printer is a folder of flattened Studio presets, profiles/<key>/ with
machine.json, process.json and filament.json (`print3d flatten --printer <key>`
rebuilds one). The checks read the build volume and the nozzle count from those
presets instead of pinning one printer, so adding a printer is its folder plus
the PRESETS entry `flatten` builds it from, not a change to the checks. The
entry also names the slicer that reads the folder: Bambu Studio for the Bambu
Lab printers, ElegooSlicer for the Elegoo Centauri Carbons.

Choose with `print3d printer <key>` (saved in ~/print3d/printer.json), or for
one run with $PRINT3D_PRINTER. Without either, print3d slices for the X2D, as
it always has. A choice that names a printer print3d has no presets for is an
error, never a silent fall back to another printer.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

PROFILES = Path(__file__).resolve().parent / "profiles"
PRINTER_ENV = "PRINT3D_PRINTER"
DEFAULT_PRINTER = "x2d-0.4"
DEFAULT_BED_TYPE = "Textured PEI Plate"
SLICERS = {"bambu": "Bambu Studio", "elegoo": "ElegooSlicer", "orca": "OrcaSlicer"}

# OrcaSlicer names its profiles per printer ("0.20mm Standard @<printer>"), so
# print3d cannot pin them in PRESETS: set these for your printer.
ORCA_MACHINE_ENV = "PRINT3D_ORCA_MACHINE"
ORCA_PROCESS_ENV = "PRINT3D_ORCA_PROCESS"
ORCA_FILAMENT_ENV = "PRINT3D_ORCA_FILAMENT"


@dataclass(frozen=True)
class Presets:
    """The Studio system presets a printer's folder is flattened from."""

    machine: str
    process: str
    filament: str
    bed_type: str = DEFAULT_BED_TYPE
    # The slicer that reads the flattened folder (a key of SLICERS), and the
    # vendor tree in that slicer's system profiles the presets come from.
    slicer: str = "bambu"
    vendor: str = "BBL"


PRESETS: dict[str, Presets] = {
    "x2d-0.4": Presets(
        machine="Bambu Lab X2D 0.4 nozzle",
        process="0.20mm Standard @BBL X2D",
        filament="Bambu PLA Basic @BBL X2D 0.4 nozzle",
    ),
    "p2s-0.4": Presets(
        machine="Bambu Lab P2S 0.4 nozzle",
        process="0.20mm Standard @BBL P2S",
        filament="Bambu PLA Basic @BBL P2S",
    ),
    "elegoo-cc-0.4": Presets(
        machine="Elegoo Centauri Carbon 0.4 nozzle",
        process="0.20mm Standard @Elegoo CC 0.4 nozzle",
        filament="Elegoo PLA @ECC",
        slicer="elegoo",
        vendor="Elegoo",
    ),
    "elegoo-cc2-0.4": Presets(
        machine="Elegoo Centauri Carbon 2 0.4 nozzle",
        process="0.20mm Standard @Elegoo CC2 0.4 nozzle",
        filament="Elegoo PLA @ECC2",
        slicer="elegoo",
        vendor="Elegoo",
    ),
    # OrcaSlicer: the three profile names come from the installed OrcaSlicer
    # profile tree (per printer); empty here means "set PRINT3D_ORCA_*".
    "orca-0.4": Presets(
        machine=os.environ.get(ORCA_MACHINE_ENV, "").strip(),
        process=os.environ.get(ORCA_PROCESS_ENV, "").strip(),
        filament=os.environ.get(ORCA_FILAMENT_ENV, "").strip(),
        slicer="orca",
        vendor="OrcaSlicer",
    ),
}


@dataclass(frozen=True)
class Printer:
    """What a printer's pinned presets say, as the checks and the print sheet need it."""

    key: str
    name: str
    nozzles: int
    nozzle_mm: float
    bed_mm: tuple[float, float, float]
    layer_mm: float
    supports: bool
    filament: str
    bed_type: str
    filament_cost_per_kg: float | None
    slicer: str = "bambu"

    @property
    def slicer_name(self) -> str:
        return SLICERS[self.slicer]

    def describe(self) -> str:
        """The print sheet's line: printer, nozzle, layers, filament, plate, supports."""
        filament = self.filament.split(" @", 1)[0]
        bed = self.bed_type.removesuffix(" Plate")
        bed = bed[:1].lower() + bed[1:]
        return (
            f"{self.name}, {self.nozzle_mm:g} mm nozzle, {self.layer_mm:.2f} mm layers, "
            f"{filament}, {bed}, supports {'on' if self.supports else 'off'}"
        )

    def as_dict(self) -> dict[str, Any]:
        return {
        **asdict(self),
        "bed_mm": list(self.bed_mm),
        "slicer_name": self.slicer_name,
        "summary": self.describe(),
    }


def available() -> list[str]:
    """The printers print3d has pinned presets for."""
    if not PROFILES.is_dir():
        return []
    return sorted(path.name for path in PROFILES.iterdir() if (path / "machine.json").is_file())


def choice_path() -> Path:
    return Path.home() / "print3d" / "printer.json"


def _known(key: str, source: str) -> str:
    printers = available()
    if key not in printers:
        raise ValueError(
            f"{source} names the printer {key!r}, and print3d has no presets for it. "
            f"Choose one of: {', '.join(printers)}."
        )
    return key


def printer_choice() -> dict[str, str]:
    """The printer to slice for, and where that came from: $PRINT3D_PRINTER, the saved choice, the default."""
    if env := os.environ.get(PRINTER_ENV, "").strip():
        return {"printer": _known(env, f"${PRINTER_ENV}"), "source": f"${PRINTER_ENV}"}
    path = choice_path()
    if path.is_file():
        try:
            saved = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path} is not valid JSON ({exc}). Run `print3d printer <key>` again.") from exc
        key = saved.get("printer") if isinstance(saved, dict) else None
        if key:
            return {"printer": _known(str(key), str(path)), "source": str(path)}
    return {"printer": DEFAULT_PRINTER, "source": "default"}


def active_printer() -> str:
    return printer_choice()["printer"]


def _load(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} is not a Studio preset.")
    return data


def _bed_mm(machine: dict[str, Any]) -> tuple[float, float, float]:
    """The build volume as Studio limits it: the printable_area polygon and printable_height."""
    corners = [[float(v) for v in str(point).strip().split("x")] for point in machine["printable_area"]]
    xs, ys = [c[0] for c in corners], [c[1] for c in corners]
    return (max(xs) - min(xs), max(ys) - min(ys), float(machine["printable_height"]))


def _first_float(values: Any) -> float | None:
    """A Studio array setting's first entry as a number, or None when it is unset."""
    value: Any = values[0] if isinstance(values, list) and values else values
    if value is None or value == "":
        return None
    return float(value)


def read_printer(folder: Path) -> Printer:
    """The printer whose flattened presets are in `folder`."""
    machine = _load(folder / "machine.json")
    process = _load(folder / "process.json")
    filament = _load(folder / "filament.json")
    nozzles = machine.get("nozzle_diameter") or ["0.4"]
    filament_ids = filament.get("filament_settings_id") or [filament.get("name", "")]
    return Printer(
        key=folder.name,
        name=str(machine.get("printer_model") or machine.get("name") or folder.name),
        nozzles=len(nozzles),
        nozzle_mm=float(nozzles[0]),
        bed_mm=_bed_mm(machine),
        layer_mm=float(process["layer_height"]),
        supports=str(process.get("enable_support", "0")) == "1",
        filament=str(filament_ids[0]),
        bed_type=str(process.get("curr_bed_type") or DEFAULT_BED_TYPE),
        filament_cost_per_kg=_first_float(filament.get("filament_cost")),
        slicer=_slicer_for(folder.name, machine),
    )


def _slicer_for(key: str, machine: dict[str, Any]) -> str:
    """The slicer for a folder: its PRESETS entry, a written marker, else the vendor its machine preset names."""
    if key in PRESETS:
        return PRESETS[key].slicer
    marker = str(machine.get("print3d_slicer", "")).strip().lower()
    if marker in SLICERS:
        return marker
    model = str(machine.get("printer_model") or machine.get("name") or "")
    if model.lower().startswith("elegoo"):
        return "elegoo"
    if model.lower().startswith("orca"):
        return "orca"
    return "bambu"


def printer_info(key: str | None = None) -> Printer:
    """The printer named by `key`, or the one print3d slices for."""
    return read_printer(PROFILES / _known(key or active_printer(), "printer_info"))


def status() -> dict[str, Any]:
    choice = printer_choice()
    return {**choice, "available": available(), "info": printer_info(choice["printer"]).as_dict()}


def choose_printer(key: str) -> dict[str, Any]:
    """Save `key` as the printer to slice for. $PRINT3D_PRINTER still wins for the runs it is set in."""
    _known(key, "print3d printer")
    path = choice_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"printer": key, "chosen_at": time.strftime("%Y-%m-%d")}, indent=2) + "\n",
        encoding="utf-8",
    )
    return status()
