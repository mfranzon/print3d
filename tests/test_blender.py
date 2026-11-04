from pathlib import Path

import pytest

from print3d.blender import BlenderError, find_blender, run_model_script, scaffold_script


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
    monkeypatch.setattr("print3d.blender.shutil.which", lambda *a, **k: None)
    monkeypatch.setattr(Path, "is_file", lambda self: False)
    with pytest.raises(BlenderError, match="BLENDER_BIN"):
        find_blender()


@pytest.mark.parametrize("location", ["~/blender-*/blender", "/opt/blender*/blender"])
def test_tarball_locations_select_highest_complete_version(tmp_path, monkeypatch, location):
    root = tmp_path / "home [user]" if location.startswith("~") else tmp_path / "opt"
    root.mkdir()
    monkeypatch.setenv("HOME", str(root))
    monkeypatch.setenv("USERPROFILE", str(root))
    pattern = location if location.startswith("~") else str(root / "blender*" / "blender")
    for version in ("4.9", "4.10"):
        folder = root / f"blender-{version}-linux-x64"
        folder.mkdir()
        (folder / "blender").write_bytes(b"")
    (root / "blender-5.0-linux-x64").mkdir()
    (root / "blender-6.0-linux-x64" / "blender").mkdir(parents=True)
    monkeypatch.delenv("BLENDER_BIN", raising=False)
    monkeypatch.setattr("print3d.blender.shutil.which", lambda *a, **k: None)
    from print3d.blender import BLENDER_LOCATIONS
    assert location in BLENDER_LOCATIONS
    monkeypatch.setattr("print3d.blender.BLENDER_LOCATIONS", (pattern,))
    monkeypatch.setattr("print3d.blender.blender_locations", lambda: iter(()))
    expected = root / "blender-4.10-linux-x64" / "blender"
    assert find_blender(search_path="") == expected.resolve()

    override = root / "custom-blender"
    override.write_bytes(b"")
    monkeypatch.setenv("BLENDER_BIN", str(override))
    assert find_blender(search_path="") == override.resolve()
    monkeypatch.delenv("BLENDER_BIN")
    monkeypatch.setattr("print3d.blender.shutil.which", lambda *a, **k: str(override))
    assert find_blender() == override.resolve()


def test_missing_tarball_patterns_report_blender_error(tmp_path, monkeypatch):
    monkeypatch.delenv("BLENDER_BIN", raising=False)
    monkeypatch.setattr("print3d.blender.shutil.which", lambda *a, **k: None)
    monkeypatch.setattr("print3d.blender.BLENDER_LOCATIONS", (str(tmp_path / "blender-*" / "blender"),))
    monkeypatch.setattr("print3d.blender.blender_locations", lambda: iter(()))
    with pytest.raises(BlenderError, match="BLENDER_BIN"):
        find_blender(search_path="")
