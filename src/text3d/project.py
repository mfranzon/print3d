"""Inspect a sliced Bambu .gcode.3mf project."""

from __future__ import annotations

import hashlib
import re
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

PLATE_GCODE_RE = re.compile(r"^Metadata/plate_(\d+)\.gcode$")
TIME_RE = re.compile(r"total estimated time:\s*(.+)$", re.IGNORECASE)
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
    return {"metadata": meta, "filaments": filaments}


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
            gcode_bytes = archive.read(member)
            md5_name = f"Metadata/plate_{index}.gcode.md5"
            md5_ok: bool | None = None
            if md5_name in names:
                expected = archive.read(md5_name).decode("ascii", "replace").strip()
                md5_ok = expected.lower() == _md5_bytes(gcode_bytes).lower()
            png_name = f"Metadata/plate_{index}.png"
            stats = _parse_gcode_stats(gcode_bytes.decode("utf-8", "replace"))
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


def slice_info_filament_g(slice_info: str | None) -> float | None:
    if not slice_info:
        return None
    try:
        root = ET.fromstring(slice_info)
    except ET.ParseError:
        return None
    total = 0.0
    found = False
    for filament in root.iter("filament"):
        used = filament.attrib.get("used_g") or filament.attrib.get("weight")
        if used:
            total += float(used)
            found = True
    return total if found else None


def read_plate_gcode(path: Path, plate: int = 1) -> str:
    """The raw gcode for one plate of a sliced project."""
    member = f"Metadata/plate_{plate}.gcode"
    with zipfile.ZipFile(path.expanduser().resolve()) as archive:
        if member not in archive.namelist():
            raise ProjectError(f"{path} has no {member}.")
        return archive.read(member).decode("utf-8", "replace")
