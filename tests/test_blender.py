from pathlib import Path
from subprocess import CompletedProcess

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


def test_wsl_location_uses_the_real_windows_install_layout(tmp_path, monkeypatch):
    """The WSL entry mirrors plugin.json/README and finds a versioned Windows install."""
    from print3d.blender import BLENDER_LOCATIONS, _WSL_BLENDER_LOCATIONS

    location = "/mnt/*/Program Files/Blender Foundation/Blender */blender.exe"
    assert location in _WSL_BLENDER_LOCATIONS
    assert location in BLENDER_LOCATIONS

    # Root the real relative structure in tmp_path instead of a generic pattern.
    root = tmp_path / "mnt" / "c" / "Program Files" / "Blender Foundation"
    for version in ("4.9", "4.10"):
        folder = root / f"Blender {version}"
        folder.mkdir(parents=True)
        (folder / "blender.exe").write_bytes(b"")
    launcher = root / "Blender Launcher"
    launcher.mkdir()
    (launcher / "blender.exe").write_bytes(b"")  # must not win the version sort

    monkeypatch.delenv("BLENDER_BIN", raising=False)
    monkeypatch.setattr("print3d.blender.shutil.which", lambda *a, **k: None)
    monkeypatch.setattr("print3d.blender.blender_locations", lambda: iter(()))
    declared = tuple(
        pattern.replace("/mnt/*", str(tmp_path / "mnt" / "*"), 1)
        for pattern in _WSL_BLENDER_LOCATIONS
    )
    monkeypatch.setattr("print3d.blender.BLENDER_LOCATIONS", declared)
    assert find_blender(search_path="") == (root / "Blender 4.10" / "blender.exe").resolve()


def test_explicit_wsl_override_without_version_is_honoured(monkeypatch):
    """The version filter applies to globs only, never to BLENDER_BIN."""
    fake = Path("/mnt/c/Blender Portable/blender.exe")
    monkeypatch.setenv("BLENDER_BIN", str(fake))
    monkeypatch.setattr("print3d.blender.shutil.which", lambda *a, **k: None)
    monkeypatch.setattr(Path, "is_file", lambda self: str(self) == str(fake))
    assert find_blender() == fake.resolve()


def test_wsl_windows_path_uses_wslpath_and_strips_output(monkeypatch):
    """wslpath -w is called with the Linux path and its stdout is trimmed."""
    from print3d import blender as b

    calls: list[list[str]] = []

    def fake_run(command: list[str], **_: object) -> CompletedProcess[str]:
        calls.append(command)
        return CompletedProcess(command, 0, "  C:\\Tools\\blender.exe\n", "")

    monkeypatch.setattr(b.subprocess, "run", fake_run)
    assert b._wsl_windows_path(Path("/home/u/blender")) == "C:\\Tools\\blender.exe"
    assert calls == [["wslpath", "-w", "/home/u/blender"]]


def test_wsl_windows_path_failure_is_actionable(monkeypatch):
    from print3d import blender as b

    def fake_run(command: list[str], **_: object) -> CompletedProcess[str]:
        raise b.subprocess.CalledProcessError(1, command, "wslpath missing")

    monkeypatch.setattr(b.subprocess, "run", fake_run)
    with pytest.raises(BlenderError, match="BLENDER_BIN"):
        b._wsl_windows_path(Path("/home/u/blender"))


def test_wsl_windows_blender_receives_translated_arguments(tmp_path, monkeypatch):
    """A /mnt/* exe gets Windows argv; results are still read via Linux paths."""
    from print3d import blender as b

    script = tmp_path / "model.py"
    script.write_text("")
    out_mesh = tmp_path / "out.stl"
    exe = Path("/mnt/c/Program Files/Blender Foundation/Blender 4.10/blender.exe")
    assert b._is_wsl_windows_exe(exe)

    monkeypatch.setattr(b, "_wsl_windows_path", lambda path: f"W:{path}")
    captured: dict[str, list[str]] = {}

    def fake_run(command: list[str], **_: object) -> CompletedProcess[str]:
        captured["command"] = command
        out_mesh.write_bytes(b"stl")
        Path(command[-1].removeprefix("W:")).write_text("{}")
        return CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(b.subprocess, "run", fake_run)
    report = b.run_model_script(script, out_mesh, params={"width": 40}, blender=exe)

    command = captured["command"]
    assert command[0] == str(exe)
    assert command[6].startswith("W:")  # harness.py
    assert command[8].startswith("W:")  # model script
    assert command[9].startswith("W:")  # out mesh
    assert command[10] == '{"width": 40}'  # JSON is not a path
    assert command[11].startswith("W:")  # result.json
    assert report["mesh"] == str(out_mesh)


def test_linux_blender_keeps_linux_arguments(tmp_path, monkeypatch):
    """The old behaviour stays unchanged for a native Blender."""
    from print3d import blender as b

    script = tmp_path / "model.py"
    script.write_text("")
    out_mesh = tmp_path / "out.stl"
    exe = tmp_path / "blender"
    assert not b._is_wsl_windows_exe(exe)
    captured: dict[str, list[str]] = {}

    def fake_run(command: list[str], **_: object) -> CompletedProcess[str]:
        captured["command"] = command
        out_mesh.write_bytes(b"stl")
        Path(command[-1]).write_text("{}")
        return CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(b.subprocess, "run", fake_run)
    b.run_model_script(script, out_mesh, blender=exe)
    assert captured["command"][6].endswith("harness.py")
    assert captured["command"][8] == str(script)
    assert captured["command"][9] == str(out_mesh)
