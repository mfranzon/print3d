import os
from pathlib import Path

import pytest

from print3d.paths import repo_root
from print3d.pipeline import run_job

pytestmark = pytest.mark.skipif(
    os.environ.get("PRINT3D_STUDIO_IT") != "1",
    reason="Set PRINT3D_STUDIO_IT=1 to run a live Blender model and Bambu Studio slice",
)


def test_nameplate_end_to_end(tmp_path: Path):
    report = run_job(
        "a 40mm nameplate that says MARCO",
        script=repo_root() / "examples" / "nameplate.py",
        jobs_dir=tmp_path,
        execute=True,
    )
    assert report["status"] == "ready_for_review"
    assert report["severity"] != "error"
    assert report["model"]["non_manifold_edges"] == 0
    assert report["model"]["size_mm"] == pytest.approx([40.0, 16.0, 3.0], abs=0.01)
    plate = report["inspection"]["plates"][0]
    assert plate["layer_count"] == 15
    assert Path(report["open_in_studio"]).is_file()


def test_advice_catches_an_unsupported_ceiling(tmp_path: Path):
    script = tmp_path / "tee.py"
    script.write_text(
        "import bpy\n"
        "bpy.ops.mesh.primitive_cube_add(size=1)\n"
        "stem = bpy.context.active_object\n"
        "stem.scale = (6, 6, 30); bpy.ops.object.transform_apply(scale=True)\n"
        "stem.location.z = 15; bpy.ops.object.transform_apply(location=True)\n"
        "bpy.ops.mesh.primitive_cube_add(size=1)\n"
        "cap = bpy.context.active_object\n"
        "cap.scale = (40, 40, 4); bpy.ops.object.transform_apply(scale=True)\n"
        "cap.location.z = 32; bpy.ops.object.transform_apply(location=True)\n"
        "m = stem.modifiers.new('u', 'BOOLEAN'); m.operation = 'UNION'\n"
        "m.object = cap; m.solver = 'EXACT'\n"
        "bpy.context.view_layer.objects.active = stem\n"
        "bpy.ops.object.modifier_apply(modifier='u')\n"
        "bpy.data.objects.remove(cap, do_unlink=True)\n",
        encoding="utf-8",
    )
    report = run_job("a t shaped bracket", script=script, jobs_dir=tmp_path, execute=True)
    assert report["status"] == "needs_work"
    assert report["severity"] == "error"
    codes = {note["code"] for note in report["advice"]}
    assert "overhang" in codes
    # The slice must agree with the mesh: material appears where the cap starts.
    assert report["slice_metrics"]["max_layer_growth_z"] == pytest.approx(30.2, abs=0.3)
