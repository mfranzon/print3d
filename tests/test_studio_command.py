from pathlib import Path

from print3d.studio import (
    SliceRequest,
    _arg_path,
    _copy_presets_to,
    _windows_slicer,
    build_project_command,
    build_slice_command,
)


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


def test_wsl_paths_are_translated_only_for_windows_slicers():
    exe = Path("/mnt/c/Program Files/OrcaSlicer/orca-slicer.exe")
    assert _windows_slicer(exe)
    assert _arg_path("/mnt/c/jobs/cube.stl", exe) == "C:/jobs/cube.stl"
    assert _arg_path("/home/christian/cube.stl", exe) == "/home/christian/cube.stl"
    assert not _windows_slicer(Path("/usr/bin/orca-slicer"))
    assert _arg_path("/mnt/c/jobs/cube.stl", Path("/usr/bin/orca-slicer")) == "/mnt/c/jobs/cube.stl"


def test_windows_slicer_command_uses_drive_paths():
    exe = Path("/mnt/c/Program Files/OrcaSlicer/orca-slicer.exe")
    request = SliceRequest(
        input_mesh=Path("/mnt/c/jobs/cube.stl"),
        output_3mf=Path("/mnt/c/jobs/out/cube.gcode.3mf"),
        machine=Path("/mnt/c/jobs/presets/machine.json"),
        process=Path("/mnt/c/jobs/presets/process.json"),
        filaments=(Path("/mnt/c/jobs/presets/filament.json"),),
        datadir=Path("/mnt/c/jobs/datadir"),
        output_dir=Path("/mnt/c/jobs/out"),
        slicer="orca",
    )
    command = build_slice_command(exe, request)
    assert "C:/jobs/presets/machine.json;C:/jobs/presets/process.json" in command
    assert "C:/jobs/presets/filament.json" in command
    assert "C:/jobs/datadir" in command
    assert "C:/jobs/out" in command
    assert command[-1] == "C:/jobs/cube.stl"


def test_windows_staging_copies_presets_into_the_datadir(tmp_path: Path):
    for name in ("machine", "process", "filament"):
        (tmp_path / f"{name}.json").write_text("{}")
    request = _request(tmp_path)
    staged = _copy_presets_to(request, tmp_path / "datadir")
    assert staged.machine == tmp_path / "datadir" / "presets" / "machine.json"
    assert staged.process.is_file()
    assert staged.filaments[0].is_file()
    assert staged.datadir == tmp_path / "datadir"
