import json
import subprocess
import sys
import time
from pathlib import Path
import anyio
import pytest
from mcp import Client

from print3d import doctor
from print3d.blender import BlenderError
from print3d.studio import StudioError


@pytest.fixture
def probes(monkeypatch, tmp_path):
    monkeypatch.setattr(doctor, "SYSTEM", "Linux")
    monkeypatch.setattr(doctor, "find_blender", lambda: tmp_path / "blender")
    monkeypatch.setattr(doctor, "find_studio", lambda: tmp_path / "studio")
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        text = "Blender 4.2.9\n" if command[-1] == "--version" else (Path(__file__).parent / "fixtures/studio-help-linux.txt").read_text()
        kwargs["stdout"].write(text.encode())
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(doctor.subprocess, "run", run)
    return calls


def test_reports_paths_versions_and_bounded_probes_without_creating_jobs(probes, tmp_path, monkeypatch):
    monkeypatch.setenv("PRINT3D_JOBS", str(tmp_path / "jobs"))
    result = doctor.dependency_status()
    assert result["ready"]
    assert result["dependencies"]["blender"]["version"] == "4.2.9"
    assert result["dependencies"]["studio"]["version"] == "02.08.02.61"
    assert all(options["timeout"] == doctor.PROBE_TIMEOUT for _, options in probes)
    assert all(options["stdin"] == subprocess.DEVNULL for _, options in probes)
    assert all(options["stdout"] != subprocess.PIPE and options["stderr"] != subprocess.PIPE
               for _, options in probes)
    assert not (tmp_path / "jobs").exists()


def test_missing_dependencies_are_reported_together(probes, monkeypatch):
    def absent_blender():
        raise BlenderError("Set BLENDER_BIN")

    def absent_studio():
        raise StudioError("Set BAMBU_STUDIO_BIN")

    monkeypatch.setattr(doctor, "find_blender", absent_blender)
    monkeypatch.setattr(doctor, "find_studio", absent_studio)
    report = doctor.dependency_status()
    assert not report["ready"]
    assert all(check["status"] == "missing" for check in report["dependencies"].values())
    assert "BAMBU_STUDIO_BIN" in report["dependencies"]["studio"]["message"]
    assert probes == []


@pytest.mark.parametrize("failure", ["timeout", "launch", "exit"])
def test_unavailable_probe_does_not_hide_other_dependency(probes, monkeypatch, failure):
    run = doctor._probe

    def broken(binary, flag):
        command = [binary, flag]
        if flag == "--version":
            if failure == "timeout":
                raise subprocess.TimeoutExpired(command, doctor.PROBE_TIMEOUT)
            if failure == "launch":
                raise PermissionError("Cannot execute")
            return subprocess.CompletedProcess(command, 1, "", "error")
        return run(binary, flag)

    monkeypatch.setattr(doctor, "_probe", broken)
    report = doctor.dependency_status()
    assert not report["ready"]
    assert report["dependencies"]["blender"]["status"] == "unavailable"
    assert report["dependencies"]["studio"]["ready"]


def test_unknown_version_is_not_a_missing_dependency(probes, monkeypatch):
    monkeypatch.setattr(doctor, "_probe", lambda *args: subprocess.CompletedProcess(args, 0, "", ""))
    report = doctor.dependency_status()
    assert report["ready"]
    assert all(check["version"] is None for check in report["dependencies"].values())


@pytest.mark.parametrize("ready", [True, False])
def test_cli_exit_code_matches_readiness(monkeypatch, capsys, ready):
    from print3d import cli
    monkeypatch.setattr(cli, "dependency_status", lambda: {"ready": ready})
    assert cli.main(["doctor"]) == (0 if ready else 1)
    assert json.loads(capsys.readouterr().out) == {"ready": ready}


def test_mcp_doctor_is_read_only_and_reports_missing_dependency(probes, monkeypatch):
    from print3d.mcp_server import server
    monkeypatch.setattr(doctor, "_probe", lambda *args: subprocess.CompletedProcess(args, 1, "", ""))

    async def run():
        async with Client(server) as client:
            tools = (await client.list_tools()).tools
            tool = next(tool for tool in tools if tool.name == "doctor")
            assert tool.annotations.read_only_hint
            response = await client.call_tool("doctor", {})
            assert not response.is_error
            assert not json.loads(response.content[0].text)["ready"]

    anyio.run(run)


@pytest.mark.parametrize("failure", ["timeout", "launch", "exit"])
def test_studio_probe_failure_is_informational(probes, monkeypatch, failure):
    probe = doctor._probe

    def broken(binary, flag):
        if flag == "--version":
            return probe(binary, flag)
        if failure == "timeout":
            raise subprocess.TimeoutExpired([binary, flag], 5)
        if failure == "launch":
            raise PermissionError("Cannot execute")
        return subprocess.CompletedProcess([binary, flag], 1, "", "x" * 3000 + "useful error")

    monkeypatch.setattr(doctor, "_probe", broken)
    report = doctor.dependency_status()
    assert report["ready"]
    studio = report["dependencies"]["studio"]
    assert studio["status"] == "available"
    assert studio["version"] is None
    if failure == "exit":
        assert studio["message"].endswith("useful error")
        assert len(studio["message"]) < 2100


def test_status_uses_shared_lookup_without_probes(probes, monkeypatch, capsys):
    from print3d import cli
    monkeypatch.setenv("PRINT3D_PRINTER", "x2d-0.4")
    found = doctor.discover_dependencies()
    assert cli.main(["status"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["studio"] == found["studio"]["path"]
    assert report["blender"] == found["blender"]["path"]
    assert not probes


def test_workflow_guide_includes_readiness_contract():
    from print3d.mcp_server import guide
    workflow = guide("workflow")
    assert "doctor" in workflow
    assert "Studio is ready when its executable is found" in workflow
    assert "does not block modelling" in workflow


@pytest.mark.parametrize("system,reader", [("Windows", "windows_file_version"), ("Darwin", "macos_bundle_version")])
@pytest.mark.parametrize("outcome", ["2.8.2.61", None, "error"])
def test_studio_metadata_never_launches_studio_or_blocks_readiness(probes, monkeypatch, system, reader, outcome):
    monkeypatch.setattr(doctor, "SYSTEM", system)

    def metadata(binary):
        if outcome == "error":
            raise OSError("Metadata inaccessible")
        return outcome

    monkeypatch.setattr(doctor, reader, metadata)
    report = doctor.dependency_status()
    assert report["ready"]
    assert report["dependencies"]["studio"]["version"] == (None if outcome == "error" else outcome)
    assert [command[-1] for command, _ in probes] == ["--version"]


def test_probe_closes_stdin_and_collects_real_output(tmp_path):
    script = tmp_path / "probe.py"
    script.write_text("import sys\nassert sys.stdin.read() == ''\nprint('Blender 4.2.9')\nprint('diagnostic', file=sys.stderr)\n")
    result = doctor._probe(sys.executable, str(script))
    assert result.returncode == 0
    assert "Blender 4.2.9" in result.stdout
    assert "diagnostic" in result.stderr


def test_timeout_does_not_wait_for_helper_output_handles(tmp_path, monkeypatch):
    script = tmp_path / "probe.py"
    script.write_text(
        "import subprocess, sys, time\n"
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(3)'], "
        "stdout=sys.stdout, stderr=sys.stderr)\n"
        "time.sleep(10)\n"
    )
    monkeypatch.setattr(doctor, "PROBE_TIMEOUT", 0.5)
    start = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        doctor._probe(sys.executable, str(script))
    assert time.monotonic() - start < 2.5
