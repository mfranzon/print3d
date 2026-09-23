"""Cheap printability checks on triangle meshes before Studio runs."""

from __future__ import annotations

import struct
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

X2D_BED_MM = (256.0, 256.0, 260.0)
MAX_TRIANGLES_FAIL = 2_000_000
MAX_TRIANGLES_WARN = 500_000
MIN_VOLUME_MM3 = 1.0
Z_ON_BED_TOL_MM = 0.2


class DfmError(RuntimeError):
    """Raised when a mesh is not safe to slice."""


@dataclass(frozen=True)
class MeshStats:
    path: str
    triangles: int
    bbox_min: tuple[float, float, float]
    bbox_max: tuple[float, float, float]
    size_mm: tuple[float, float, float]
    volume_mm3: float
    manifold: bool
    on_bed: bool


@dataclass(frozen=True)
class DfmReport:
    ok: bool
    stats: MeshStats
    errors: tuple[str, ...]
    warnings: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        return payload


def read_stl(path: Path) -> list[tuple[tuple[float, float, float], ...]]:
    data = path.read_bytes()
    if data[:5].lower() == b"solid" and b"facet" in data[:200].lower():
        return _read_ascii_stl(data.decode("utf-8", "replace"))
    return _read_binary_stl(data)


def _read_ascii_stl(text: str) -> list[tuple[tuple[float, float, float], ...]]:
    triangles: list[tuple[tuple[float, float, float], ...]] = []
    verts: list[tuple[float, float, float]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith("vertex"):
            parts = stripped.split()
            verts.append((float(parts[1]), float(parts[2]), float(parts[3])))
            if len(verts) == 3:
                triangles.append(tuple(verts))
                verts = []
    return triangles


def _read_binary_stl(data: bytes) -> list[tuple[tuple[float, float, float], ...]]:
    if len(data) < 84:
        raise DfmError("STL is too small to be binary or ASCII.")
    count = struct.unpack_from("<I", data, 80)[0]
    expected = 84 + count * 50
    if len(data) < expected:
        raise DfmError("Binary STL is truncated.")
    triangles: list[tuple[tuple[float, float, float], ...]] = []
    offset = 84
    for _ in range(count):
        nums = struct.unpack_from("<12f", data, offset)
        triangles.append((nums[3:6], nums[6:9], nums[9:12]))
        offset += 50
    return triangles


def _volume(triangles: list[tuple[tuple[float, float, float], ...]]) -> float:
    total = 0.0
    for a, b, c in triangles:
        total += (
            a[0] * (b[1] * c[2] - b[2] * c[1])
            - a[1] * (b[0] * c[2] - b[2] * c[0])
            + a[2] * (b[0] * c[1] - b[1] * c[0])
        )
    return abs(total) / 6.0


def _manifold(triangles: list[tuple[tuple[float, float, float], ...]]) -> bool:
    edges: Counter[tuple[tuple[float, float, float], tuple[float, float, float]]] = Counter()
    for a, b, c in triangles:
        for u, v in ((a, b), (b, c), (c, a)):
            key = (u, v) if u < v else (v, u)
            edges[key] += 1
    return bool(edges) and all(count == 2 for count in edges.values())


def inspect_mesh(path: Path) -> MeshStats:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise DfmError(f"Mesh does not exist: {resolved}")
    triangles = read_stl(resolved)
    if not triangles:
        raise DfmError(f"Mesh has no triangles: {resolved}")
    xs = [v[0] for tri in triangles for v in tri]
    ys = [v[1] for tri in triangles for v in tri]
    zs = [v[2] for tri in triangles for v in tri]
    bbox_min = (min(xs), min(ys), min(zs))
    bbox_max = (max(xs), max(ys), max(zs))
    size = (bbox_max[0] - bbox_min[0], bbox_max[1] - bbox_min[1], bbox_max[2] - bbox_min[2])
    return MeshStats(
        path=str(resolved),
        triangles=len(triangles),
        bbox_min=bbox_min,
        bbox_max=bbox_max,
        size_mm=size,
        volume_mm3=_volume(triangles),
        manifold=_manifold(triangles),
        on_bed=abs(bbox_min[2]) <= Z_ON_BED_TOL_MM,
    )


def check_mesh(path: Path, *, bed_mm: tuple[float, float, float] = X2D_BED_MM) -> DfmReport:
    stats = inspect_mesh(path)
    errors: list[str] = []
    warnings: list[str] = []
    if stats.volume_mm3 < MIN_VOLUME_MM3:
        errors.append(f"Volume {stats.volume_mm3:.3f} mm^3 is too small.")
    if any(stats.size_mm[i] > bed_mm[i] + 0.01 for i in range(3)):
        errors.append(f"Size {stats.size_mm} mm exceeds X2D bed {bed_mm} mm.")
    if stats.triangles > MAX_TRIANGLES_FAIL:
        errors.append(f"{stats.triangles} triangles exceeds {MAX_TRIANGLES_FAIL}.")
    elif stats.triangles > MAX_TRIANGLES_WARN:
        warnings.append(f"{stats.triangles} triangles is large; slice will be slow.")
    if not stats.manifold:
        errors.append("Mesh is not manifold (an edge is not shared by exactly two triangles).")
    if not stats.on_bed:
        warnings.append(f"Lowest Z is {stats.bbox_min[2]:.3f} mm; Studio --ensure-on-bed will lift it.")
    warnings.append("Minimum wall thickness is not checked on triangle meshes.")
    return DfmReport(ok=not errors, stats=stats, errors=tuple(errors), warnings=tuple(warnings))


def require_printable(path: Path) -> DfmReport:
    report = check_mesh(path)
    if not report.ok:
        raise DfmError("; ".join(report.errors))
    return report
