from pathlib import Path

import pytest

from conftest import ascii_stl_cube, write_cube_stl
from print3d.dfm import DfmError, check_mesh, require_printable


def test_cube_is_printable(tmp_path: Path):
    path = write_cube_stl(tmp_path / "cube.stl", 20)
    report = require_printable(path)
    assert report.ok
    assert report.stats.triangles == 12
    assert report.stats.size_mm == (20.0, 20.0, 20.0)
    assert report.stats.volume_mm3 == pytest.approx(8000.0, rel=1e-6)
    assert report.stats.manifold


def test_open_triangle_fails(tmp_path: Path):
    path = tmp_path / "tri.stl"
    path.write_text(
        "solid tri\n"
        "  facet normal 0 0 1\n"
        "    outer loop\n"
        "      vertex 0 0 0\n"
        "      vertex 1 0 0\n"
        "      vertex 0 1 0\n"
        "    endloop\n"
        "  endfacet\n"
        "endsolid tri\n",
        encoding="ascii",
    )
    report = check_mesh(path)
    assert not report.ok
    with pytest.raises(DfmError):
        require_printable(path)


def test_oversize_fails(tmp_path: Path):
    path = tmp_path / "huge.stl"
    path.write_text(ascii_stl_cube(300), encoding="ascii")
    report = check_mesh(path)
    assert not report.ok
    assert any("exceeds" in error for error in report.errors)
