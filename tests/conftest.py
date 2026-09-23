"""Shared mesh fixtures. The cube used to come from the package; it is only
ever a test input now that Blender owns modelling.
"""

from pathlib import Path

import pytest


def ascii_stl_cube(size_mm: float = 20.0) -> str:
    s = float(size_mm)
    corners = [
        (0, 0, 0), (s, 0, 0), (s, s, 0), (0, s, 0),
        (0, 0, s), (s, 0, s), (s, s, s), (0, s, s),
    ]
    faces = [
        (0, 3, 2, 1, (0, 0, -1)), (4, 5, 6, 7, (0, 0, 1)),
        (0, 1, 5, 4, (0, -1, 0)), (2, 3, 7, 6, (0, 1, 0)),
        (1, 2, 6, 5, (1, 0, 0)), (3, 0, 4, 7, (-1, 0, 0)),
    ]
    lines = ["solid cube"]
    for a, b, c, d, normal in faces:
        for tri in ((a, b, c), (a, c, d)):
            lines.append("  facet normal %g %g %g" % normal)
            lines.append("    outer loop")
            lines.extend("      vertex %g %g %g" % corners[i] for i in tri)
            lines.append("    endloop")
            lines.append("  endfacet")
    lines.append("endsolid cube")
    return "\n".join(lines) + "\n"


def write_cube_stl(path: Path, size_mm: float = 20.0) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(ascii_stl_cube(size_mm), encoding="ascii")
    return path


@pytest.fixture
def cube_stl(tmp_path: Path) -> Path:
    return write_cube_stl(tmp_path / "cube.stl", 20.0)
