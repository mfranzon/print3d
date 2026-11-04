import base64
import json

from conftest import write_cube_stl
from print3d.viewer import build_viewer


def _data(page: str) -> dict:
    start = page.index('id="data">') + len('id="data">')
    return json.loads(page[start:page.index("</script>", start)])


def test_one_part_carries_its_mesh_and_numbers(tmp_path):
    job = tmp_path / "plate"
    stl = write_cube_stl(job / "mesh" / "plate.stl")
    report = {
        "brief": {"prompt": "a plate </script> test", "color_name": "red"},
        "mesh_metrics": {"size_mm": [20, 20, 20]},
        "slice_metrics": {"estimated_minutes": 12.5, "filament_g": 3.1},
    }
    viewer = build_viewer(job, report)
    assert viewer == job / "view.html"
    data = _data(viewer.read_text())
    assert data["title"] == "a plate </script> test"
    assert (data["minutes"], data["grams"]) == (12.5, 3.1)
    [part] = data["parts"]
    assert part["color"] == "red" and part["hex"].startswith("#")
    assert base64.b64decode(part["stl"]) == stl.read_bytes()


def test_parts_come_assembled_with_their_groups(tmp_path):
    job = tmp_path / "box"
    for name in ("body", "lid", "mark"):
        write_cube_stl(job / "mesh" / "assembly" / f"{name}.stl")
    report = {
        "brief": {"prompt": "box"},
        "parts": [
            {"name": "body", "object": "body", "color": "white"},
            {"name": "lid", "object": "lid", "color": "white"},
            {"name": "mark", "object": "lid", "color": "black"},
        ],
    }
    data = _data(build_viewer(job, report).read_text())
    assert [(p["name"], p["group"]) for p in data["parts"]] == [("body", "body"), ("lid", "lid"), ("mark", "lid")]


def test_no_mesh_no_viewer(tmp_path):
    report = {"brief": {"prompt": "x"}, "mesh_metrics": {"size_mm": [1, 1, 1]}}
    assert build_viewer(tmp_path / "empty", report) is None


def test_panel_folds_and_starts_folded_in_a_narrow_pane(tmp_path):
    job = tmp_path / "plate"
    write_cube_stl(job / "mesh" / "plate.stl")
    page = build_viewer(job, {"brief": {"prompt": "plate"}, "mesh_metrics": {"size_mm": [20, 20, 20]}}).read_text()
    assert '<details id="panel" open>' in page and "<summary>" in page
    assert "window.innerWidth >= 700" in page


def test_measure_between_two_clicks(tmp_path):
    job = tmp_path / "plate"
    write_cube_stl(job / "mesh" / "plate.stl")
    page = build_viewer(job, {"brief": {"prompt": "plate"}, "mesh_metrics": {"size_mm": [20, 20, 20]}}).read_text()
    assert '<button id="measure" aria-pressed="false">Measure</button>' in page
    assert '<div id="readout" hidden></div>' in page
