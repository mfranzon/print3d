"""Inspect a sliced Bambu .gcode.3mf project."""

from __future__ import annotations

import hashlib
import re
import zipfile
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

PLATE_GCODE_RE = re.compile(r"^Metadata/plate_(\d+)\.gcode$")
TIME_RE = re.compile(r"total estimated time:\s*(.+)$", re.IGNORECASE)
ORCA_TIME_RE = re.compile(
    r"estimated printing time \((?:normal|quiet) mode\)\s*=\s*(.+)$", re.IGNORECASE
)
FILAMENT_G_RE = re.compile(r"total filament weight \[g\]\s*[:=]\s*([0-9.]+)", re.IGNORECASE)
FILAMENT_M_RE = re.compile(
    r"total filament length \[mm\]\s*[:=]\s*([0-9., ]+)|filament used \[mm\]\s*[:=]\s*([0-9., ]+)",
    re.IGNORECASE,
)
LAYER_RE = re.compile(r"total layer number:\s*(\d+)", re.IGNORECASE)


class ProjectError(RuntimeError):
    """Raised when a 3mf is not a sliced Bambu project."""


@dataclass(frozen=True)
class PlateInfo:
    index: int
    gcode_path: str
    gcode_bytes: int
    md5_ok: bool | None
    png_path: str | None
    estimated_time: str | None
    filament_g: float | None
    filament_mm: str | None
    layer_count: int | None


