"""A rerun must not authorize handoff with a report from an older slice."""
import json
import shutil
from pathlib import Path

import pytest

from conftest import write_cube_stl
from print3d import pipeline
from print3d.blender import BlenderError
from print3d.dfm import DfmError
from print3d.handoff import HandoffError, build_handoff
from print3d.studio import StudioError

GOLDEN = Path(__file__).parent / "fixtures" / "cube_20mm.gcode.3mf"


@pytest.fixture(params=[False, True], ids=["single", "declared-parts"])
def job(tmp_path, monkeypatch, request):
    monkeypatch.setenv("PRINT3D_FITS", str(tmp_path / "fits.json"))
    script = tmp_path / "model.py"
    script.write_text("# Native processes are replaced by mesh and slice fixtures.")
    job_dir = tmp_path / "jobs" / "state_test_cube"
    state = {"side": 20, "failure": None, "status_at_model_start": None}

    def model(script, output, **kwargs):
        report = job_dir / "report.json"
        state["status_at_model_start"] = json.loads(report.read_text())["status"] if report.exists() else None
        write_cube_stl(output, state["side"])
        if state["failure"] == "model":
            raise BlenderError("model failed after export")
        if state["failure"] == "interrupt":
            raise KeyboardInterrupt()
        result = {"mesh": str(output), "size_mm": [state["side"]] * 3}
        if request.param:
            part = write_cube_stl(output.parent / "parts" / "body.stl", state["side"])
            write_cube_stl(output.parent / "assembly" / "body.stl", state["side"])
            result.update(
                parts=[{"name": "body", "color": "white", "print_stl": str(part),
                        "print_size_mm": [state["side"]] * 3, "settings": {}}],
                objects=[{"name": "body", "parts": ["body"], "plate": 1, "stl": str(part)}],
                fits=[], declared_fits=[],
            )
        return result

    def slice_(request):
        if state["failure"] == "slice":
            raise StudioError("slice failed")
        request.output_dir.mkdir(parents=True, exist_ok=True)
        for dest in (request.output_3mf, request.output_project_3mf):
            shutil.copyfile(GOLDEN, dest)
        return {"returncode": 0, "output_3mf": str(request.output_3mf),
                "output_project_3mf": str(request.output_project_3mf),
                "output_size_bytes": request.output_3mf.stat().st_size}

    monkeypatch.setattr(pipeline, "run_model_script", model)
    monkeypatch.setattr(pipeline, "run_slice", slice_)
    monkeypatch.setattr(pipeline, "find_studio", lambda: Path("fixture-studio"))

    def run(**kwargs):
        return pipeline.run_job("state test cube", script=script, jobs_dir=tmp_path / "jobs", **kwargs)

    assert run()["status"] == "ready_for_review"
    return job_dir, state, run


def test_missing_studio_stops_before_modelling_and_blocks_old_handoff(job, monkeypatch):
    job_dir, state, run = job
    previous_mesh = next((job_dir / "mesh").glob("*.stl")).read_bytes()
    delivery = build_handoff(job_dir, render=False)
    sheet = Path(delivery["sheet"])
    previous_sheet = sheet.read_bytes()
    state["status_at_model_start"] = None

    def missing():
        raise StudioError("Set BAMBU_STUDIO_BIN")

    monkeypatch.setattr(pipeline, "find_studio", missing)
    with pytest.raises(StudioError, match="BAMBU_STUDIO_BIN"):
        run()
    assert state["status_at_model_start"] is None
    assert next((job_dir / "mesh").glob("*.stl")).read_bytes() == previous_mesh
    assert json.loads((job_dir / "report.json").read_text())["status"] == "failed"
    with pytest.raises(HandoffError):
        build_handoff(job_dir, render=False)
    assert sheet.read_bytes() == previous_sheet


def test_dry_run_does_not_require_studio(job, monkeypatch):
    _, _, run = job
    monkeypatch.setattr(pipeline, "find_studio", lambda: pytest.fail("dry run probed Studio"))
    assert run(execute=False)["status"] == "dry_run"


def test_dry_run_blocks_handoff_until_a_successful_slice(job):
    job_dir, state, run = job
    state["side"] = 30
    result = run(execute=False)
    with pytest.raises(HandoffError):
        build_handoff(job_dir, render=False)
    saved = json.loads((job_dir / "report.json").read_text())
    assert saved == json.loads(json.dumps(result))
    assert saved["status"] == "dry_run"
    assert state["status_at_model_start"] == "running"

    state["side"] = 20
    assert run()["status"] == "ready_for_review"
    assert Path(build_handoff(job_dir, render=False)["sheet"]).is_file()


@pytest.mark.parametrize("failure", ["model", "dfm", "slice"])
def test_failed_rerun_blocks_handoff_and_preserves_previous_delivery(job, failure):
    job_dir, state, run = job
    delivery = build_handoff(job_dir, render=False)
    sheet = Path(delivery["sheet"])
    previous_sheet = sheet.read_bytes()
    state.update(failure=failure, side=300 if failure == "dfm" else 30)
    expected = {"model": pipeline.PipelineError, "dfm": (DfmError, pipeline.PipelineError),
                "slice": StudioError}[failure]
    with pytest.raises(expected) as error:
        run()
    with pytest.raises(HandoffError):
        build_handoff(job_dir, render=False)
    saved = json.loads((job_dir / "report.json").read_text())
    assert saved["status"] == "failed"
    assert saved["error"] == str(error.value)
    assert state["status_at_model_start"] == "running"
    assert sheet.read_bytes() == previous_sheet


def test_interrupted_rerun_leaves_a_non_deliverable_report(job):
    job_dir, state, run = job
    state.update(failure="interrupt", side=30)
    with pytest.raises(KeyboardInterrupt):
        run()
    with pytest.raises(HandoffError):
        build_handoff(job_dir, render=False)
    assert json.loads((job_dir / "report.json").read_text())["status"] == "running"
