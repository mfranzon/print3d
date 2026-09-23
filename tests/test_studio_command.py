from pathlib import Path

from print3d.studio import SliceRequest, build_project_command, build_slice_command


def _request(tmp_path: Path) -> SliceRequest:
    return SliceRequest(
        input_mesh=tmp_path / "cube.stl",
        output_3mf=tmp_path / "out" / "cube.gcode.3mf",
        output_project_3mf=tmp_path / "out" / "cube.3mf",
        machine=tmp_path / "machine.json",
        process=tmp_path / "process.json",
        filaments=(tmp_path / "filament.json",),
        datadir=tmp_path / "datadir",
        output_dir=tmp_path / "out",
    )


def test_command_uses_relative_export_and_slice_0(tmp_path: Path):
    request = _request(tmp_path)
    studio = Path("/Applications/BambuStudio.app/Contents/MacOS/BambuStudio")
    command = build_slice_command(studio, request)
    assert "--slice" in command
    assert command[command.index("--slice") + 1] == "0"
    assert command[command.index("--export-3mf") + 1] == "cube.gcode.3mf"
    assert "--export-png" not in command
    assert command[command.index("--orient") + 1] == "1"
    assert "--datadir" in command


def test_project_export_has_no_slice(tmp_path: Path):
    request = _request(tmp_path)
    studio = Path("/Applications/BambuStudio.app/Contents/MacOS/BambuStudio")
    command = build_project_command(studio, request)
    assert "--slice" not in command
    assert command[command.index("--export-3mf") + 1] == "cube.3mf"
