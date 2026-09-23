from pathlib import Path

import pytest

from text3d.blender import BlenderError, find_blender, run_model_script, scaffold_script


def test_scaffold_carries_the_brief(tmp_path: Path):
    path = scaffold_script(tmp_path / "model.py", prompt="a 40mm tag", slug="tag", size=(40, 16, 3))
    body = path.read_text()
    assert "a 40mm tag" in body
    assert "WIDTH, DEPTH, HEIGHT = (40, 16, 3)" in body
    assert "TODO" in body
    compile(body, str(path), "exec")


def test_missing_script_is_reported_before_blender_starts(tmp_path: Path):
    with pytest.raises(BlenderError, match="does not exist"):
        run_model_script(tmp_path / "absent.py", tmp_path / "out.stl")


def test_find_blender_prefers_the_env_override(tmp_path: Path, monkeypatch):
    fake = tmp_path / "blender"
    fake.write_text("")
    monkeypatch.setenv("BLENDER_BIN", str(fake))
    assert find_blender() == fake.resolve()


def test_find_blender_explains_itself_when_absent(monkeypatch):
    monkeypatch.setenv("BLENDER_BIN", "/nonexistent/blender")
    monkeypatch.setattr("text3d.blender.shutil.which", lambda *a, **k: None)
    monkeypatch.setattr(Path, "is_file", lambda self: False)
    with pytest.raises(BlenderError, match="BLENDER_BIN"):
        find_blender()