def _md5_bytes(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


def _header_block(gcode: str) -> str:
    lines: list[str] = []
    in_header = False
    for line in gcode.splitlines():
        if "HEADER_BLOCK_START" in line:
            in_header = True
            continue
        if "HEADER_BLOCK_END" in line:
            break
        if in_header or (not lines and line.startswith(";")):
            lines.append(line)
            if not in_header and len(lines) > 40:
                break
    return "\n".join(lines)


def _parse_gcode_stats(gcode: str) -> dict[str, Any]:
    header = _header_block(gcode)
    time = None
    filament_g = None
    filament_mm = None
    layers = None
    for line in header.splitlines():
        match = TIME_RE.search(line)
        if match:
            time = match.group(1).strip().rstrip(";")
        match = FILAMENT_G_RE.search(line)
        if match:
            filament_g = float(match.group(1))
        match = FILAMENT_M_RE.search(line)
        if match:
            filament_mm = (match.group(1) or match.group(2) or "").strip()
        match = LAYER_RE.search(line)
        if match:
            layers = int(match.group(1))
    if time is None:
        # Orca-lineage slicers write the summary line after the gcode, outside
        # the header comments.
        match = TIME_RE.search(gcode) or ORCA_TIME_RE.search(gcode)
        if match:
            time = match.group(1).strip().rstrip(";")
    return {
        "estimated_time": time,
        "filament_g": filament_g,
        "filament_mm": filament_mm or None,
        "layer_count": layers,
    }


def _parse_slice_info(slice_info: str | None) -> dict[str, Any]:
    if not slice_info:
        return {}
    try:
        root = ET.fromstring(slice_info)
    except ET.ParseError:
        return {}
    meta: dict[str, str] = {}
    for item in root.findall(".//metadata"):
        key = item.attrib.get("key")
        value = item.attrib.get("value")
        if key and value is not None:
            meta[key] = value
    filaments = []
    for filament in root.findall(".//filament"):
        filaments.append(dict(filament.attrib))
    plates = []
    for plate in root.findall("plate"):
        plate_meta = {
            item.attrib["key"]: item.attrib.get("value")
            for item in plate.findall("metadata")
            if "key" in item.attrib
        }
        plates.append(
            {
                "index": int(plate_meta.get("index") or len(plates) + 1),
                "weight_g": float(plate_meta["weight"]) if plate_meta.get("weight") else None,
                "filaments": [dict(f.attrib) for f in plate.findall("filament")],
            }
        )
    return {"metadata": meta, "filaments": filaments, "plates": plates}


def inspect_project(path: Path) -> dict[str, Any]:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise ProjectError(f"Project does not exist: {resolved}")
    if resolved.suffix.lower() != ".3mf":
        raise ProjectError(f"Not a .3mf file: {resolved}")
    plates: list[PlateInfo] = []
    names: list[str] = []
    slice_info = None
    with zipfile.ZipFile(resolved) as archive:
        names = archive.namelist()
        gcodes = sorted(
            (int(match.group(1)), member)
            for member in names
            if (match := PLATE_GCODE_RE.match(member))
        )
        if not gcodes:
            raise ProjectError(
                f"{resolved} has no Metadata/plate_N.gcode. Re-slice; this is not a print job."
            )
        if "Metadata/slice_info.config" in names:
            slice_info = archive.read("Metadata/slice_info.config").decode("utf-8", "replace")
        parsed_info = _parse_slice_info(slice_info)
        for index, member in gcodes:
            gcode_bytes, gcode_text = _cached_plate_gcode(str(resolved), index)
            md5_name = f"Metadata/plate_{index}.gcode.md5"
            md5_ok: bool | None = None
            if md5_name in names:
                expected = archive.read(md5_name).decode("ascii", "replace").strip()
                md5_ok = expected.lower() == _md5_bytes(gcode_bytes).lower()
            png_name = f"Metadata/plate_{index}.png"
            stats = _parse_gcode_stats(gcode_text)
            info_weight = parsed_info.get("metadata", {}).get("weight")
            if stats["filament_g"] is None and info_weight:
                stats["filament_g"] = float(info_weight)
            plates.append(
                PlateInfo(
                    index=index,
                    gcode_path=member,
                    gcode_bytes=len(gcode_bytes),
                    md5_ok=md5_ok,
                    png_path=png_name if png_name in names else None,
                    estimated_time=stats["estimated_time"],
                    filament_g=stats["filament_g"],
                    filament_mm=stats["filament_mm"],
                    layer_count=stats["layer_count"],
                )
            )
    return {
        "path": str(resolved),
        "size_bytes": resolved.stat().st_size,
        "entries": names,
        "plates": [asdict(plate) for plate in plates],
        "slice_info": slice_info,
        "slice_info_parsed": parsed_info,
        "sliced": True,
    }


def extract_plate_png(path: Path, dest: Path, plate: int = 1) -> Path | None:
    member = f"Metadata/plate_{plate}.png"
    with zipfile.ZipFile(path) as archive:
        if member not in archive.namelist():
            return None
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(archive.read(member))
        return dest


def read_plate_gcode(path: Path, plate: int = 1) -> str:
    """The raw gcode for one plate of a sliced project."""
    return _cached_plate_gcode(str(_resolve_3mf(path)), plate)[1]


def _resolve_3mf(path: Path) -> Path:
    return path.expanduser().resolve()


@lru_cache(maxsize=4)
def _cached_plate_gcode(path_str: str, plate: int) -> tuple[bytes, str]:
    """The plate's gcode as (bytes, text), memoized per file state.

    The pipeline inspects the same sliced project several times (inspection,
    per-plate analysis, hand-off); decompressing multi-megabyte gcode per call
    dominated small-job runtimes. The file's mtime and size key the cache, so
    a re-sliced or repaired project is re-read.
    """
    resolved = Path(path_str)
    st = resolved.stat()
    with zipfile.ZipFile(resolved) as archive:
        member = f"Metadata/plate_{plate}.gcode"
        if member not in archive.namelist():
            raise ProjectError(f"{resolved} has no {member}.")
        data = archive.read(member)
    return data, data.decode("utf-8", "replace")


def slice_info_weight_g(path: Path, plate: int = 1) -> float | None:
    """The plate's filament weight from slice_info, for gcode headers that omit it."""
    try:
        with zipfile.ZipFile(path.expanduser().resolve()) as archive:
            if "Metadata/slice_info.config" not in archive.namelist():
                return None
            parsed = _parse_slice_info(archive.read("Metadata/slice_info.config").decode("utf-8", "replace"))
    except (OSError, zipfile.BadZipFile):
        return None
    for plate_info in parsed.get("plates", []):
        if plate_info.get("index") == plate:
            return plate_info.get("weight_g")
    return None
