import pytest

from print3d import pipeline
from print3d.studio import StudioError


def test_scaffold_is_written_without_studio(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "find_studio", lambda: pytest.fail("scaffold probed Studio"))
    with pytest.raises(pipeline.PipelineError, match="No model yet"):
        pipeline.run_job("test cube", jobs_dir=tmp_path)
    assert (tmp_path / "test_cube/model.py").is_file()


def test_supplied_mesh_checks_studio_before_mesh_analysis(cube_stl, tmp_path, monkeypatch):
    def missing():
        raise StudioError("Set BAMBU_STUDIO_BIN")

    monkeypatch.setattr(pipeline, "find_studio", missing)
    monkeypatch.setattr(pipeline, "require_printable", lambda *a, **kw: pytest.fail("mesh was analyzed"))
    monkeypatch.setattr(pipeline, "run_model_script", lambda *a, **kw: pytest.fail("Blender was started"))
    with pytest.raises(StudioError, match="BAMBU_STUDIO_BIN"):
        pipeline.run_job("test cube", mesh=cube_stl, jobs_dir=tmp_path / "jobs")
