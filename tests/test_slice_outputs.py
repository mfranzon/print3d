"""A successful CLI exit must never authorize output from an earlier run."""
from dataclasses import replace
from pathlib import Path
import os
import shutil
import subprocess
import sys
import zipfile

import pytest

from print3d import studio

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
    expected = subprocess.TimeoutExpired if failure == "timeout" else studio.StudioError
    with pytest.raises(expected) as error:
        studio.run_slice(request_, studio=Path("fake-slicer"))
    if failure == "invalid":
        assert isinstance(error.value.__cause__, zipfile.BadZipFile)
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
    for key in ("command", "project_command"):
        planned = report[key]
        executed = report["executed_" + key]
        assert Path(planned[planned.index("--outputdir") + 1]) == request.output_dir
        assert request.output_dir.is_dir()
        assert executed == calls[0 if key == "project_command" else 1]
        assert not Path(executed[executed.index("--outputdir") + 1]).exists()
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
    with pytest.raises(studio.StudioError):
        studio.run_slice(request_, studio=Path("fake-slicer"))
    assert [p.read_bytes() for p in (request_.output_3mf, request_.output_project_3mf)] == previous


@pytest.mark.parametrize("entry", [studio.slice_plan, studio.run_slice])
@pytest.mark.parametrize("explicit", [False, True])
def test_coincident_outputs_are_rejected_before_running_slicer(request_, monkeypatch, entry, explicit):
    path = request_.output_dir / "same.3mf"
    path.write_bytes(b"existing project")
    request = replace(request_, output_3mf=path, output_project_3mf=path if explicit else None)
    monkeypatch.setattr(studio, "_run", lambda *_: pytest.fail("slicer ran for conflicting paths"))
    with pytest.raises(studio.StudioError, match="different output paths"):
        entry(request, studio=Path("fake-slicer"))
    assert path.read_bytes() == b"existing project"


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission bits and umask")
@pytest.mark.parametrize("mask", [0o022, 0o077, 0o002])
def test_published_file_permissions_follow_umask(tmp_path, mask):
    # Set umask only in a child: changing it in the test process would affect
    # other threads. Exercise new files and replacement of an existing file.
    code = """
import os, stat, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from print3d.studio import _publish
folder, mask = Path(sys.argv[2]), int(sys.argv[3])
os.umask(mask)
source = folder / 'source'
source.write_bytes(b'new archive')
source.chmod(0o600)
for name in ('new.3mf', 'existing.3mf'):
    destination = folder / name
    if name == 'existing.3mf':
        destination.write_bytes(b'old archive')
        destination.chmod(0o600)
    _publish(source, destination)
    assert destination.read_bytes() == b'new archive'
    actual = stat.S_IMODE(destination.stat().st_mode)
    assert actual == 0o666 & ~mask, (oct(actual), oct(mask))
assert not list(folder.glob('.print3d-*'))
"""
    result = subprocess.run([sys.executable, "-c", code, str(Path(studio.__file__).parents[1]),
                             str(tmp_path), str(mask)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
