"""A successful CLI exit must never authorize output from an earlier run."""
from dataclasses import replace
from pathlib import Path
import shutil
import subprocess
import zipfile

import pytest

from print3d import studio
from print3d.project import ProjectError

FIXTURE = Path(__file__).parent / "fixtures/cube_20mm.gcode.3mf"


@pytest.fixture
def request_(tmp_path):
    output = tmp_path / "slice"
    output.mkdir()
    request = studio.SliceRequest(
        input_mesh=tmp_path / "cube.stl", output_3mf=output / "cube.gcode.3mf",
        output_project_3mf=output / "cube.3mf", machine=tmp_path / "machine.json",
        process=tmp_path / "process.json", filaments=(tmp_path / "filament.json",),
        datadir=tmp_path / "data", output_dir=output,
    )
    for path in (request.output_3mf, request.output_project_3mf):
        shutil.copyfile(FIXTURE, path)
    return request


def fake_cli(monkeypatch, *, failure_stage=None, failure=None):
    calls = []

    def run(command, timeout_s):
        stage = "slice" if "--slice" in command else "project"
        calls.append(command)
        output = Path(command[command.index("--outputdir") + 1])
        dest = output / command[command.index("--export-3mf") + 1]
        if stage == failure_stage:
            if failure == "timeout":
                dest.write_bytes(b"partial output")
                raise subprocess.TimeoutExpired(command, timeout_s)
            if failure == "exit":
                dest.write_bytes(b"partial output")
                return subprocess.CompletedProcess(command, 1, "", "slicer error")
            if failure == "invalid":
                dest.write_bytes(b"not a 3mf")
            # missing: exit 0 without creating output
        else:
            shutil.copyfile(FIXTURE, dest)
            with zipfile.ZipFile(dest, "a") as archive:
                archive.writestr("fresh-run.txt", "new output")
        return subprocess.CompletedProcess(command, 0, "fresh run", "")

    monkeypatch.setattr(studio, "_run", run)
    return calls


@pytest.mark.parametrize("stage", ["project", "slice"])
@pytest.mark.parametrize("failure", ["missing", "exit", "timeout", "invalid"])
def test_failed_rerun_preserves_both_previous_outputs(request_, monkeypatch, stage, failure):
    paths = (request_.output_3mf, request_.output_project_3mf)
    previous = [path.read_bytes() for path in paths]
    fake_cli(monkeypatch, failure_stage=stage, failure=failure)
    with pytest.raises((studio.StudioError, subprocess.TimeoutExpired, zipfile.BadZipFile)):
        studio.run_slice(request_, studio=Path("fake-slicer"))
    assert [path.read_bytes() for path in paths] == previous
    assert sorted(path.name for path in request_.output_dir.iterdir()) == ["cube.3mf", "cube.gcode.3mf"]


@pytest.mark.parametrize("slicer", ["bambu", "elegoo"])
def test_success_publishes_both_fresh_outputs_and_cleans_staging(request_, monkeypatch, slicer):
    request = replace(request_, slicer=slicer)
    calls = fake_cli(monkeypatch)
    report = studio.run_slice(request, studio=Path("fake-slicer"))
    assert report["output_3mf"] == str(request.output_3mf)
    assert report["output_project_3mf"] == str(request.output_project_3mf)
    assert report["returncode"] == 0
    assert report["stdout"] == "fresh run"
    for path in (request.output_3mf, request.output_project_3mf):
        with zipfile.ZipFile(path) as archive:
            assert archive.read("fresh-run.txt") == b"new output"
    assert all(Path(cmd[cmd.index("--outputdir") + 1]) != request.output_dir for cmd in calls)
    assert sorted(path.name for path in request.output_dir.iterdir()) == ["cube.3mf", "cube.gcode.3mf"]


def test_same_basename_in_separate_destination_folders(request_, monkeypatch, tmp_path):
    request = replace(request_, output_project_3mf=tmp_path / "editable" / "same.3mf",
                      output_3mf=tmp_path / "sliced" / "same.3mf")
    fake_cli(monkeypatch)
    result = studio.run_slice(request, studio=Path("fake-slicer"))
    assert Path(result["output_3mf"]).is_file()
    assert Path(result["output_project_3mf"]).is_file()


@pytest.mark.parametrize("invalid", ["checksum", "unsliced"])
def test_invalid_gcode_is_not_published(request_, monkeypatch, invalid):
    previous = [p.read_bytes() for p in (request_.output_3mf, request_.output_project_3mf)]
    fake_cli(monkeypatch)
    run = studio._run

    def corrupt(command, timeout):
        result = run(command, timeout)
        if "--slice" in command:
            output = Path(command[command.index("--outputdir") + 1]) / request_.output_3mf.name
            with zipfile.ZipFile(output) as archive:
                files = {n: archive.read(n) for n in archive.namelist()}
            for name in list(files):
                if ".gcode" in name:
                    if invalid == "unsliced":
                        del files[name]
                    elif name.endswith(".md5"):
                        files[name] = b"invalid checksum"
            with zipfile.ZipFile(output, "w") as archive:
                for name, data in files.items():
                    archive.writestr(name, data)
        return result

    monkeypatch.setattr(studio, "_run", corrupt)
    with pytest.raises((studio.StudioError, ProjectError)):
        studio.run_slice(request_, studio=Path("fake-slicer"))
    assert [p.read_bytes() for p in (request_.output_3mf, request_.output_project_3mf)] == previous
